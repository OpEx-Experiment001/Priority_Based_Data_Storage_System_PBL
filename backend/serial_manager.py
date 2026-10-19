import threading
import time
import serial
from serial.tools import list_ports
from backend.ingestion import parse_message, classify_priority

class SerialManager:
    def __init__(self, on_record, on_status):
        self.on_record = on_record
        self.on_status = on_status
        self._lock = threading.RLock()
        self._selected = []
        self._connections = {}
        self._threads = {}
        self._stop = threading.Event()
        self._monitor_thread = None

    def start_monitoring(self):
        if self._monitor_thread and self._monitor_thread.is_alive(): return
        self._monitor_thread = threading.Thread(target=self._monitor_ports, daemon=True, name="serial-port-monitor")
        self._monitor_thread.start()

    def _monitor_ports(self):
        while not self._stop.is_set():
            available = {p.device for p in list_ports.comports()}
            with self._lock:
                for device in list(self._connections):
                    if device not in available:
                        try: self._connections[device].close()
                        except Exception: pass
                        self._connections.pop(device, None)
                        self.on_status(f"{device} disconnected or removed")
                for device in self._selected:
                    if device in available and device not in self._connections:
                        self._open_port(device)
            self._stop.wait(3)

    def _open_port(self, device):
        try:
            baud = int(__import__("os").getenv("SERIAL_BAUD", "9600"))
            conn = serial.Serial(device, baudrate=baud, timeout=1)
            self._connections[device] = conn
            t = threading.Thread(target=self._read_loop, args=(device, conn), daemon=True, name=f"serial-{device}")
            self._threads[device] = t
            t.start()
            self.on_status(f"Connected to {device} at {baud} baud")
        except Exception as exc:
            self.on_status(f"Could not open {device}: {exc}")

    def _read_loop(self, device, conn):
        while not self._stop.is_set():
            try:
                if not conn.is_open: break
                raw = conn.readline()
                if not raw: continue
                line = raw.decode("utf-8", errors="replace").strip()
                if not line: continue
                try:
                    record = parse_message(line, device)
                    record["priority"] = classify_priority(record["module_id"], record["values"])
                    self.on_record(record)
                    self.on_status(f"{device}: accepted module {record['module_id']} record")
                except ValueError as exc:
                    self.on_status(f"{device}: rejected input — {exc}")
            except (serial.SerialException, OSError) as exc:
                self.on_status(f"{device}: serial read error — {exc}")
                break
        with self._lock:
            if self._connections.get(device) is conn:
                self._connections.pop(device, None)
            try: conn.close()
            except Exception: pass

    def set_ports(self, devices):
        errors = []
        with self._lock:
            for device, conn in list(self._connections.items()):
                if device not in devices:
                    try: conn.close()
                    except Exception: pass
                    self._connections.pop(device, None)
            self._selected = list(dict.fromkeys(devices))
            for device in self._selected:
                if device not in self._connections:
                    self._open_port(device)
            for device in self._selected:
                if device not in self._connections:
                    errors.append(f"{device}: connection not established; check that it is not used by another application")
        return errors

    def selected_ports(self):
        with self._lock: return list(self._selected)

    def connected_ports(self):
        with self._lock: return list(self._connections.keys())
