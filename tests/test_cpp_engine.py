import json
import socket
import subprocess
import time
import unittest
from pathlib import Path

class TestCppPriorityEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = 8999
        cls.db_path = Path("./data_store/test_cpp_engine_db.json")
        if cls.db_path.exists():
            cls.db_path.unlink()
        
        exe_path = Path("./cpp_backend/bin/priority_engine.exe")
        cls.proc = subprocess.Popen(
            [str(exe_path), "--port", str(cls.port), "--db", str(cls.db_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=2.0)
        except Exception:
            cls.proc.kill()
        if cls.db_path.exists():
            try: cls.db_path.unlink()
            except Exception: pass

    def send_cmd(self, cmd_dict):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3.0)
        s.connect(("127.0.0.1", self.port))
        line = json.dumps(cmd_dict) + "\n"
        s.sendall(line.encode("utf-8"))
        
        file_obj = s.makefile("r", encoding="utf-8")
        while True:
            resp_line = file_obj.readline()
            if not resp_line:
                break
            parsed = json.loads(resp_line.strip())
            if "event" not in parsed:
                s.close()
                return parsed
        s.close()
        return {}

    def test_01_ping(self):
        res = self.send_cmd({"action": "PING"})
        self.assertEqual(res.get("status"), "PONG")

    def test_02_ingest_and_worker_processing(self):
        # High gas value 920.0 should be classified as HIGH by C++ engine
        res = self.send_cmd({
            "action": "INGEST",
            "record": {
                "module_id": 101,
                "values": [26.4, 55.0, 920.0, 1012.0],
                "source_port": "TEST_COM"
            }
        })
        self.assertEqual(res.get("status"), "INGESTED")
        self.assertEqual(res.get("priority"), "HIGH")
        data_id = res.get("data_id")
        self.assertIsNotNone(data_id)

        # Wait for C++ worker thread to pop from priority queue and complete processing
        time.sleep(0.5)

        # Query record back from JSON document store
        rec_res = self.send_cmd({"action": "GET_RECORD", "data_id": data_id})
        self.assertEqual(rec_res.get("status"), "OK")
        rec = rec_res["record"]
        self.assertEqual(rec["status"], "COMPLETED")
        self.assertEqual(rec["priority"], "HIGH")
        self.assertEqual(rec["attempt_count"], 1)

    def test_03_crud_operations(self):
        # Ingest record to test CRUD
        res = self.send_cmd({
            "action": "INGEST",
            "record": {"module_id": 102, "values": [12.0, 30.0], "priority": "LOW"}
        })
        data_id = res["data_id"]

        # Batch Priority update
        batch_prio = self.send_cmd({
            "action": "BATCH_PRIORITY",
            "data_ids": [data_id],
            "priority": "MEDIUM"
        })
        self.assertEqual(batch_prio.get("updated_count"), 1)

        # Delete single
        del_res = self.send_cmd({"action": "DELETE_RECORD", "data_id": data_id})
        self.assertEqual(del_res.get("status"), "DELETED")

if __name__ == "__main__":
    unittest.main()
