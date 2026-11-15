import os
import threading
import time
from datetime import datetime, timezone, timedelta
from queue import Empty
from typing import Callable, List, Optional
from backend.config import Config
from backend.storage.base import BaseStorage
from backend.scheduler.priority_queue import OSPriorityQueue

class OSWorkerPool:
    """
    Dynamic Multithreaded Worker Pool with Autoscaling and Retirement Management.
    Emulates OS kernel-level priority scheduling and dynamic resource allocation.
    """

    def __init__(self, storage: BaseStorage, queue: OSPriorityQueue, on_event: Optional[Callable] = None):
        self.storage = storage
        self.queue = queue
        self.on_event = on_event or (lambda ev, data: None)
        
        self.min_workers = Config.MIN_WORKERS
        self.max_workers = Config.MAX_WORKERS
        self.scale_up_depth = Config.SCALE_UP_QUEUE_DEPTH
        self.scale_down_depth = Config.SCALE_DOWN_QUEUE_DEPTH
        
        self._workers: List[threading.Thread] = []
        self._worker_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._retire_tokens = 0
        self._last_scale_time = 0.0

    @property
    def active_worker_count(self) -> int:
        with self._worker_lock:
            return sum(1 for t in self._workers if t.is_alive())

    def start(self):
        with self._worker_lock:
            for _ in range(self.min_workers):
                self._spawn_worker()
        
        # Start Autoscaler Thread
        t_auto = threading.Thread(target=self._autoscale_loop, daemon=True, name="os-autoscaler")
        t_auto.start()

    def stop(self):
        self._stop_event.set()

    def _spawn_worker(self):
        with self._worker_lock:
            if self.active_worker_count >= self.max_workers:
                return
            n = len(self._workers) + 1
            t = threading.Thread(target=self._worker_loop, daemon=True, name=f"worker-thread-{n}")
            self._workers.append(t)
            t.start()
            self.on_event("system_event", {"type": "WORKER_SPAWNED", "worker": t.name, "active_workers": self.active_worker_count})

    def _autoscale_loop(self):
        while not self._stop_event.wait(2.0):
            depth = self.queue.qsize()
            now = time.monotonic()
            
            with self._worker_lock:
                self._workers = [t for t in self._workers if t.is_alive()]
                current_count = len(self._workers)

            # Prevent thrashing by checking scale cooldown
            if now - self._last_scale_time < 4.0:
                continue

            if depth >= self.scale_up_depth and current_count < self.max_workers:
                self._spawn_worker()
                self._last_scale_time = now
                self.on_event("system_event", {
                    "type": "AUTOSCALE_UP",
                    "reason": f"Queue depth {depth} exceeds threshold {self.scale_up_depth}",
                    "active_workers": self.active_worker_count
                })
            elif depth <= self.scale_down_depth and current_count > self.min_workers:
                with self._worker_lock:
                    self._retire_tokens += 1
                self._last_scale_time = now
                self.on_event("system_event", {
                    "type": "AUTOSCALE_DOWN",
                    "reason": f"Queue depth {depth} fell to/below {self.scale_down_depth}",
                    "active_workers": self.active_worker_count
                })

    def _worker_loop(self):
        thread_name = threading.current_thread().name
        while not self._stop_event.is_set():
            try:
                rank, seq, record = self.queue.get(timeout=1.0)
            except Empty:
                with self._worker_lock:
                    if self._retire_tokens > 0 and self.active_worker_count > self.min_workers:
                        self._retire_tokens -= 1
                        self.on_event("system_event", {
                            "type": "WORKER_RETIRED",
                            "worker": thread_name,
                            "reason": "Idle scale-down token consumed",
                            "active_workers": self.active_worker_count - 1
                        })
                        return
                continue

            data_id = str(record.get("data_id"))
            attempt = 1
            started_iso = datetime.now(timezone.utc).isoformat()
            
            try:
                # Retrieve current attempt count from storage
                existing_record = self.storage.get_record(data_id)
                if existing_record:
                    attempt = int(existing_record.get("attempt_count", 0)) + 1

                self.storage.update_record_status(
                    data_id=data_id,
                    status="PROCESSING",
                    attempt_count=attempt,
                    started=True,
                    error=None
                )
                self.on_event("record_processing", {"data_id": data_id, "worker": thread_name, "priority": record.get("priority")})

                # Simulated OS Workload / Data Anomaly & Analytics Execution
                time.sleep(Config.SIMULATED_PROCESS_SECONDS)

                ended_iso = datetime.now(timezone.utc).isoformat()
                self.storage.update_record_status(
                    data_id=data_id,
                    status="COMPLETED",
                    attempt_count=attempt,
                    completed=True,
                    error=None
                )
                self.storage.log_attempt(
                    data_id=data_id,
                    attempt_number=attempt,
                    outcome="COMPLETED",
                    error_message=None,
                    started_at=started_iso,
                    ended_at=ended_iso
                )
                self.on_event("record_completed", {
                    "data_id": data_id,
                    "worker": thread_name,
                    "priority": record.get("priority"),
                    "attempt": attempt
                })

            except Exception as exc:
                ended_iso = datetime.now(timezone.utc).isoformat()
                max_attempts = Config.MAX_ATTEMPTS
                self.storage.log_attempt(
                    data_id=data_id,
                    attempt_number=attempt,
                    outcome="FAILED",
                    error_message=str(exc),
                    started_at=started_iso,
                    ended_at=ended_iso
                )

                if attempt < max_attempts:
                    delay = min(30, 2 ** (attempt - 1))
                    retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
                    self.storage.update_record_status(
                        data_id=data_id,
                        status="RETRY_WAIT",
                        attempt_count=attempt,
                        error=str(exc),
                        retry_at=retry_at
                    )
                    # Launch background retry timer
                    threading.Thread(target=self._requeue_after_delay, args=(record, delay), daemon=True).start()
                    self.on_event("record_retry", {"data_id": data_id, "attempt": attempt, "delay": delay})
                else:
                    self.storage.update_record_status(
                        data_id=data_id,
                        status="FAILED",
                        attempt_count=attempt,
                        completed=True,
                        error=str(exc)
                    )
                    self.on_event("record_failed", {"data_id": data_id, "error": str(exc), "attempt": attempt})

            finally:
                self.queue.task_done()

    def _requeue_after_delay(self, record: dict, delay: float):
        time.sleep(delay)
        data_id = str(record.get("data_id"))
        self.storage.update_record_status(data_id, "QUEUED")
        self.queue.put(record)
        self.on_event("record_requeued", {"data_id": data_id, "priority": record.get("priority")})

