import random
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional
from backend.config import Config
from backend.ingestion.classifier import PriorityClassifier

class VirtualHardwareGenerator:
    """
    Sandboxed Virtual Microcontroller Generator.
    Safely simulates Arduino sensor packets in-memory without needing physical USB hardware.
    Protects the host machine from hardware serial lockups and COM driver crashes.
    """

    def __init__(self, classifier: PriorityClassifier, on_record_generated: Callable, on_status_event: Callable):
        self.classifier = classifier
        self.on_record_generated = on_record_generated
        self.on_status_event = on_status_event
        self._is_running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.interval_sec = max(0.2, Config.VIRTUAL_INTERVAL_MS / 1000.0)

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._is_running

    def start(self):
        with self._lock:
            if self._is_running:
                return
            self._is_running = True
            self._thread = threading.Thread(target=self._generator_loop, daemon=True, name="virtual-arduino-node")
            self._thread.start()
            self.on_status_event("Sandboxed Virtual Hardware Generator activated", "VIRTUAL_HARDWARE_STARTED")

    def stop(self):
        with self._lock:
            if not self._is_running:
                return
            self._is_running = False
            self.on_status_event("Sandboxed Virtual Hardware Generator deactivated", "VIRTUAL_HARDWARE_STOPPED")

    def toggle(self) -> bool:
        if self.is_running:
            self.stop()
            return False
        else:
            self.start()
            return True

    def _make_record(self):
        """Generate a single telemetry record dict without calling any callback."""
        mod_choice = random.choice([101, 102])
        now_iso = datetime.now(timezone.utc).isoformat()

        if mod_choice == 101:
            temp = round(random.uniform(18.0, 36.5), 1)
            hum = round(random.uniform(30.0, 90.0), 1)
            if random.random() < 0.25:
                gas = random.randint(800, 980)
            elif random.random() < 0.35:
                gas = random.randint(600, 799)
            else:
                gas = random.randint(200, 599)
            press = round(random.uniform(980.0, 1045.0), 1)
            values = [temp, hum, float(gas), press]
            raw_str = f"{{{mod_choice},{temp},{hum},{gas},{press}}}"
        else:
            motion = random.randint(0, 100)
            dist = round(random.uniform(2.0, 50.0), 1)
            tilt = round(random.uniform(-45.0, 45.0), 1)
            battery = round(random.uniform(3.4, 4.2), 2)
            values = [float(motion), dist, tilt, battery]
            raw_str = f"{{{mod_choice},{motion},{dist},{tilt},{battery}}}"

        priority = self.classifier.classify(mod_choice, values)
        return {
            "module_id": mod_choice,
            "values": values,
            "raw_message": raw_str,
            "source_port": "VIRTUAL_COM1",
            "priority": priority,
            "received_at": now_iso
        }

    def generate_single(self, callback: Callable):
        """Public method: generate exactly one record and call the given callback with it."""
        record = self._make_record()
        callback(record)

    def _generator_loop(self):
        while self.is_running:
            try:
                record = self._make_record()
                self.on_record_generated(record)
            except Exception as exc:
                self.on_status_event(f"Virtual generator exception: {exc}", "VIRTUAL_HARDWARE_ERROR")

            time.sleep(self.interval_sec)

