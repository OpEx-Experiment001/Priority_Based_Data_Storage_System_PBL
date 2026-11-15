from backend.config import Config
from backend.storage.base import BaseStorage
from backend.storage.json_storage import JsonDocStorage

def create_storage_engine() -> BaseStorage:
    engine_type = Config.STORAGE_ENGINE.lower()
    if engine_type == "cpp":
        try:
            from backend.storage.cpp_engine_client import CppEngineClient
            client = CppEngineClient(port=int(Config.BASE_DIR.name and "8085"))
            return client
        except Exception as exc:
            print(f"[StorageFactory] Warning: C++ engine client failed ({exc}), falling back to direct JSON storage.")
            return JsonDocStorage()
    elif engine_type == "mysql":
        try:
            from backend.storage.mysql_storage import MySQLStorage
            storage = MySQLStorage()
            storage.ping()
            return storage
        except Exception as exc:
            print(f"[StorageFactory] Warning: Failed to connect to MySQL ({exc}). Falling back to JSON document store.")
            return JsonDocStorage()
    else:
        # Default: Pure JSON Document Store
        return JsonDocStorage()
