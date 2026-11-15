import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

class Config:
    BASE_DIR = BASE_DIR
    
    # Storage Engine
    STORAGE_ENGINE = os.getenv("STORAGE_ENGINE", "json").lower()
    JSON_STORAGE_PATH = Path(os.getenv("JSON_STORAGE_PATH", "./data_store/storage.json"))
    
    # MySQL Database
    DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT = int(os.getenv("DB_PORT", "3306"))
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_NAME = os.getenv("DB_NAME", "priority_storage")
    DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "8"))
    
    # Security & IAM
    JWT_SECRET = os.getenv("JWT_SECRET", "priority_storage_default_secret_key_12345")
    JWT_EXPIRATION_HOURS = int(os.getenv("JWT_EXPIRATION_HOURS", "24"))
    ADMIN_DEFAULT_PASSWORD = os.getenv("ADMIN_DEFAULT_PASSWORD", "Admin@123")
    
    # Server
    FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1")
    FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
    
    # Serial & Hardware
    SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "9600"))
    ENABLE_VIRTUAL_HARDWARE = os.getenv("ENABLE_VIRTUAL_HARDWARE", "false").lower() in ("true", "1", "yes")
    VIRTUAL_INTERVAL_MS = int(os.getenv("VIRTUAL_INTERVAL_MS", "1200"))
    
    # Worker Thread Concurrency (OS Scheduling)
    MIN_WORKERS = max(1, int(os.getenv("MIN_WORKERS", "2")))
    MAX_WORKERS = max(MIN_WORKERS, int(os.getenv("MAX_WORKERS", "6")))
    SCALE_UP_QUEUE_DEPTH = max(1, int(os.getenv("SCALE_UP_QUEUE_DEPTH", "10")))
    SCALE_DOWN_QUEUE_DEPTH = max(0, int(os.getenv("SCALE_DOWN_QUEUE_DEPTH", "2")))
    
    # Task Processing & Retry
    MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "3"))
    SIMULATED_PROCESS_SECONDS = float(os.getenv("SIMULATED_PROCESS_SECONDS", "0.1"))

