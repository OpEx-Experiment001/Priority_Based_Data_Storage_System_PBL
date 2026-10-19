from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from queue import PriorityQueue, Empty
from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv
from serial.tools import list_ports

from backend.db import Database
from backend.ingestion import parse_message, classify_priority
from backend.serial_manager import SerialManager
from backend.scheduler import Scheduler

load_dotenv()
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "change-this-development-key")

db = Database()
scheduler = Scheduler(db)
serial_manager = SerialManager(on_record= scheduler.submit_record, on_status=scheduler.add_status)
serial_manager.start_monitoring()
scheduler.start()

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/api/ports")
def ports():
    result = []
    for p in list_ports.comports():
        result.append({
            "device": p.device,
            "description": p.description or "Serial device",
            "manufacturer": p.manufacturer,
            "serial_number": p.serial_number,
            "vid": f"{p.vid:04X}" if p.vid is not None else None,
            "pid": f"{p.pid:04X}" if p.pid is not None else None,
        })
    return jsonify({"ports": result, "selected": serial_manager.selected_ports(),
                    "connected": serial_manager.connected_ports()})

@app.post("/api/ports/connect")
def connect_ports():
    body = request.get_json(silent=True) or {}
    devices = body.get("ports", [])
    if not isinstance(devices, list) or not all(isinstance(x, str) for x in devices):
        return jsonify({"error": "ports must be a list of serial-port names"}), 400
    available = {p.device for p in list_ports.comports()}
    invalid = [d for d in devices if d not in available]
    if invalid:
        return jsonify({"error": f"These ports are no longer available: {', '.join(invalid)}"}), 400
    errors = serial_manager.set_ports(devices)
    return jsonify({"selected": serial_manager.selected_ports(),
                    "connected": serial_manager.connected_ports(), "errors": errors})

@app.post("/api/ports/disconnect")
def disconnect_ports():
    serial_manager.set_ports([])
    return jsonify({"selected": [], "connected": []})

@app.get("/api/status")
def status():
    return jsonify({
        "selected_ports": serial_manager.selected_ports(),
        "connected_ports": serial_manager.connected_ports(),
        "workers": scheduler.worker_count,
        "queue_depth": scheduler.queue_depth,
        "records_received": db.count_records(),
        "recent_records": db.recent_records(50),
        "statuses": scheduler.recent_statuses(),
        "priority_counts": db.priority_counts(),
    })

@app.get("/api/records")
def records():
    return jsonify({"records": db.recent_records(200)})

@app.post("/api/demo-record")
def demo_record():
    body = request.get_json(silent=True) or {}
    try:
        module_id = int(body.get("module_id", 101))
        values = [float(v) for v in body.get("values", [28.4, 64.9, 554, 28.5])]
    except (TypeError, ValueError):
        return jsonify({"error": "module_id must be an integer and values must be numeric"}), 400
    record = {"module_id": module_id, "values": values, "received_at": datetime.now(timezone.utc).isoformat(),
              "source_port": "DEMO"}
    record["priority"] = classify_priority(module_id, values)
    db.persist_record(record)
    scheduler.submit_persisted(record)
    return jsonify({"message": "Demo record persisted and queued", "record": record}), 201

@app.get("/api/health")
def health():
    try:
        db.ping()
        return jsonify({"status": "ok", "database": "connected"})
    except Exception as exc:
        return jsonify({"status": "degraded", "database": str(exc)}), 503

if __name__ == "__main__":
    app.run(host=os.getenv("FLASK_HOST", "127.0.0.1"), port=int(os.getenv("FLASK_PORT", "5000")), debug=False, threaded=True)
