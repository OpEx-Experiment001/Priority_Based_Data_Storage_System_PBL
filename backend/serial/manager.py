import os
import threading
import time
from typing import Callable, Dict, List, Optional
import serial
from serial.tools import list_ports
from backend.config import Config
from backend.ingestion.parser import parse_telemetry_message
from backend.ingestion.classifier import PriorityClassifier

class SerialManager:
    """
    Manages physical COM / serial ports with resilient, non-blocking connection lifecycles.
    Cooperative thread termination prevents Windows OS ClearCommError/access violations on close.
    """

    def __init__(self, classifier: PriorityClassifier, on_record_ingested: Callable,
                 on_status_event: Callable, on_ports_changed: Optional[Callable] = None):
        self.classifier = classifier
        self.on_record_ingested = on_record_ingested
        self.on_status_event = on_status_event
        self.on_ports_changed = on_ports_changed

        self._lock = threading.RLock()
        self._selected_ports: List[str] = []
        self._connections: Dict[str, serial.Serial] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._stop_events: Dict[str, threading.Event] = {}
        self._global_stop = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._last_port_set: Optional[set] = None

    def start_monitoring(self):
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True, name="serial-monitor")
        self._monitor_thread.start()

    def stop_all(self):
        self._global_stop.set()
        with self._lock:
            for port in list(self._connections.keys()):
                self._close_port(port)

    def _monitor_loop(self):
        while not self._global_stop.wait(3.0):
            try:
                discovered_ports = list_ports.comports()
                available = {p.device for p in discovered_ports}
                with self._lock:
                    # Clean up ports that were unplugged
                    for port in list(self._connections.keys()):
                        if port not in available:
                            self._close_port(port)
                            self.on_status_event(f"Hardware device on {port} disconnected or removed", "PORT_DISCONNECTED")

                    # Attempt reconnection for selected ports that came back online
                    for port in self._selected_ports:
                        if port in available and port not in self._connections:
                            self._open_port(port)

                # Detect physical port set changes and push SSE notification
                if self.on_ports_changed and self._last_port_set != available:
                    self._last_port_set = available
                    ports_list = [
                        {"device": p.device, "description": p.description or "Serial device"}
                        for p in discovered_ports
                    ]
                    try:
                        self.on_ports_changed(ports_list)
                    except Exception:
                        pass
            except Exception as exc:
                self.on_status_event(f"Port monitor error: {exc}", "PORT_ERROR")

    def _open_port(self, port_name: str) -> bool:
        try:
            baud = Config.SERIAL_BAUD
            # Short timeout enables cooperative thread shutdown without blocking indefinitely
            conn = serial.Serial(port_name, baudrate=baud, timeout=0.5)
            stop_ev = threading.Event()
            
            self._connections[port_name] = conn
            self._stop_events[port_name] = stop_ev
            
            t = threading.Thread(target=self._read_loop, args=(port_name, conn, stop_ev),
                                 daemon=True, name=f"serial-reader-{port_name}")
            self._threads[port_name] = t
            t.start()
            
            self.on_status_event(f"Connected to serial port {port_name} at {baud} baud", "PORT_CONNECTED")
            return True
        except Exception as exc:
            self.on_status_event(f"Failed to open port {port_name}: {exc}", "PORT_ERROR")
            return False

    def _close_port(self, port_name: str):
        stop_ev = self._stop_events.pop(port_name, None)
        if stop_ev:
            stop_ev.set()
        
        conn = self._connections.pop(port_name, None)
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        
        self._threads.pop(port_name, None)

    def _read_loop(self, port_name: str, conn: serial.Serial, stop_ev: threading.Event):
        while not stop_ev.is_set() and not self._global_stop.is_set():
            try:
                if not conn.is_open:
                    break
                raw_bytes = conn.readline()
                if not raw_bytes:
                    continue
                line = raw_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                try:
                    record = parse_telemetry_message(line, source_port=port_name)
                    priority = self.classifier.classify(record["module_id"], record["values"])
                    record["priority"] = priority
                    self.on_record_ingested(record)
                except ValueError as val_err:
                    self.on_status_event(f"{port_name} parse warning: {val_err}", "PARSE_WARNING")

            except (serial.SerialException, OSError) as exc:
                if not stop_ev.is_set():
                    self.on_status_event(f"{port_name} I/O read error: {exc}", "PORT_ERROR")
                break

        # Cleanup if reader thread exited unexpectedly
        with self._lock:
            if self._connections.get(port_name) is conn:
                self._close_port(port_name)

    def set_selected_ports(self, port_names: List[str]) -> List[str]:
        errors = []
        with self._lock:
            # Close deselected ports
            for port in list(self._connections.keys()):
                if port not in port_names:
                    self._close_port(port)

            self._selected_ports = list(dict.fromkeys(port_names))
            
            # Connect newly selected ports
            for port in self._selected_ports:
                if port not in self._connections:
                    success = self._open_port(port)
                    if not success:
                        errors.append(f"Could not connect to {port}")
        return errors

    def get_selected_ports(self) -> List[str]:
        with self._lock:
            return list(self._selected_ports)

    def get_connected_ports(self) -> List[str]:
        with self._lock:
            return list(self._connections.keys())

