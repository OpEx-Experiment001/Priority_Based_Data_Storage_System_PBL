from __future__ import annotations

import io
import os
import json
import socket
import threading
import time
import zipfile
from queue import Queue, Empty
from datetime import datetime, timezone
from flask import Flask, jsonify, render_template, request, Response, stream_with_context
from serial.tools import list_ports

from backend.config import Config
from backend.auth.iam import (
    Role, Permission, create_jwt_token, verify_jwt_token,
    require_auth, require_permission, extract_token_from_request
)
from backend.auth.user_manager import UserManager
from backend.storage.factory import create_storage_engine
from backend.ingestion.parser import parse_telemetry_message
from backend.ingestion.classifier import PriorityClassifier
from backend.scheduler.priority_queue import OSPriorityQueue
from backend.scheduler.worker_pool import OSWorkerPool
from backend.scheduler.reconciler import WorkReconciler
from backend.serial.manager import SerialManager
from backend.serial.virtual_generator import VirtualHardwareGenerator

# Initialize Flask App
app = Flask(__name__)
app.config["SECRET_KEY"] = Config.JWT_SECRET

# Storage & Auth Initialization (NoSQL JSON document store stored on host machine)
storage = create_storage_engine()
user_manager = UserManager()
classifier = PriorityClassifier(storage)

# OS Concurrency & Priority Queue Setup (Used in Python storage mode)
queue = OSPriorityQueue()

# -----------------------------------------------------------------------------
# Server-Sent Events (SSE) Client Registry & Broadcaster
# -----------------------------------------------------------------------------
sse_clients: set[Queue] = set()
sse_lock = threading.Lock()

def broadcast_event(event_type: str, data: dict):
    """Pushes an SSE frame to all connected clients."""
    payload = json.dumps(data)
    sse_frame = f"event: {event_type}\ndata: {payload}\n\n"
    with sse_lock:
        for client_q in list(sse_clients):
            try:
                client_q.put_nowait(sse_frame)
            except Exception:
                pass

# Background thread: forward broadcast events from C++ engine to SSE clients
def cpp_event_listener_loop():
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", 8085))
            f = s.makefile("r", encoding="utf-8")
            while True:
                line = f.readline()
                if not line:
                    break
                try:
                    parsed = json.loads(line.strip())
                    if "event" in parsed:
                        broadcast_event(parsed["event"], parsed.get("data", {}))
                except Exception:
                    pass
            s.close()
        except Exception:
            pass
        time.sleep(2.0)

# Start C++ event forwarding thread if running in C++ mode
if Config.STORAGE_ENGINE == "cpp":
    threading.Thread(target=cpp_event_listener_loop, daemon=True, name="cpp-sse-bridge").start()

# OS Worker Pool & Reconciler
worker_pool = OSWorkerPool(storage=storage, queue=queue, on_event=broadcast_event)
reconciler = WorkReconciler(storage=storage, queue=queue, on_event=broadcast_event)

# Core Ingestion Handler
def on_telemetry_received(record: dict):
    try:
        data_id = storage.persist_record(record)
        storage.update_record_status(data_id, "QUEUED")
        seq = queue.put(record)
        broadcast_event("record_ingested", {
            "data_id": data_id,
            "module_id": record["module_id"],
            "priority": record["priority"],
            "source_port": record.get("source_port"),
            "values": record.get("values", []),
            "queue_depth": queue.qsize()
        })
    except Exception as exc:
        broadcast_event("system_event", {"type": "INGESTION_ERROR", "error": str(exc)})

def on_system_status(message: str, event_type: str = "SYSTEM_INFO"):
    broadcast_event("system_event", {"type": event_type, "message": message})

# Hardware & Virtual Serial Managers
serial_manager = SerialManager(
    classifier=classifier,
    on_record_ingested=on_telemetry_received,
    on_status_event=on_system_status,
    on_ports_changed=lambda ports: broadcast_event("ports_changed", {"ports": ports, "connected": serial_manager.get_connected_ports()})
)
virtual_generator = VirtualHardwareGenerator(
    classifier=classifier,
    on_record_generated=on_telemetry_received,
    on_status_event=on_system_status
)

# Start Background Daemons
worker_pool.start()
reconciler.start()
serial_manager.start_monitoring()
if Config.ENABLE_VIRTUAL_HARDWARE:
    virtual_generator.start()

# -----------------------------------------------------------------------------
# Server-Sent Events (SSE) Route
# -----------------------------------------------------------------------------
@app.get("/api/events/stream")
def sse_event_stream():
    """
    HTTP Server-Sent Events endpoint.
    Establishes a persistent, auto-reconnecting unidirectional stream to the browser.
    """
    client_q = Queue(maxsize=100)
    with sse_lock:
        sse_clients.add(client_q)

    def event_generator():
        try:
            # Send initial connected handshake event
            handshake = json.dumps({
                "status": "connected",
                "storage_engine": Config.STORAGE_ENGINE,
                "workers": worker_pool.active_worker_count,
                "queue_depth": queue.qsize(),
                "virtual_hardware": virtual_generator.is_running
            })
            yield f"event: connected\ndata: {handshake}\n\n"

            while True:
                try:
                    # Wait for an event frame (timeout every 15s to send keep-alive ping)
                    frame = client_q.get(timeout=15.0)
                    yield frame
                except Empty:
                    yield ": ping\n\n"
        except GeneratorExit:
            pass
        finally:
            with sse_lock:
                sse_clients.discard(client_q)

    return Response(
        stream_with_context(event_generator()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Transfer-Encoding": "chunked"
        }
    )

# -----------------------------------------------------------------------------
# Web Page Route
# -----------------------------------------------------------------------------
@app.get("/")
def index():
    return render_template("index.html")

@app.get("/ws")
def ws_stub():
    """Stub route: WebSockets replaced by SSE. Prevents 404 log noise."""
    return jsonify({"error": "WebSockets not supported. Use /api/events/stream (SSE)."}), 410

# -----------------------------------------------------------------------------
# IAM & Authentication Endpoints
# -----------------------------------------------------------------------------
@app.post("/api/auth/signup")
def signup():
    body = request.get_json(silent=True) or {}
    username = body.get("username", "").strip()
    password = body.get("password", "")
    role = body.get("role", Role.OPERATOR).upper()

    user_count = user_manager.count_users()

    if user_count == 0:
        # First user becomes root superadmin regardless of requested role
        try:
            user = user_manager.create_user(username, password, Role.SUPERADMIN)
            from tinydb import Query as _Q
            q = _Q()
            user_manager.users_table.update({"is_root": True}, q.username == username)
            user["is_root"] = True
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        effective = user_manager.get_effective_permissions(username)
        token = create_jwt_token(username, Role.SUPERADMIN, effective_permissions=effective)
        return jsonify({"message": "Root superadmin created", "token": token, "user": user}), 201
    else:
        # Subsequent signups require USERS_CREATE permission
        from backend.auth.iam import extract_token_from_request, verify_jwt_token
        token_str = extract_token_from_request()
        if not token_str:
            return jsonify({"error": "Unauthorized: Missing authentication token"}), 401
        payload = verify_jwt_token(token_str)
        if not payload:
            return jsonify({"error": "Unauthorized: Invalid or expired token"}), 401
        if Permission.USERS_CREATE not in payload.get("permissions", []):
            return jsonify({"error": f"Forbidden: You lack permission '{Permission.USERS_CREATE}' for this action."}), 403
        try:
            user = user_manager.create_user(username, password, role)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        effective = user_manager.get_effective_permissions(username)
        token = create_jwt_token(username, user["role"], effective_permissions=effective)
        return jsonify({"message": f"User '{username}' created", "token": token, "user": user}), 201


@app.post("/api/auth/login")
def login():
    body = request.get_json(silent=True) or {}
    username = body.get("username", "").strip()
    password = body.get("password", "")

    user = user_manager.authenticate(username, password)
    if not user:
        return jsonify({"error": "Invalid username or password"}), 401

    effective = user_manager.get_effective_permissions(username)
    token = create_jwt_token(
        user["username"],
        user["role"],
        effective_permissions=effective,
        groups=user.get("groups", [])
    )
    return jsonify({
        "message": "Authentication successful",
        "token": token,
        "user": user
    })

@app.get("/api/auth/me")
@require_auth
def get_current_user_profile():
    return jsonify({"user": request.current_user})

@app.get("/api/users")
@require_permission(Permission.USERS_READ)
def list_users():
    return jsonify({"users": user_manager.list_users()})

@app.post("/api/users")
@require_permission(Permission.USERS_CREATE)
def create_user():
    body = request.get_json(silent=True) or {}
    username = body.get("username", "").strip()
    password = body.get("password", "")
    role = body.get("role", Role.OPERATOR).upper()
    try:
        user = user_manager.create_user(username, password, role)
        return jsonify({"message": f"User '{username}' created", "user": user}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

@app.put("/api/users/<username>")
@require_permission(Permission.USERS_UPDATE)
def update_user(username: str):
    body = request.get_json(silent=True) or {}
    role = body.get("role")
    is_active = body.get("is_active")
    password = body.get("password")
    try:
        success = user_manager.update_user(username, role=role, is_active=is_active, password=password)
        if not success:
            return jsonify({"error": "User not found or nothing updated"}), 404
        return jsonify({"message": f"User '{username}' updated successfully"})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

@app.delete("/api/users/<username>")
@require_permission(Permission.USERS_DELETE_SINGLE)
def delete_user(username: str):
    try:
        success = user_manager.delete_user(username)
        if not success:
            return jsonify({"error": "User not found"}), 404
        return jsonify({"message": f"User '{username}' deleted successfully"})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

@app.post("/api/users/batch-delete")
@require_permission(Permission.USERS_DELETE_BATCH)
def batch_delete_users():
    body = request.get_json(silent=True) or {}
    usernames = body.get("usernames", [])
    if not isinstance(usernames, list):
        return jsonify({"error": "usernames must be a list"}), 400
    deleted_count = user_manager.batch_delete_users(usernames)
    return jsonify({"message": f"Successfully deleted {deleted_count} user(s)", "count": deleted_count})

@app.post("/api/users/purge")
@require_permission(Permission.USERS_PURGE_ALL)
def purge_users():
    purged_count = user_manager.purge_users()
    return jsonify({"message": f"Purged {purged_count} non-admin user(s)", "count": purged_count})

# -----------------------------------------------------------------------------
# User Groups Endpoints
# -----------------------------------------------------------------------------
@app.get("/api/groups")
@require_permission(Permission.GROUPS_MANAGE)
def list_groups():
    return jsonify({"groups": user_manager.list_groups()})

@app.post("/api/groups")
@require_permission(Permission.GROUPS_MANAGE)
def create_group():
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    permissions = body.get("permissions", [])
    if not name:
        return jsonify({"error": "Group name is required"}), 400
    try:
        created_by = request.current_user.get("sub", "system") if hasattr(request, "current_user") and request.current_user else "system"
        grp = user_manager.create_group(name, permissions, created_by)
        return jsonify({"message": f"Group '{name}' created", "group": grp}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

@app.delete("/api/groups/<name>")
@require_permission(Permission.GROUPS_MANAGE)
def delete_group(name: str):
    success = user_manager.delete_group(name)
    if not success:
        return jsonify({"error": "Group not found"}), 404
    return jsonify({"message": f"Group '{name}' deleted successfully"})

@app.post("/api/groups/<name>/assign")
@require_permission(Permission.GROUPS_MANAGE)
def assign_user_to_group(name: str):
    body = request.get_json(silent=True) or {}
    username = body.get("username", "").strip()
    if not username:
        return jsonify({"error": "username is required"}), 400
    success = user_manager.assign_user_to_group(username, name)
    if not success:
        return jsonify({"error": "User or group not found"}), 404
    return jsonify({"message": f"User '{username}' assigned to group '{name}'"})

@app.post("/api/groups/<name>/remove")
@require_permission(Permission.GROUPS_MANAGE)
def remove_user_from_group(name: str):
    body = request.get_json(silent=True) or {}
    username = body.get("username", "").strip()
    if not username:
        return jsonify({"error": "username is required"}), 400
    success = user_manager.remove_user_from_group(username, name)
    if not success:
        return jsonify({"error": "User or group not found, or user not in group"}), 404
    return jsonify({"message": f"User '{username}' removed from group '{name}'"})

# -----------------------------------------------------------------------------
# Per-User Fine-Grained Permission Overrides
# -----------------------------------------------------------------------------
@app.post("/api/users/<username>/permissions/grant")
@require_permission(Permission.USERS_CUSTOMIZE)
def grant_user_permission(username: str):
    body = request.get_json(silent=True) or {}
    permission = body.get("permission", "").strip()
    if not permission:
        return jsonify({"error": "permission is required"}), 400
    success = user_manager.grant_permission(username, permission)
    if not success:
        return jsonify({"error": "User not found"}), 404
    return jsonify({
        "message": f"Permission '{permission}' granted to user '{username}'",
        "effective_permissions": user_manager.get_effective_permissions(username)
    })

@app.post("/api/users/<username>/permissions/revoke")
@require_permission(Permission.USERS_CUSTOMIZE)
def revoke_user_permission(username: str):
    body = request.get_json(silent=True) or {}
    permission = body.get("permission", "").strip()
    if not permission:
        return jsonify({"error": "permission is required"}), 400
    success = user_manager.revoke_permission(username, permission)
    if not success:
        return jsonify({"error": "User not found"}), 404
    return jsonify({
        "message": f"Permission '{permission}' revoked from user '{username}'",
        "effective_permissions": user_manager.get_effective_permissions(username)
    })

@app.get("/api/users/<username>/permissions")
@require_permission(Permission.USERS_READ)
def get_user_permissions(username: str):
    user = user_manager.get_user(username)
    if not user:
        return jsonify({"error": "User not found"}), 404
    effective = user_manager.get_effective_permissions(username)
    return jsonify({
        "username": username,
        "effective_permissions": effective,
        "extra_permissions": user.get("extra_permissions", []),
        "revoked_permissions": user.get("revoked_permissions", []),
        "groups": user.get("groups", [])
    })

# -----------------------------------------------------------------------------
# Data Records CRUD Endpoints (Single, Batch, Whole)
# -----------------------------------------------------------------------------
@app.get("/api/records")
@require_permission(Permission.RECORDS_READ)
def get_records():
    limit = min(500, int(request.args.get("limit", 100)))
    offset = max(0, int(request.args.get("offset", 0)))
    status = request.args.get("status")
    priority = request.args.get("priority")
    
    records = storage.list_records(limit=limit, offset=offset, status=status, priority=priority)
    total = storage.count_records(status=status, priority=priority)
    return jsonify({
        "records": records,
        "total": total,
        "limit": limit,
        "offset": offset
    })

@app.get("/api/records/<data_id>")
@require_permission(Permission.RECORDS_READ)
def get_single_record(data_id: str):
    rec = storage.get_record(data_id)
    if not rec:
        return jsonify({"error": "Record not found"}), 404
    return jsonify({"record": rec})

@app.post("/api/records")
@require_permission(Permission.RECORDS_CREATE)
def create_record():
    body = request.get_json(silent=True) or {}
    try:
        module_id = int(body.get("module_id", 101))
        values = [float(v) for v in body.get("values", [28.4, 64.9, 554, 28.5])]
    except (TypeError, ValueError):
        return jsonify({"error": "module_id must be integer, values must be numeric array"}), 400

    priority = body.get("priority") or classifier.classify(module_id, values)
    record = {
        "module_id": module_id,
        "values": values,
        "raw_message": body.get("raw_message", f"{{{module_id},{','.join(map(str, values))}}}"),
        "source_port": body.get("source_port", "API_DEMO"),
        "priority": priority,
        "received_at": datetime.now(timezone.utc).isoformat()
    }
    on_telemetry_received(record)
    return jsonify({"message": "Record ingested and queued", "record": record}), 201

@app.put("/api/records/<data_id>")
@require_permission(Permission.RECORDS_UPDATE)
def update_single_record(data_id: str):
    body = request.get_json(silent=True) or {}
    success = storage.update_record(data_id, body)
    if not success:
        return jsonify({"error": "Record not found or failed to update"}), 404
    broadcast_event("record_updated", {"data_id": data_id, "updates": body})
    return jsonify({"message": f"Record {data_id} updated successfully"})

@app.delete("/api/records/<data_id>")
@require_permission(Permission.RECORDS_DELETE_SINGLE)
def delete_single_record(data_id: str):
    success = storage.delete_record(data_id)
    if not success:
        return jsonify({"error": "Record not found"}), 404
    broadcast_event("record_deleted", {"data_id": data_id})
    return jsonify({"message": f"Record {data_id} deleted successfully"})

@app.post("/api/records/batch-delete")
@require_permission(Permission.RECORDS_DELETE_BATCH)
def batch_delete_records():
    body = request.get_json(silent=True) or {}
    data_ids = body.get("data_ids", [])
    if not isinstance(data_ids, list):
        return jsonify({"error": "data_ids must be a list"}), 400
    deleted_count = storage.batch_delete_records(data_ids)
    broadcast_event("records_batch_deleted", {"data_ids": data_ids, "count": deleted_count})
    return jsonify({"message": f"Successfully deleted {deleted_count} record(s)", "count": deleted_count})

@app.post("/api/records/batch-priority")
@require_permission(Permission.RECORDS_UPDATE)
def batch_update_priority():
    body = request.get_json(silent=True) or {}
    data_ids = body.get("data_ids", [])
    new_priority = body.get("priority", "LOW").upper()
    if not isinstance(data_ids, list):
        return jsonify({"error": "data_ids must be a list"}), 400
    try:
        updated_count = storage.batch_update_priority(data_ids, new_priority)
        broadcast_event("records_priority_changed", {"data_ids": data_ids, "priority": new_priority, "count": updated_count})
        return jsonify({"message": f"Updated priority to {new_priority} for {updated_count} record(s)", "count": updated_count})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

@app.post("/api/records/batch-requeue")
@require_permission(Permission.RECORDS_UPDATE)
def batch_requeue():
    body = request.get_json(silent=True) or {}
    data_ids = body.get("data_ids", [])
    requeued = 0
    for data_id in data_ids:
        rec = storage.get_record(str(data_id))
        if rec:
            storage.update_record_status(str(data_id), "QUEUED")
            queue.put(rec)
            requeued += 1
    broadcast_event("records_requeued", {"data_ids": data_ids, "count": requeued})
    return jsonify({"message": f"Requeued {requeued} record(s) for OS processing", "count": requeued})

@app.post("/api/records/purge")
@require_permission(Permission.RECORDS_PURGE_ALL)
def purge_records():
    status = request.args.get("status")
    purged_count = storage.purge_records(status=status)
    broadcast_event("records_purged", {"status_filter": status, "count": purged_count})
    return jsonify({"message": f"Purged {purged_count} records", "count": purged_count})

@app.get("/api/records/export")
@require_permission(Permission.RECORDS_READ)
def export_records():
    fmt = request.args.get("format", "").lower()

    if fmt == "zip":
        # ---- Zipped bundle with records + users + rules + metadata ----
        all_records = storage.list_records(limit=10_000_000, offset=0)
        raw_users = user_manager.list_users()
        safe_users = [
            {
                "username": u["username"],
                "role": u["role"],
                "is_active": u["is_active"],
                "created_at": u.get("created_at")
            }
            for u in raw_users
        ]
        all_rules = storage.get_rules()
        metadata = {
            "export_time": datetime.now(timezone.utc).isoformat(),
            "record_count": len(all_records),
            "storage_engine": Config.STORAGE_ENGINE
        }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("records.json", json.dumps(all_records, indent=2))
            zf.writestr("users.json", json.dumps(safe_users, indent=2))
            zf.writestr("rules.json", json.dumps(all_rules, indent=2))
            zf.writestr("metadata.json", json.dumps(metadata, indent=2))
        buf.seek(0)

        return Response(
            buf.read(),
            mimetype="application/zip",
            headers={
                "Content-Disposition": "attachment; filename=priority_storage_export.zip"
            }
        )

    # ---- Default: stream all records as a JSON file (no limit) ----
    all_records = storage.list_records(limit=10_000_000, offset=0)
    data_json = json.dumps(all_records, indent=2)
    return Response(
        data_json,
        mimetype="application/json",
        headers={
            "Content-Disposition": "attachment; filename=telemetry_full_export.json"
        }
    )

# -----------------------------------------------------------------------------
# Dynamic Priority Rules Endpoints
# -----------------------------------------------------------------------------
@app.get("/api/rules")
@require_permission(Permission.RECORDS_READ)
def list_rules():
    return jsonify({"rules": storage.get_rules()})

@app.post("/api/rules")
@require_permission(Permission.RULES_MANAGE)
def create_or_update_rule():
    body = request.get_json(silent=True) or {}
    try:
        rule_dict = {
            "rule_id": body.get("rule_id"),
            "module_id": int(body["module_id"]),
            "sensor_index": int(body["sensor_index"]),
            "sensor_name": body.get("sensor_name", f"Sensor {body['sensor_index']}"),
            "operator": body.get("operator", ">="),
            "threshold": float(body["threshold"]),
            "priority": body.get("priority", "HIGH").upper(),
            "description": body.get("description", "")
        }
        rule_id = storage.save_rule(rule_dict)
        broadcast_event("rules_updated", {"rule_id": rule_id})
        return jsonify({"message": "Rule saved successfully", "rule_id": rule_id}), 201
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"error": f"Invalid rule payload: {exc}"}), 400

@app.delete("/api/rules/<rule_id>")
@require_permission(Permission.RULES_MANAGE)
def delete_rule(rule_id: str):
    success = storage.delete_rule(rule_id)
    if not success:
        return jsonify({"error": "Rule not found"}), 404
    broadcast_event("rules_updated", {"deleted_rule_id": rule_id})
    return jsonify({"message": f"Rule '{rule_id}' deleted successfully"})

# -----------------------------------------------------------------------------
# Hardware & Serial Ports Endpoints
# -----------------------------------------------------------------------------
@app.get("/api/ports")
def get_ports():
    discovered = []
    for p in list_ports.comports():
        discovered.append({
            "device": p.device,
            "description": p.description or "Serial device",
            "manufacturer": p.manufacturer,
            "hwid": p.hwid
        })
    return jsonify({
        "ports": discovered,
        "selected": serial_manager.get_selected_ports(),
        "connected": serial_manager.get_connected_ports(),
        "virtual_hardware_active": virtual_generator.is_running
    })

@app.post("/api/ports/connect")
@require_permission(Permission.PORTS_MANAGE)
def connect_ports():
    body = request.get_json(silent=True) or {}
    ports_to_connect = body.get("ports", [])
    errors = serial_manager.set_selected_ports(ports_to_connect)
    return jsonify({
        "selected": serial_manager.get_selected_ports(),
        "connected": serial_manager.get_connected_ports(),
        "errors": errors
    })

@app.post("/api/ports/disconnect")
@require_permission(Permission.PORTS_MANAGE)
def disconnect_ports():
    serial_manager.set_selected_ports([])
    return jsonify({"selected": [], "connected": []})

@app.post("/api/virtual-hardware/toggle")
@require_permission(Permission.PORTS_MANAGE)
def toggle_virtual_hardware():
    is_active = virtual_generator.toggle()
    broadcast_event("system_event", {
        "type": "VIRTUAL_HARDWARE_TOGGLED",
        "is_active": is_active
    })
    return jsonify({"virtual_hardware_active": is_active})

@app.post("/api/system/burst-demo")
@require_permission(Permission.RECORDS_CREATE)
def burst_demo():
    body = request.get_json(silent=True) or {}
    count = min(20, int(body.get("count", 5)))
    generated = 0
    for _ in range(count):
        try:
            virtual_generator.generate_single(on_telemetry_received)
            generated += 1
        except Exception:
            pass
    return jsonify({"message": f"Generated burst of {generated} telemetry records", "count": generated})

# -----------------------------------------------------------------------------
# System Status & Health Endpoints
# -----------------------------------------------------------------------------
@app.get("/api/system/status")
def get_system_status():
    if hasattr(storage, "get_status"):
        try:
            cpp_stat = storage.get_status()
            if cpp_stat.get("status") == "OK":
                return jsonify({
                    "storage_engine": cpp_stat.get("storage_engine", "JSON (C++ Engine)"),
                    "active_workers": cpp_stat.get("active_workers", 2),
                    "min_workers": Config.MIN_WORKERS,
                    "max_workers": Config.MAX_WORKERS,
                    "queue_depth": cpp_stat.get("queue_depth", 0),
                    "total_records": cpp_stat.get("total_records", 0),
                    "priority_distribution": cpp_stat.get("priority_counts", {}),
                    "connected_ports": serial_manager.get_connected_ports(),
                    "virtual_hardware_active": virtual_generator.is_running
                })
        except Exception:
            pass

    return jsonify({
        "storage_engine": Config.STORAGE_ENGINE,
        "active_workers": worker_pool.active_worker_count,
        "min_workers": Config.MIN_WORKERS,
        "max_workers": Config.MAX_WORKERS,
        "queue_depth": queue.qsize(),
        "total_records": storage.count_records(),
        "priority_distribution": storage.get_priority_counts(),
        "connected_ports": serial_manager.get_connected_ports(),
        "virtual_hardware_active": virtual_generator.is_running
    })

@app.get("/api/system/health")
def health_check():
    db_ok = storage.ping()
    return jsonify({
        "status": "healthy" if db_ok else "degraded",
        "storage_engine": Config.STORAGE_ENGINE,
        "database_connected": db_ok,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

if __name__ == "__main__":
    app.run(
        host=Config.FLASK_HOST,
        port=Config.FLASK_PORT,
        debug=False,
        threaded=True
    )
