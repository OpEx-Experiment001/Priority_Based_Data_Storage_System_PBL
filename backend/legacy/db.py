import json
import os
import threading
from datetime import datetime
import mysql.connector
from mysql.connector import pooling

class Database:
    def __init__(self):
        self._lock = threading.Lock()
        self.pool = pooling.MySQLConnectionPool(
            pool_name="priority_storage_pool",
            pool_size=max(2, int(os.getenv("DB_POOL_SIZE", "8"))),
            host=os.getenv("DB_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT", "3306")),
            user=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "priority_storage"),
            autocommit=False,
        )
        self.initialize()

    def connection(self):
        return self.pool.get_connection()

    def initialize(self):
        conn = self.connection()
        try:
            cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS modules (
                module_id INT PRIMARY KEY, module_name VARCHAR(100) NOT NULL,
                description VARCHAR(500), is_enabled BOOLEAN NOT NULL DEFAULT TRUE)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS data_records (
                data_id BIGINT AUTO_INCREMENT PRIMARY KEY, module_id INT NOT NULL,
                source_port VARCHAR(64), raw_message TEXT, received_at DATETIME(6) NOT NULL,
                payload_json JSON NOT NULL, priority ENUM('HIGH','MEDIUM','LOW') NOT NULL,
                FOREIGN KEY (module_id) REFERENCES modules(module_id),
                INDEX idx_records_received (received_at), INDEX idx_records_priority (priority))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS readings (
                reading_id BIGINT AUTO_INCREMENT PRIMARY KEY, data_id BIGINT NOT NULL,
                reading_index INT NOT NULL, reading_value DOUBLE NOT NULL,
                FOREIGN KEY (data_id) REFERENCES data_records(data_id) ON DELETE CASCADE,
                UNIQUE KEY uq_record_reading (data_id, reading_index))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS processes (
                process_id BIGINT AUTO_INCREMENT PRIMARY KEY, data_id BIGINT NOT NULL,
                status ENUM('PENDING','QUEUED','PROCESSING','RETRY_WAIT','COMPLETED','FAILED') NOT NULL DEFAULT 'PENDING',
                attempt_count INT NOT NULL DEFAULT 0, max_attempts INT NOT NULL DEFAULT 3,
                last_error TEXT, submitted_at DATETIME(6) NOT NULL,
                started_at DATETIME(6), completed_at DATETIME(6), next_retry_at DATETIME(6),
                FOREIGN KEY (data_id) REFERENCES data_records(data_id) ON DELETE CASCADE,
                UNIQUE KEY uq_process_data (data_id), INDEX idx_process_status (status))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS processing_attempts (
                attempt_id BIGINT AUTO_INCREMENT PRIMARY KEY, data_id BIGINT NOT NULL,
                attempt_number INT NOT NULL, outcome VARCHAR(30) NOT NULL,
                error_message TEXT, started_at DATETIME(6) NOT NULL, ended_at DATETIME(6),
                FOREIGN KEY (data_id) REFERENCES data_records(data_id) ON DELETE CASCADE)""")
            for mid, name, desc in [
                (101, "Environmental Module", "Illustrative temperature, humidity, gas, pressure readings"),
                (102, "Motion Module", "Illustrative motion, distance, tilt, battery readings")]:
                cur.execute("INSERT IGNORE INTO modules(module_id,module_name,description) VALUES(%s,%s,%s)", (mid,name,desc))
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _dt(value):
        if isinstance(value, datetime): return value
        return datetime.now()

    def persist_record(self, record):
        conn = self.connection()
        try:
            cur = conn.cursor()
            cur.execute("""INSERT INTO modules(module_id,module_name,description)
                VALUES(%s,%s,%s) ON DUPLICATE KEY UPDATE module_id=VALUES(module_id)""",
                (record["module_id"], f"Module {record['module_id']}", "Registered automatically from an incoming record"))
            received = datetime.now()
            cur.execute("""INSERT INTO data_records(module_id,source_port,raw_message,received_at,payload_json,priority)
                VALUES(%s,%s,%s,%s,%s,%s)""",
                (record["module_id"], record.get("source_port"), record.get("raw_message"),
                 received, json.dumps(record["values"]), record["priority"]))
            data_id = cur.lastrowid
            for i, value in enumerate(record["values"]):
                cur.execute("INSERT INTO readings(data_id,reading_index,reading_value) VALUES(%s,%s,%s)", (data_id,i,value))
            cur.execute("INSERT INTO processes(data_id,status,submitted_at) VALUES(%s,'PENDING',%s)", (data_id,received))
            conn.commit()
            record["data_id"] = data_id
            return data_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def mark_status(self, data_id, status, attempt_count=None, error=None, started=False, completed=False, retry_at=None):
        conn = self.connection()
        try:
            cur = conn.cursor()
            updates = ["status=%s", "last_error=%s"]
            vals = [status, error]
            if attempt_count is not None: updates.append("attempt_count=%s"); vals.append(attempt_count)
            if started: updates.append("started_at=NOW(6)")
            if completed: updates.append("completed_at=NOW(6)")
            if retry_at is not None: updates.append("next_retry_at=%s"); vals.append(retry_at)
            vals.append(data_id)
            cur.execute("UPDATE processes SET " + ",".join(updates) + " WHERE data_id=%s", vals)
            conn.commit()
        finally: conn.close()

    def log_attempt(self, data_id, number, outcome, error, started_at, ended_at):
        conn = self.connection()
        try:
            cur=conn.cursor()
            cur.execute("""INSERT INTO processing_attempts(data_id,attempt_number,outcome,error_message,started_at,ended_at)
                           VALUES(%s,%s,%s,%s,%s,%s)""", (data_id,number,outcome,error,started_at,ended_at))
            conn.commit()
        finally: conn.close()

    def recent_records(self, limit=50):
        conn=self.connection()
        try:
            cur=conn.cursor(dictionary=True)
            cur.execute("""SELECT d.data_id,d.module_id,d.source_port,d.received_at,d.payload_json,d.priority,
                           p.status,p.attempt_count,p.last_error
                           FROM data_records d JOIN processes p ON p.data_id=d.data_id
                           ORDER BY d.data_id DESC LIMIT %s""",(limit,))
            rows=cur.fetchall()
            for r in rows:
                r["received_at"] = r["received_at"].isoformat(sep=" ", timespec="seconds") if r["received_at"] else None
                if isinstance(r["payload_json"], str):
                    try: r["values"] = json.loads(r["payload_json"])
                    except Exception: r["values"] = []
                else: r["values"] = r["payload_json"]
                del r["payload_json"]
            return rows
        finally: conn.close()

    def count_records(self):
        conn=self.connection()
        try:
            cur=conn.cursor(); cur.execute("SELECT COUNT(*) FROM data_records"); return cur.fetchone()[0]
        finally: conn.close()

    def priority_counts(self):
        conn=self.connection()
        try:
            cur=conn.cursor(dictionary=True)
            cur.execute("SELECT priority,COUNT(*) AS count FROM data_records GROUP BY priority")
            return {r["priority"]:r["count"] for r in cur.fetchall()}
        finally: conn.close()


    def get_attempt_count(self, data_id):
        conn=self.connection()
        try:
            cur=conn.cursor()
            cur.execute("SELECT attempt_count FROM processes WHERE data_id=%s", (data_id,))
            row=cur.fetchone()
            return int(row[0]) if row else 0
        finally: conn.close()

    def fetch_pending_records(self, limit=100):
        conn=self.connection()
        try:
            cur=conn.cursor(dictionary=True)
            cur.execute("""SELECT d.data_id,d.module_id,d.source_port,d.raw_message,d.priority,d.payload_json
                FROM data_records d JOIN processes p ON p.data_id=d.data_id
                WHERE p.status IN ('PENDING','RETRY_WAIT')
                  AND (p.next_retry_at IS NULL OR p.next_retry_at <= NOW(6))
                ORDER BY CASE d.priority WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END,
                         d.data_id ASC LIMIT %s""", (limit,))
            rows=cur.fetchall()
            result=[]
            for r in rows:
                vals=r["payload_json"]
                if isinstance(vals,str):
                    try: vals=json.loads(vals)
                    except Exception: vals=[]
                result.append({"data_id":r["data_id"],"module_id":r["module_id"],"source_port":r["source_port"],
                    "raw_message":r["raw_message"],"priority":r["priority"],"values":vals})
            return result
        finally: conn.close()

    def ping(self):
        conn=self.connection()
        try:
            cur=conn.cursor(); cur.execute("SELECT 1"); cur.fetchone()
        finally: conn.close()
