import threading
from queue import PriorityQueue, Empty
from typing import Any, Dict, Optional, Tuple

PRIORITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

class OSPriorityQueue:
    """
    Thread-safe Operating System Priority Queue.
    Uses priority-rank with monotonic sequence-ID tie-breaking to guarantee
    FIFO order among items of equal priority, avoiding thread starvation.
    """

    def __init__(self):
        self._queue = PriorityQueue()
        self._sequence = 0
        self._lock = threading.Lock()

    def put(self, record: Dict[str, Any]) -> int:
        priority_str = record.get("priority", "LOW").upper()
        rank = PRIORITY_RANK.get(priority_str, 2)
        with self._lock:
            self._sequence += 1
            seq = self._sequence
        # Lower tuple items come off first: (rank, seq, record)
        self._queue.put((rank, seq, record))
        return seq

    def get(self, timeout: Optional[float] = 1.0) -> Tuple[int, int, Dict[str, Any]]:
        """
        Pops the highest-priority item.
        Raises queue.Empty if timeout expires.
        """
        return self._queue.get(timeout=timeout)

    def task_done(self):
        self._queue.task_done()

    def qsize(self) -> int:
        return self._queue.qsize()

    def is_empty(self) -> bool:
        return self._queue.empty()

