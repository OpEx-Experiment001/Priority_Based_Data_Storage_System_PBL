"""
Sharded JSON Document Storage Engine.

Records are distributed across N shard files (default 8) based on
`record_id % SHARD_COUNT`. A global index file maps data_id -> shard_num
for O(1) targeted lookups. Rules and attempt logs remain in a separate
non-sharded meta file.

Directory layout:
  data_store/
    shards/
      index.json        <- global counter (_meta table) + data_id->shard_num mapping (_index table)
      shard_0.json ... shard_N-1.json
    storage_meta.json   <- rules_table + attempts_table (unchanged schema)
    storage.json        <- old monolithic file (migrated on first boot to storage.json.migrated)
"""

import datetime
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from tinydb import TinyDB, Query
from backend.storage.base import BaseStorage
from backend.config import Config

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SHARD_COUNT = int(os.getenv("SHARD_COUNT", "8"))


class JsonDocStorage(BaseStorage):
    """
    Lightweight, thread-safe, sharded NoSQL JSON Document Storage Engine.
    Requires zero external server installations; sandboxed portability retained.
    """

    # ------------------------------------------------------------------
    # Construction & initialisation
    # ------------------------------------------------------------------

    def __init__(self, db_path=None):
        """
        db_path: Path to the legacy monolithic storage.json.
                 When supplied the shards directory is placed next to it.
                 When omitted the Config defaults are used.
        """
        self._global_lock = threading.RLock()   # used for counter & index ops

        # Resolve base directory for shards and meta files
        legacy_path = Path(db_path) if db_path else Path(Config.JSON_STORAGE_PATH)
        base_dir = legacy_path.parent           # e.g. data_store/

        shard_dir = base_dir / "shards"
        shard_dir.mkdir(parents=True, exist_ok=True)

        # ---- open index db ----
        self._index_db = TinyDB(shard_dir / "index.json")
        self._meta_table = self._index_db.table("_meta")     # stores next_id counter
        self._index_table = self._index_db.table("_index")   # data_id -> shard_num

        # Seed counter if first run
        q = Query()
        if not self._meta_table.contains(q.key == "next_id"):
            self._meta_table.insert({"key": "next_id", "value": 1})

        # ---- open shard dbs ----
        self._shards: List[TinyDB] = []
        self._shard_tables = []          # TinyDB table objects for records
        self._shard_locks: List[threading.RLock] = []

        for i in range(SHARD_COUNT):
            db = TinyDB(shard_dir / f"shard_{i}.json")
            self._shards.append(db)
            self._shard_tables.append(db.table("records"))
            self._shard_locks.append(threading.RLock())

        # ---- open meta db (rules + attempts, NOT sharded) ----
        meta_path = base_dir / "storage_meta.json"
        self._meta_db = TinyDB(meta_path)
        self.rules_table = self._meta_db.table("rules")
        self.attempts_table = self._meta_db.table("attempts")

        # Seed default rules (idempotent)
        self._seed_default_rules()

        # Migrate legacy monolithic file if present
        self._migrate_legacy(legacy_path)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        """Atomically increment and return the next global record ID."""
        with self._global_lock:
            q = Query()
            rec = self._meta_table.get(q.key == "next_id")
            if not rec:
                self._meta_table.insert({"key": "next_id", "value": 2})
                return 1
            current = int(rec["value"])
            self._meta_table.update({"value": current + 1}, q.key == "next_id")
            return current

    def _shard_for_id(self, record_id: int) -> int:
        return record_id % SHARD_COUNT

    def _lookup_shard(self, data_id: str) -> Optional[int]:
        """Return the shard number for data_id, or fallback by numeric ID."""
        q = Query()
        with self._global_lock:
            entry = self._index_table.get(q.data_id == str(data_id))
        if entry:
            return int(entry["shard_num"])

        # Fallback: if data_id is numeric, calculate shard directly and verify
        try:
            val = int(data_id)
            candidate_shard = self._shard_for_id(val)
            q_doc = Query()
            with self._shard_locks[candidate_shard]:
                doc = self._shard_tables[candidate_shard].get(q_doc.data_id == str(data_id))
            if doc:
                with self._global_lock:
                    self._register_index(str(data_id), candidate_shard)
                return candidate_shard
        except (ValueError, TypeError):
            pass

        return None

    def _register_index(self, data_id: str, shard_num: int):
        q = Query()
        if not self._index_table.contains(q.data_id == str(data_id)):
            self._index_table.insert({"data_id": str(data_id), "shard_num": shard_num})

    def _remove_index(self, data_id: str):
        q = Query()
        self._index_table.remove(q.data_id == str(data_id))

    def _seed_default_rules(self):
        with self._global_lock:
            if len(self.rules_table) == 0:
                self.rules_table.insert_multiple([
                    {
                        "rule_id": "rule-gas-high",
                        "module_id": 101,
                        "sensor_index": 2,
                        "sensor_name": "Gas PPM",
                        "operator": ">=",
                        "threshold": 800.0,
                        "priority": "HIGH",
                        "description": "Hazardous gas spike alert"
                    },
                    {
                        "rule_id": "rule-gas-med",
                        "module_id": 101,
                        "sensor_index": 2,
                        "sensor_name": "Gas PPM",
                        "operator": ">=",
                        "threshold": 600.0,
                        "priority": "MEDIUM",
                        "description": "Elevated gas level warning"
                    },
                    {
                        "rule_id": "rule-motion-high",
                        "module_id": 102,
                        "sensor_index": 0,
                        "sensor_name": "Motion Value",
                        "operator": ">=",
                        "threshold": 90.0,
                        "priority": "HIGH",
                        "description": "Sudden perimeter intrusion"
                    }
                ])

    def _migrate_legacy(self, legacy_path: Path):
        """
        If the old monolithic storage.json exists and has a 'records' table,
        import every record into the sharded engine then rename the file.
        """
        if not legacy_path.exists():
            return
        if legacy_path.name in ("storage_meta.json", "index.json"):
            return
        migrated_marker = legacy_path.parent / f"{legacy_path.name}.migrated"
        if migrated_marker.exists():
            return

        try:
            old_db = TinyDB(legacy_path)
            old_records = old_db.table("records").all()
            old_rules = old_db.table("rules").all()
            old_attempts = old_db.table("attempts").all()
            old_db.close()
        except Exception:
            return

        # Migrate rules if meta table is empty
        if old_rules and len(self.rules_table) == 0:
            with self._global_lock:
                self.rules_table.insert_multiple(old_rules)

        # Migrate attempts if meta table is empty
        if old_attempts and len(self.attempts_table) == 0:
            with self._global_lock:
                self.attempts_table.insert_multiple(old_attempts)

        for old_rec in old_records:
            doc = dict(old_rec)
            doc.pop("data_id", None)
            self.persist_record(doc)

        try:
            legacy_path.rename(migrated_marker)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public API — Records
    # ------------------------------------------------------------------

    def persist_record(self, record: Dict[str, Any]) -> str:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        record_id = self._next_id()
        shard_num = self._shard_for_id(record_id)
        data_id_str = str(record_id)

        doc = {
            "data_id": data_id_str,
            "module_id": int(record["module_id"]),
            "source_port": str(record.get("source_port", "UNKNOWN")),
            "raw_message": str(record.get("raw_message", "")),
            "received_at": record.get("received_at", now_iso),
            "values": [float(v) for v in record.get("values", [])],
            "priority": str(record.get("priority", "LOW")),
            "status": "PENDING",
            "attempt_count": 0,
            "max_attempts": Config.MAX_ATTEMPTS,
            "last_error": None,
            "submitted_at": now_iso,
            "started_at": None,
            "completed_at": None,
            "next_retry_at": None
        }

        with self._shard_locks[shard_num]:
            self._shard_tables[shard_num].insert(doc)

        with self._global_lock:
            self._register_index(data_id_str, shard_num)

        record["data_id"] = data_id_str
        return data_id_str

    def get_record(self, data_id: str) -> Optional[Dict[str, Any]]:
        shard_num = self._lookup_shard(str(data_id))
        if shard_num is None:
            return None
        q = Query()
        with self._shard_locks[shard_num]:
            doc = self._shard_tables[shard_num].get(q.data_id == str(data_id))
        if doc:
            item = dict(doc)
            item["data_id"] = str(item.get("data_id") or doc.doc_id)
            return item
        return None

    def list_records(self, limit: int = 50, offset: int = 0,
                     status: Optional[str] = None,
                     priority: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Cursor-based cross-shard scan that avoids loading ALL records when a
        reasonable page is requested. Iterates shards in order, applies filters,
        and does server-side pagination.
        """
        results: List[Dict[str, Any]] = []
        skipped = 0

        for i in range(SHARD_COUNT):
            with self._shard_locks[i]:
                for doc in self._shard_tables[i]:
                    if status and doc.get("status") != status:
                        continue
                    if priority and doc.get("priority") != priority:
                        continue

                    if skipped < offset:
                        skipped += 1
                        continue

                    item = dict(doc)
                    item["data_id"] = str(item.get("data_id") or doc.doc_id)
                    results.append(item)
                    if len(results) >= limit:
                        return results
        return results

    def count_records(self, status: Optional[str] = None,
                      priority: Optional[str] = None) -> int:
        total = 0
        for i in range(SHARD_COUNT):
            with self._shard_locks[i]:
                if not status and not priority:
                    total += len(self._shard_tables[i])
                else:
                    for d in self._shard_tables[i]:
                        if status and d.get("status") != status:
                            continue
                        if priority and d.get("priority") != priority:
                            continue
                        total += 1
        return total

    def update_record_status(self, data_id: str, status: str,
                             attempt_count: Optional[int] = None,
                             error: Optional[str] = None,
                             started: bool = False,
                             completed: bool = False,
                             retry_at: Optional[str] = None) -> bool:
        shard_num = self._lookup_shard(str(data_id))
        if shard_num is None:
            return False

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        updates: Dict[str, Any] = {"status": status, "last_error": error}
        if attempt_count is not None:
            updates["attempt_count"] = attempt_count
        if started:
            updates["started_at"] = now_iso
        if completed:
            updates["completed_at"] = now_iso
        if retry_at is not None:
            updates["next_retry_at"] = retry_at

        q = Query()
        with self._shard_locks[shard_num]:
            updated_ids = self._shard_tables[shard_num].update(updates, q.data_id == str(data_id))
        return len(updated_ids) > 0

    def update_record(self, data_id: str, update_dict: Dict[str, Any]) -> bool:
        shard_num = self._lookup_shard(str(data_id))
        if shard_num is None:
            return False
        clean = {k: v for k, v in update_dict.items() if k not in ("data_id", "doc_id")}
        q = Query()
        try:
            with self._shard_locks[shard_num]:
                updated_ids = self._shard_tables[shard_num].update(clean, q.data_id == str(data_id))
            return len(updated_ids) > 0
        except Exception:
            return False

    def delete_record(self, data_id: str) -> bool:
        shard_num = self._lookup_shard(str(data_id))
        if shard_num is None:
            return False
        q = Query()
        try:
            with self._shard_locks[shard_num]:
                removed = self._shard_tables[shard_num].remove(q.data_id == str(data_id))
            if removed:
                with self._global_lock:
                    self._remove_index(str(data_id))
                return True
            return False
        except Exception:
            return False

    def batch_delete_records(self, data_ids: List[str]) -> int:
        count = 0
        for data_id in data_ids:
            if self.delete_record(str(data_id)):
                count += 1
        return count

    def batch_update_priority(self, data_ids: List[str], new_priority: str) -> int:
        if new_priority not in ("HIGH", "MEDIUM", "LOW"):
            raise ValueError("Invalid priority")
        count = 0
        for data_id in data_ids:
            shard_num = self._lookup_shard(str(data_id))
            if shard_num is None:
                continue
            q = Query()
            with self._shard_locks[shard_num]:
                result = self._shard_tables[shard_num].update(
                    {"priority": new_priority}, q.data_id == str(data_id)
                )
            if result:
                count += len(result)
        return count

    def purge_records(self, status: Optional[str] = None) -> int:
        total = 0
        for i in range(SHARD_COUNT):
            with self._shard_locks[i]:
                if status:
                    q = Query()
                    removed = self._shard_tables[i].remove(q.status == status)
                    total += len(removed)
                else:
                    total += len(self._shard_tables[i])
                    self._shard_tables[i].truncate()

        # Clean index
        with self._global_lock:
            if status:
                all_entries = self._index_table.all()
                for entry in all_entries:
                    did = entry["data_id"]
                    sn = int(entry["shard_num"])
                    q2 = Query()
                    with self._shard_locks[sn]:
                        still_there = self._shard_tables[sn].get(q2.data_id == did)
                    if not still_there:
                        q3 = Query()
                        self._index_table.remove(q3.data_id == did)
            else:
                self._index_table.truncate()
        return total

    def fetch_pending_records(self, limit: int = 100) -> List[Dict[str, Any]]:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        pending: List[Dict[str, Any]] = []

        for i in range(SHARD_COUNT):
            with self._shard_locks[i]:
                for d in self._shard_tables[i]:
                    st = d.get("status")
                    if st in ("PENDING", "RETRY_WAIT"):
                        retry_at = d.get("next_retry_at")
                        if not retry_at or retry_at <= now_iso:
                            item = dict(d)
                            item["data_id"] = str(item.get("data_id") or d.doc_id)
                            pending.append(item)

        rank_map = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        pending.sort(key=lambda x: (
            rank_map.get(x.get("priority"), 2),
            int(x.get("data_id", 0)) if str(x.get("data_id", "")).isdigit() else 0
        ))
        return pending[:limit]

    def get_priority_counts(self) -> Dict[str, int]:
        counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for i in range(SHARD_COUNT):
            with self._shard_locks[i]:
                for d in self._shard_tables[i]:
                    p = d.get("priority", "LOW")
                    if p in counts:
                        counts[p] += 1
                    else:
                        counts["LOW"] += 1
        return counts

    # ------------------------------------------------------------------
    # Public API — Attempts (non-sharded)
    # ------------------------------------------------------------------

    def log_attempt(self, data_id: str, attempt_number: int, outcome: str,
                    error_message: Optional[str], started_at: str, ended_at: str):
        with self._global_lock:
            self.attempts_table.insert({
                "data_id": str(data_id),
                "attempt_number": attempt_number,
                "outcome": outcome,
                "error_message": error_message,
                "started_at": started_at,
                "ended_at": ended_at
            })

    # ------------------------------------------------------------------
    # Public API — Rules (non-sharded)
    # ------------------------------------------------------------------

    def get_rules(self) -> List[Dict[str, Any]]:
        with self._global_lock:
            docs = self.rules_table.all()
            for d in docs:
                d["id"] = d.get("rule_id", str(d.doc_id))
            return docs

    def save_rule(self, rule_dict: Dict[str, Any]) -> str:
        with self._global_lock:
            rule_id = rule_dict.get("rule_id") or f"rule-{int(datetime.datetime.now().timestamp())}"
            rule_dict["rule_id"] = rule_id
            q = Query()
            existing = self.rules_table.get(q.rule_id == rule_id)
            if existing:
                self.rules_table.update(rule_dict, doc_ids=[existing.doc_id])
            else:
                self.rules_table.insert(rule_dict)
            return rule_id

    def delete_rule(self, rule_id: str) -> bool:
        with self._global_lock:
            q = Query()
            removed = self.rules_table.remove(q.rule_id == rule_id)
            return len(removed) > 0

    # ------------------------------------------------------------------
    # Health & lifecycle
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        return True

    def close(self):
        with self._global_lock:
            for db in self._shards:
                try:
                    db.close()
                except Exception:
                    pass
            try:
                self._index_db.close()
            except Exception:
                pass
            try:
                self._meta_db.close()
            except Exception:
                pass
