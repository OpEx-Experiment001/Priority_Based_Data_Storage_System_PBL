import os
import unittest
import tempfile
import time
from pathlib import Path

from backend.config import Config
from backend.auth.iam import (
    Role, Permission, create_jwt_token, verify_jwt_token, ROLE_PERMISSIONS
)
from backend.auth.user_manager import UserManager
from backend.storage.json_storage import JsonDocStorage
from backend.ingestion.parser import parse_telemetry_message
from backend.ingestion.classifier import PriorityClassifier
from backend.scheduler.priority_queue import OSPriorityQueue
from backend.scheduler.worker_pool import OSWorkerPool

class TestPrioritySystem(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_storage.json"
        self.user_db_path = Path(self.temp_dir.name) / "test_users.json"
        
        self.storage = JsonDocStorage(db_path=self.db_path)
        self.user_mgr = UserManager(db_path=self.user_db_path)
        self.classifier = PriorityClassifier(self.storage)
        self.queue = OSPriorityQueue()

    def tearDown(self):
        try:
            self.storage.close()
            self.user_mgr.close()
        except Exception:
            pass
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. IAM & RBAC Tests
    # -------------------------------------------------------------------------
    def test_iam_authentication_and_jwt(self):
        # Default admin seeded (first user is root SUPERADMIN)
        auth = self.user_mgr.authenticate("admin", Config.ADMIN_DEFAULT_PASSWORD)
        self.assertIsNotNone(auth)
        self.assertIn(auth["role"], (Role.SUPERADMIN, Role.ADMIN))

        # JWT creation & verification
        token = create_jwt_token("admin", Role.ADMIN)
        payload = verify_jwt_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["sub"], "admin")
        self.assertEqual(payload["role"], Role.ADMIN)
        self.assertIn(Permission.RECORDS_PURGE_ALL, payload["permissions"])

        # Operator role permissions
        token_op = create_jwt_token("operator", Role.OPERATOR)
        payload_op = verify_jwt_token(token_op)
        self.assertNotIn(Permission.RECORDS_PURGE_ALL, payload_op["permissions"])
        self.assertIn(Permission.RECORDS_CREATE, payload_op["permissions"])

    def test_user_crud(self):
        # Create user
        new_user = self.user_mgr.create_user("sensor_agent", "Agent@Pass123", Role.OPERATOR)
        self.assertEqual(new_user["username"], "sensor_agent")

        # Authenticate
        auth = self.user_mgr.authenticate("sensor_agent", "Agent@Pass123")
        self.assertIsNotNone(auth)

        # List users
        users = self.user_mgr.list_users()
        usernames = [u["username"] for u in users]
        self.assertIn("sensor_agent", usernames)

        # Delete user
        self.user_mgr.delete_user("sensor_agent")
        self.assertIsNone(self.user_mgr.get_user("sensor_agent"))

    # -------------------------------------------------------------------------
    # 2. Ingestion & Dynamic Rule Tests
    # -------------------------------------------------------------------------
    def test_telemetry_parser(self):
        # Test brace-delimited Arduino format
        raw = "{101,24.5,65.2,850,1012.3}"
        parsed = parse_telemetry_message(raw, source_port="COM3")
        self.assertEqual(parsed["module_id"], 101)
        self.assertEqual(len(parsed["values"]), 4)
        self.assertEqual(parsed["values"][2], 850.0)

        # Test JSON format
        raw_json = '{"module_id": 102, "values": [85.0, 12.4, -5.0, 3.9]}'
        parsed_json = parse_telemetry_message(raw_json, source_port="VIRTUAL")
        self.assertEqual(parsed_json["module_id"], 102)
        self.assertEqual(parsed_json["values"][0], 85.0)

    def test_dynamic_priority_classifier(self):
        # Gas reading 850 >= 800 threshold should be classified as HIGH
        p_high = self.classifier.classify(101, [25.0, 50.0, 850.0, 1000.0])
        self.assertEqual(p_high, "HIGH")

        # Gas reading 650 >= 600 threshold should be classified as MEDIUM
        p_med = self.classifier.classify(101, [25.0, 50.0, 650.0, 1000.0])
        self.assertEqual(p_med, "MEDIUM")

        # Normal telemetry below thresholds should be LOW
        p_low = self.classifier.classify(101, [25.0, 50.0, 400.0, 1000.0])
        self.assertEqual(p_low, "LOW")

    # -------------------------------------------------------------------------
    # 3. NoSQL JSON Storage CRUD (Single, Batch, Whole)
    # -------------------------------------------------------------------------
    def test_json_storage_crud(self):
        # Create single record
        rec = {
            "module_id": 101,
            "values": [25.0, 60.0, 850.0, 1013.0],
            "priority": "HIGH",
            "source_port": "VIRTUAL"
        }
        data_id = self.storage.persist_record(rec)
        self.assertIsNotNone(data_id)

        # Read single
        fetched = self.storage.get_record(data_id)
        self.assertEqual(fetched["module_id"], 101)
        self.assertEqual(fetched["status"], "PENDING")

        # Update status
        self.storage.update_record_status(data_id, "PROCESSING", attempt_count=1)
        updated = self.storage.get_record(data_id)
        self.assertEqual(updated["status"], "PROCESSING")
        self.assertEqual(updated["attempt_count"], 1)

        # Batch operations
        rec2 = {"module_id": 102, "values": [10.0, 20.0], "priority": "LOW"}
        id2 = self.storage.persist_record(rec2)
        
        batch_ids = [data_id, id2]
        self.storage.batch_update_priority(batch_ids, "MEDIUM")
        self.assertEqual(self.storage.get_record(data_id)["priority"], "MEDIUM")
        self.assertEqual(self.storage.get_record(id2)["priority"], "MEDIUM")

        # Batch delete
        deleted_count = self.storage.batch_delete_records([data_id])
        self.assertEqual(deleted_count, 1)
        self.assertIsNone(self.storage.get_record(data_id))

        # Whole Purge
        self.storage.purge_records()
        self.assertEqual(self.storage.count_records(), 0)

    # -------------------------------------------------------------------------
    # 4. OS Priority Queue & Concurrency Tests
    # -------------------------------------------------------------------------
    def test_priority_queue_ordering_and_tie_breaking(self):
        # Push LOW, then HIGH, then another HIGH
        rec_low = {"data_id": "1", "priority": "LOW", "tag": "first_low"}
        rec_high_1 = {"data_id": "2", "priority": "HIGH", "tag": "first_high"}
        rec_high_2 = {"data_id": "3", "priority": "HIGH", "tag": "second_high"}
        rec_med = {"data_id": "4", "priority": "MEDIUM", "tag": "first_med"}

        self.queue.put(rec_low)
        self.queue.put(rec_high_1)
        self.queue.put(rec_high_2)
        self.queue.put(rec_med)

        # Pop 1: Must be first_high (HIGH rank 0, seq 2)
        _, _, popped_1 = self.queue.get(timeout=1.0)
        self.assertEqual(popped_1["tag"], "first_high")

        # Pop 2: Must be second_high (HIGH rank 0, seq 3)
        _, _, popped_2 = self.queue.get(timeout=1.0)
        self.assertEqual(popped_2["tag"], "second_high")

        # Pop 3: Must be first_med (MEDIUM rank 1)
        _, _, popped_3 = self.queue.get(timeout=1.0)
        self.assertEqual(popped_3["tag"], "first_med")

        # Pop 4: Must be first_low (LOW rank 2)
        _, _, popped_4 = self.queue.get(timeout=1.0)
        self.assertEqual(popped_4["tag"], "first_low")

    def test_worker_pool_execution(self):
        # Create worker pool with simulated fast processing
        events = []
        def listener(ev, data):
            events.append((ev, data))

        pool = OSWorkerPool(storage=self.storage, queue=self.queue, on_event=listener)
        pool.min_workers = 2
        pool.start()

        # Enqueue a record
        rec = {
            "module_id": 101,
            "values": [28.0, 70.0, 850.0, 1010.0],
            "priority": "HIGH"
        }
        data_id = self.storage.persist_record(rec)
        self.queue.put(rec)

        # Allow worker thread to process
        time.sleep(0.5)
        pool.stop()

        final_rec = self.storage.get_record(data_id)
        self.assertEqual(final_rec["status"], "COMPLETED")
        self.assertEqual(final_rec["attempt_count"], 1)

if __name__ == "__main__":
    unittest.main()

