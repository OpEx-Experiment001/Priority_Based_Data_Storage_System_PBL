import json
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional
import mysql.connector
from mysql.connector import pooling
from backend.storage.base import BaseStorage
from backend.config import Config

class MySQLStorage(BaseStorage):
    """
    Relational MySQL implementation of BaseStorage.
    Uses connection pooling and ACID transactions.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.pool = pooling.MySQLConnectionPool(
            pool_name="priority_mysql_pool",
            pool_size=max(2, Config.DB_POOL_SIZE),
            host=Config.DB_HOST,
            port=Config.DB_PORT,
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
            database=Config.DB_NAME,
            autocommit=False
        )
        self._initialize_schema()

    def _connection(self):
        return self.pool.get_connection()

    def _initialize_schema(self):
        conn = self._connection()
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
            cur.execute("""CREATE TABLE IF NOT EXISTS priority_rules (
                rule_id VARCHAR(64) PRIMARY KEY, module_id INT NOT NULL,
                sensor_index INT NOT NULL, sensor_name VARCHAR(64),
                operator VARCHAR(4) NOT NULL, threshold DOUBLE NOT NULL,
                priority ENUM('HIGH','MEDIUM','LOW') NOT NULL, description VARCHAR(255))""")
            conn.commit()
        finally:
            conn.close()

    def persist_record(self, record: Dict[str, Any]) -> str:
        conn = self._connection()
        try:
            cur = conn.cursor()
            cur.execute("""INSERT INTO modules(module_id,module_name,description)
                VALUES(%s,%s,%s) ON DUPLICATE KEY UPDATE module_id=VALUES(module_id)""",
                (record["module_id"], f"Module {record['module_id']}", "Auto-registered module"))
            
            now = datetime.now()
            cur.execute("""INSERT INTO data_records(module_id,source_port,raw_message,received_at,payload_json,priority)
                VALUES(%s,%s,%s,%s,%s,%s)""",
                (record["module_id"], record.get("source_port"), record.get("raw_message"),
                 now, json.dumps(record.get("values", [])), record.get("priority", "LOW")))
            data_id = cur.lastrowid
            cur.execute("INSERT INTO processes(data_id,status,submitted_at) VALUES(%s,'PENDING',%s)", (data_id, now))
            conn.commit()
            data_id_str = str(data_id)
            record["data_id"] = data_id_str
            return data_id_str
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_record(self, data_id: str) -> Optional[Dict[str, Any]]:
        conn = self._connection()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("""SELECT d.data_id, d.module_id, d.source_port, d.received_at, d.payload_json,
                                  d.priority, p.status, p.attempt_count, p.last_error
                           FROM data_records d JOIN processes p ON p.data_id=d.data_id
                           WHERE d.data_id=%s""", (data_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._format_row(row)
        finally:
            conn.close()

    def list_records(self, limit: int = 50, offset: int = 0, status: Optional[str] = None, priority: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self._connection()
        try:
            cur = conn.cursor(dictionary=True)
            query = """SELECT d.data_id, d.module_id, d.source_port, d.received_at, d.payload_json,
                              d.priority, p.status, p.attempt_count, p.last_error
                       FROM data_records d JOIN processes p ON p.data_id=d.data_id"""
            params: List[Any] = []
            clauses = []
            if status:
                clauses.append("p.status=%s")
                params.append(status)
            if priority:
                clauses.append("d.priority=%s")
                params.append(priority)
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY d.data_id DESC LIMIT %s OFFSET %s"
            params.extend([limit, offset])
            cur.execute(query, tuple(params))
            return [self._format_row(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def count_records(self, status: Optional[str] = None, priority: Optional[str] = None) -> int:
        conn = self._connection()
        try:
            cur = conn.cursor()
            query = "SELECT COUNT(*) FROM data_records d JOIN processes p ON p.data_id=d.data_id"
            params: List[Any] = []
            clauses = []
            if status:
                clauses.append("p.status=%s")
                params.append(status)
            if priority:
                clauses.append("d.priority=%s")
                params.append(priority)
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            cur.execute(query, tuple(params))
            return cur.fetchone()[0]
        finally:
            conn.close()

    def update_record_status(self, data_id: str, status: str, attempt_count: Optional[int] = None,
                             error: Optional[str] = None, started: bool = False,
                             completed: bool = False, retry_at: Optional[str] = None) -> bool:
        conn = self._connection()
        try:
            cur = conn.cursor()
            updates = ["status=%s", "last_error=%s"]
            vals: List[Any] = [status, error]
            if attempt_count is not None:
                updates.append("attempt_count=%s")
                vals.append(attempt_count)
            if started:
                updates.append("started_at=NOW(6)")
            if completed:
                updates.append("completed_at=NOW(6)")
            if retry_at:
                updates.append("next_retry_at=%s")
                vals.append(retry_at)
            vals.append(data_id)
            cur.execute("UPDATE processes SET " + ",".join(updates) + " WHERE data_id=%s", tuple(vals))
            conn.commit()
            return True
        finally:
            conn.close()

    def update_record(self, data_id: str, update_dict: Dict[str, Any]) -> bool:
        conn = self._connection()
        try:
            cur = conn.cursor()
            if "priority" in update_dict:
                cur.execute("UPDATE data_records SET priority=%s WHERE data_id=%s", (update_dict["priority"], data_id))
            if "values" in update_dict:
                cur.execute("UPDATE data_records SET payload_json=%s WHERE data_id=%s", (json.dumps(update_dict["values"]), data_id))
            conn.commit()
            return True
        finally:
            conn.close()

    def delete_record(self, data_id: str) -> bool:
        conn = self._connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM data_records WHERE data_id=%s", (data_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def batch_delete_records(self, data_ids: List[str]) -> int:
        if not data_ids:
            return 0
        conn = self._connection()
        try:
            cur = conn.cursor()
            format_strings = ",".join(["%s"] * len(data_ids))
            cur.execute(f"DELETE FROM data_records WHERE data_id IN ({format_strings})", tuple(data_ids))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def batch_update_priority(self, data_ids: List[str], new_priority: str) -> int:
        if not data_ids:
            return 0
        conn = self._connection()
        try:
            cur = conn.cursor()
            format_strings = ",".join(["%s"] * len(data_ids))
            cur.execute(f"UPDATE data_records SET priority=%s WHERE data_id IN ({format_strings})", (new_priority, *data_ids))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def purge_records(self, status: Optional[str] = None) -> int:
        conn = self._connection()
        try:
            cur = conn.cursor()
            if status:
                cur.execute("DELETE d FROM data_records d JOIN processes p ON p.data_id=d.data_id WHERE p.status=%s", (status,))
            else:
                cur.execute("DELETE FROM data_records")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def fetch_pending_records(self, limit: int = 100) -> List[Dict[str, Any]]:
        conn = self._connection()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("""SELECT d.data_id, d.module_id, d.source_port, d.raw_message, d.priority, d.payload_json
                           FROM data_records d JOIN processes p ON p.data_id=d.data_id
                           WHERE p.status IN ('PENDING','RETRY_WAIT')
                             AND (p.next_retry_at IS NULL OR p.next_retry_at <= NOW(6))
                           ORDER BY CASE d.priority WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END,
                                    d.data_id ASC LIMIT %s""", (limit,))
            rows = cur.fetchall()
            return [self._format_row(r) for r in rows]
        finally:
            conn.close()

    def log_attempt(self, data_id: str, attempt_number: int, outcome: str,
                    error_message: Optional[str], started_at: str, ended_at: str):
        conn = self._connection()
        try:
            cur = conn.cursor()
            cur.execute("""INSERT INTO processing_attempts(data_id,attempt_number,outcome,error_message,started_at,ended_at)
                           VALUES(%s,%s,%s,%s,%s,%s)""",
                        (data_id, attempt_number, outcome, error_message, started_at, ended_at))
            conn.commit()
        finally:
            conn.close()

    def get_priority_counts(self) -> Dict[str, int]:
        conn = self._connection()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT priority, COUNT(*) AS count FROM data_records GROUP BY priority")
            counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
            for r in cur.fetchall():
                counts[r["priority"]] = r["count"]
            return counts
        finally:
            conn.close()

    def get_rules(self) -> List[Dict[str, Any]]:
        conn = self._connection()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT * FROM priority_rules")
            return cur.fetchall()
        finally:
            conn.close()

    def save_rule(self, rule_dict: Dict[str, Any]) -> str:
        conn = self._connection()
        try:
            cur = conn.cursor()
            rule_id = rule_dict.get("rule_id") or f"rule-{int(datetime.now().timestamp())}"
            cur.execute("""INSERT INTO priority_rules(rule_id,module_id,sensor_index,sensor_name,operator,threshold,priority,description)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
                           ON DUPLICATE KEY UPDATE module_id=VALUES(module_id), sensor_index=VALUES(sensor_index),
                                                   sensor_name=VALUES(sensor_name), operator=VALUES(operator),
                                                   threshold=VALUES(threshold), priority=VALUES(priority),
                                                   description=VALUES(description)""",
                        (rule_id, rule_dict["module_id"], rule_dict["sensor_index"], rule_dict.get("sensor_name"),
                         rule_dict["operator"], rule_dict["threshold"], rule_dict["priority"], rule_dict.get("description")))
            conn.commit()
            return rule_id
        finally:
            conn.close()

    def delete_rule(self, rule_id: str) -> bool:
        conn = self._connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM priority_rules WHERE rule_id=%s", (rule_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def ping(self) -> bool:
        conn = self._connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            return cur.fetchone() is not None
        finally:
            conn.close()

    def _format_row(self, r: dict) -> dict:
        r["data_id"] = str(r["data_id"])
        if "received_at" in r and r["received_at"]:
            r["received_at"] = r["received_at"].isoformat()
        if "payload_json" in r:
            if isinstance(r["payload_json"], str):
                try: r["values"] = json.loads(r["payload_json"])
                except Exception: r["values"] = []
            else:
                r["values"] = r["payload_json"]
            del r["payload_json"]
        return r

