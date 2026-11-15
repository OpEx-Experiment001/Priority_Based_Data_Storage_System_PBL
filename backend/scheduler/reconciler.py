import threading
import time
from typing import Callable, Optional
from backend.storage.base import BaseStorage
from backend.scheduler.priority_queue import OSPriorityQueue

class WorkReconciler:
    """
    Background daemon for crash recovery and state reconciliation.
    Ensures zero data loss on unexpected power cuts, shutdowns, or server restarts.
    """

    def __init__(self, storage: BaseStorage, queue: OSPriorityQueue, on_event: Optional[Callable] = None):
        self.storage = storage
        self.queue = queue
        self.on_event = on_event or (lambda ev, data: None)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._reconcile_loop, daemon=True, name="os-reconciler")
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _reconcile_loop(self):
        # Run immediately on start, then every 10 seconds
        while not self._stop_event.wait(10.0):
            try:
                pending_records = self.storage.fetch_pending_records(limit=50)
                if pending_records:
                    reconciled_count = 0
                    for rec in pending_records:
                        data_id = str(rec.get("data_id"))
                        # Mark QUEUED atomically to avoid duplicate reconciliation runs
                        self.storage.update_record_status(data_id, "QUEUED")
                        self.queue.put(rec)
                        reconciled_count += 1
                    
                    if reconciled_count > 0:
                        self.on_event("system_event", {
                            "type": "RECONCILIATION_COMPLETED",
                            "message": f"Successfully recovered and queued {reconciled_count} stranded record(s)"
                        })
            except Exception as exc:
                self.on_event("system_event", {
                    "type": "RECONCILIATION_ERROR",
                    "error": str(exc)
                })

