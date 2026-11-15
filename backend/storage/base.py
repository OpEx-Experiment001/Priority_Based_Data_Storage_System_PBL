from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

class BaseStorage(ABC):
    """
    Abstract interface for loosely-coupled telemetry and queue state storage.
    Enables swapping between NoSQL (TinyDB/MongoDB) and SQL (MySQL/Postgres)
    without modifying the OS scheduler or ingestion logic.
    """

    @abstractmethod
    def persist_record(self, record: Dict[str, Any]) -> str:
        """Saves an incoming record and initializes its process state to 'PENDING'."""
        pass

    @abstractmethod
    def get_record(self, data_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single record by its ID."""
        pass

    @abstractmethod
    def list_records(self, limit: int = 50, offset: int = 0, status: Optional[str] = None, priority: Optional[str] = None) -> List[Dict[str, Any]]:
        """Queries records with optional filtering and pagination."""
        pass

    @abstractmethod
    def count_records(self, status: Optional[str] = None, priority: Optional[str] = None) -> int:
        """Counts total records matching the filter."""
        pass

    @abstractmethod
    def update_record_status(self, data_id: str, status: str, attempt_count: Optional[int] = None,
                             error: Optional[str] = None, started: bool = False,
                             completed: bool = False, retry_at: Optional[str] = None) -> bool:
        """Transitions record process state (PENDING, QUEUED, PROCESSING, RETRY_WAIT, COMPLETED, FAILED)."""
        pass

    @abstractmethod
    def update_record(self, data_id: str, update_dict: Dict[str, Any]) -> bool:
        """Updates record metadata (e.g. priority or values) - Admin Single CRUD."""
        pass

    @abstractmethod
    def delete_record(self, data_id: str) -> bool:
        """Deletes a single record by its ID."""
        pass

    @abstractmethod
    def batch_delete_records(self, data_ids: List[str]) -> int:
        """Deletes multiple records in a single batch operation."""
        pass

    @abstractmethod
    def batch_update_priority(self, data_ids: List[str], new_priority: str) -> int:
        """Updates priority for a batch of records."""
        pass

    @abstractmethod
    def purge_records(self, status: Optional[str] = None) -> int:
        """Purges all records, or records matching a given status (e.g. COMPLETED)."""
        pass

    @abstractmethod
    def fetch_pending_records(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetches records in PENDING or RETRY_WAIT status for crash reconciliation."""
        pass

    @abstractmethod
    def log_attempt(self, data_id: str, attempt_number: int, outcome: str,
                    error_message: Optional[str], started_at: str, ended_at: str):
        """Appends an execution attempt to the historical audit log."""
        pass

    @abstractmethod
    def get_priority_counts(self) -> Dict[str, int]:
        """Returns distribution counts across HIGH, MEDIUM, LOW."""
        pass

    @abstractmethod
    def get_rules(self) -> List[Dict[str, Any]]:
        """Returns dynamic priority classification rules."""
        pass

    @abstractmethod
    def save_rule(self, rule_dict: Dict[str, Any]) -> str:
        """Creates or updates a dynamic priority rule."""
        pass

    @abstractmethod
    def delete_rule(self, rule_id: str) -> bool:
        """Deletes a dynamic priority rule."""
        pass

    @abstractmethod
    def ping(self) -> bool:
        """Health check for database connectivity."""
        pass

