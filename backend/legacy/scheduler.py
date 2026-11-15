import os
import threading
import time
from datetime import datetime, timedelta
from queue import PriorityQueue, Empty
from backend.ingestion import classify_priority

PRIORITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

class Scheduler:
    def __init__(self, db):
        self.db = db
        self.queue = PriorityQueue()
        self._sequence = 0
        self._seq_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._statuses = []
        self._workers = []
        self._worker_lock = threading.RLock()
        self._stop = threading.Event()
        self.min_workers = max(1, int(os.getenv("MIN_WORKERS", "1")))
        self.max_workers = max(self.min_workers, int(os.getenv("MAX_WORKERS", "4")))
        self.scale_up_depth = max(1, int(os.getenv("SCALE_UP_QUEUE_DEPTH", "20")))
        self.scale_down_depth = max(0, int(os.getenv("SCALE_DOWN_QUEUE_DEPTH", "3")))
        self._last_scale = 0
        self._retire_tokens = 0

    @property
    def worker_count(self):
        with self._worker_lock: return sum(1 for t in self._workers if t.is_alive())

    @property
    def queue_depth(self): return self.queue.qsize()

    def add_status(self, message):
        item = {"time": datetime.now().strftime("%H:%M:%S"), "message": str(message)}
        with self._status_lock:
            self._statuses.append(item)
            self._statuses = self._statuses[-100:]

    def recent_statuses(self):
        with self._status_lock: return list(reversed(self._statuses[-30:]))

    def start(self):
        for _ in range(self.min_workers): self._spawn_worker()
        threading.Thread(target=self._autoscale_loop, daemon=True, name="worker-autoscaler").start()
        threading.Thread(target=self._reconcile_loop, daemon=True, name="pending-work-reconciler").start()

    def submit_record(self, record):
        try:
            self.db.persist_record(record)
            self.submit_persisted(record)
            self.add_status(f"Persisted record {record['data_id']} from {record.get('source_port')} with {record['priority']} priority")
        except Exception as exc:
            self.add_status(f"Persistence failed for incoming record: {exc}")

    def submit_persisted(self, record):
        data_id = record.get("data_id")
        if data_id is None: return
        self.db.mark_status(data_id, "QUEUED")
        with self._seq_lock:
            self._sequence += 1
            seq = self._sequence
        # PriorityQueue uses lower numeric values first; sequence provides FIFO within a priority.
        self.queue.put((PRIORITY_RANK.get(record["priority"], 2), seq, record))
        self.add_status(f"Queued record {data_id} ({record['priority']})")

    def _spawn_worker(self):
        with self._worker_lock:
            if self.worker_count >= self.max_workers: return
            n = len(self._workers) + 1
            t = threading.Thread(target=self._worker_loop, daemon=True, name=f"worker-{n}")
            self._workers.append(t)
            t.start()
            self.add_status(f"Started worker {t.name}")

    def _autoscale_loop(self):
        while not self._stop.wait(2):
            depth = self.queue.qsize()
            now = time.monotonic()
            with self._worker_lock:
                live = [t for t in self._workers if t.is_alive()]
                self._workers = live
                count = len(live)
            if now - self._last_scale < 5: continue
            if depth >= self.scale_up_depth and count < self.max_workers:
                self._spawn_worker()
                self._last_scale = now
            elif depth <= self.scale_down_depth and count > self.min_workers:
                # Ask one idle worker to retire; it will finish any current task first.
                with self._worker_lock:
                    self._retire_tokens += 1
                    self._target_workers = max(self.min_workers, count - 1)
                self._last_scale = now
            else:
                self._target_workers = max(getattr(self, "_target_workers", self.min_workers), count)

    def _reconcile_loop(self):
        # Reconcile durable pending/retry work after app restarts. Queue is in-memory;
        # MySQL process status is the source of truth. Avoid repeated enqueue while queued.
        while not self._stop.wait(10):
            try:
                for record in self.db.fetch_pending_records():
                    self.submit_persisted(record)
            except Exception as exc:
                self.add_status(f"Pending-work reconciliation failed: {exc}")

    def _worker_loop(self):
        while not self._stop.is_set():
            try: _, _, record = self.queue.get(timeout=1)
            except Empty:
                with self._worker_lock:
                    if self._retire_tokens > 0 and self.worker_count > self.min_workers:
                        self._retire_tokens -= 1
                        self.add_status(f"Worker {threading.current_thread().name} retired after idle period")
                        return
                continue
            data_id = record["data_id"]
            attempt = 0
            try:
                # Processing operation is deliberately lightweight in this MVP.
                existing = self.db.get_attempt_count(data_id)
                attempt = existing + 1
                started = datetime.now()
                self.db.mark_status(data_id, "PROCESSING", attempt_count=attempt, started=True, error=None)
                # Replace this demonstration work with the project's real processing task.
                time.sleep(float(os.getenv("SIMULATED_PROCESS_SECONDS", "0.15")))
                self.db.mark_status(data_id, "COMPLETED", attempt_count=attempt, completed=True, error=None)
                self.db.log_attempt(data_id, attempt, "COMPLETED", None, started, datetime.now())
                self.add_status(f"Record {data_id} completed by {threading.current_thread().name}")
            except Exception as exc:
                try:
                    attempt = max(1, attempt)
                    retryable = not isinstance(exc, (ValueError, TypeError))
                    max_attempts = int(os.getenv("MAX_ATTEMPTS", "3"))
                    self.db.log_attempt(data_id, attempt, "FAILED", str(exc), datetime.now(), datetime.now())
                    if retryable and attempt < max_attempts:
                        delay = min(30, 2 ** (attempt - 1))
                        retry_at = datetime.now() + timedelta(seconds=delay)
                        self.db.mark_status(data_id, "RETRY_WAIT", attempt_count=attempt, error=str(exc), retry_at=retry_at)
                        threading.Thread(target=self._retry_later, args=(record, delay), daemon=True).start()
                        self.add_status(f"Record {data_id} failed; retry {attempt + 1}/{max_attempts} in {delay}s")
                    else:
                        self.db.mark_status(data_id, "FAILED", attempt_count=attempt, error=str(exc), completed=True)
                        self.add_status(f"Record {data_id} marked FAILED: {exc}")
                except Exception as secondary:
                    self.add_status(f"Could not persist failure state for record {data_id}: {secondary}")
            finally:
                self.queue.task_done()

    def _retry_later(self, record, delay):
        time.sleep(delay)
        try: self.submit_persisted(record)
        except Exception as exc: self.add_status(f"Could not requeue record {record.get('data_id')}: {exc}")
