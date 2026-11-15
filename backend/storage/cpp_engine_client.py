import json
import socket
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional
from backend.storage.base import BaseStorage
from backend.config import Config

class CppEngineClient(BaseStorage):
    """
    Microservice Client communicating with the native C++ Priority Telemetry Engine
    over TCP sockets. Implements BaseStorage for loose coupling.
    """

    def __init__(self, host="127.0.0.1", port=8085, auto_spawn=True):
        self.host = host
        self.port = port
        self.auto_spawn = auto_spawn
        self._lock = threading.RLock()
        self._process = None
        self._ensure_server_running()

    def _ensure_server_running(self):
        if not self.ping():
            if self.auto_spawn:
                exe_path = Config.BASE_DIR / "cpp_backend" / "bin" / "priority_engine.exe"
                if exe_path.exists():
                    db_path = Config.JSON_STORAGE_PATH
                    print(f"[CppEngineClient] Spawning C++ Priority Engine ({exe_path}) on port {self.port}...")
                    self._process = subprocess.Popen(
                        [str(exe_path), "--port", str(self.port), "--db", str(db_path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    # Wait briefly for server startup
                    for _ in range(15):
                        time.sleep(0.2)
                        if self.ping():
                            print("[CppEngineClient] Connected to C++ Priority Engine.")
                            return
            print("[CppEngineClient] Warning: C++ Priority Engine server is not yet reachable.")

    def _send_command(self, cmd_dict: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3.0)
            try:
                s.connect((self.host, self.port))
                req_line = json.dumps(cmd_dict) + "\n"
                s.sendall(req_line.encode("utf-8"))

                file_obj = s.makefile("r", encoding="utf-8")
                while True:
                    resp_line = file_obj.readline()
                    if not resp_line:
                        s.close()
                        return {"status": "EMPTY_RESPONSE"}
                    parsed = json.loads(resp_line.strip())
                    if "event" not in parsed:
                        s.close()
                        return parsed
            except Exception as exc:
                try: s.close()
                except Exception: pass
                return {"status": "ERROR", "error": str(exc)}

    def persist_record(self, record: Dict[str, Any]) -> str:
        res = self._send_command({"action": "INGEST", "record": record})
        data_id = str(res.get("data_id", ""))
        record["data_id"] = data_id
        return data_id

    def get_record(self, data_id: str) -> Optional[Dict[str, Any]]:
        res = self._send_command({"action": "GET_RECORD", "data_id": str(data_id)})
        return res.get("record")

    def list_records(self, limit: int = 50, offset: int = 0, status: Optional[str] = None, priority: Optional[str] = None) -> List[Dict[str, Any]]:
        res = self._send_command({
            "action": "GET_RECORDS",
            "limit": limit,
            "offset": offset,
            "status": status or "",
            "priority": priority or ""
        })
        return res.get("records", [])

    def count_records(self, status: Optional[str] = None, priority: Optional[str] = None) -> int:
        res = self._send_command({
            "action": "GET_RECORDS",
            "limit": 1,
            "offset": 0,
            "status": status or "",
            "priority": priority or ""
        })
        return res.get("total", 0)

    def update_record_status(self, data_id: str, status: str, attempt_count: Optional[int] = None,
                             error: Optional[str] = None, started: bool = False,
                             completed: bool = False, retry_at: Optional[str] = None) -> bool:
        # Status transitions are handled inside C++ worker pool, but also exposed:
        res = self._send_command({
            "action": "UPDATE_RECORD",
            "data_id": str(data_id),
            "updates": {"status": status}
        })
        return res.get("status") == "OK"

    def update_record(self, data_id: str, update_dict: Dict[str, Any]) -> bool:
        res = self._send_command({
            "action": "UPDATE_RECORD",
            "data_id": str(data_id),
            "updates": update_dict
        })
        return res.get("status") == "OK"

    def delete_record(self, data_id: str) -> bool:
        res = self._send_command({"action": "DELETE_RECORD", "data_id": str(data_id)})
        return res.get("status") == "DELETED"

    def batch_delete_records(self, data_ids: List[str]) -> int:
        res = self._send_command({"action": "BATCH_DELETE", "data_ids": data_ids})
        return res.get("deleted_count", 0)

    def batch_update_priority(self, data_ids: List[str], new_priority: str) -> int:
        res = self._send_command({"action": "BATCH_PRIORITY", "data_ids": data_ids, "priority": new_priority})
        return res.get("updated_count", 0)

    def purge_records(self, status: Optional[str] = None) -> int:
        res = self._send_command({"action": "PURGE_RECORDS", "status": status or ""})
        return res.get("purged_count", 0)

    def fetch_pending_records(self, limit: int = 100) -> List[Dict[str, Any]]:
        # The C++ engine handles its own priority queue and reconciliation internally
        return []

    def log_attempt(self, data_id: str, attempt_number: int, outcome: str,
                    error_message: Optional[str], started_at: str, ended_at: str):
        pass

    def get_priority_counts(self) -> Dict[str, int]:
        res = self._send_command({"action": "GET_STATUS"})
        return res.get("priority_counts", {"HIGH": 0, "MEDIUM": 0, "LOW": 0})

    def get_rules(self) -> List[Dict[str, Any]]:
        res = self._send_command({"action": "GET_RULES"})
        return res.get("rules", [])

    def save_rule(self, rule_dict: Dict[str, Any]) -> str:
        res = self._send_command({"action": "SAVE_RULE", "rule": rule_dict})
        return res.get("rule_id", "")

    def delete_rule(self, rule_id: str) -> bool:
        res = self._send_command({"action": "DELETE_RULE", "rule_id": str(rule_id)})
        return res.get("status") == "DELETED"

    def ping(self) -> bool:
        res = self._send_command({"action": "PING"})
        return res.get("status") == "PONG"

    def get_status(self) -> Dict[str, Any]:
        return self._send_command({"action": "GET_STATUS"})
