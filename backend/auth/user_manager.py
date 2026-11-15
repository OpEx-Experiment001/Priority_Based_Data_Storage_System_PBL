import datetime
import threading
from werkzeug.security import generate_password_hash, check_password_hash
from tinydb import TinyDB, Query
from backend.config import Config
from backend.auth.iam import Role, ROLE_PERMISSIONS


class UserManager:
    def __init__(self, db_path=None):
        self._lock = threading.RLock()
        path = db_path or (Config.BASE_DIR / "data_store" / "users.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = TinyDB(path)
        self.users_table = self.db.table("users")
        self.groups_table = self.db.table("groups")
        self._ensure_root_admin()

    # -------------------------------------------------------------------------
    # Startup: seed first-run root admin only if no users exist
    # -------------------------------------------------------------------------
    def _ensure_root_admin(self):
        with self._lock:
            if len(self.users_table.all()) == 0:
                record = {
                    "username": "admin",
                    "password_hash": generate_password_hash(Config.ADMIN_DEFAULT_PASSWORD),
                    "role": Role.SUPERADMIN,
                    "is_active": True,
                    "is_root": True,
                    "extra_permissions": [],
                    "revoked_permissions": [],
                    "groups": [],
                    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }
                self.users_table.insert(record)

    # -------------------------------------------------------------------------
    # Authentication
    # -------------------------------------------------------------------------
    def authenticate(self, username, password) -> dict | None:
        with self._lock:
            q = Query()
            user = self.users_table.get(q.username == username)
            if not user:
                return None
            if not user.get("is_active", True):
                return None
            if check_password_hash(user["password_hash"], password):
                return {
                    "username": user["username"],
                    "role": user["role"],
                    "created_at": user.get("created_at"),
                    "extra_permissions": user.get("extra_permissions", []),
                    "revoked_permissions": user.get("revoked_permissions", []),
                    "groups": user.get("groups", []),
                    "is_root": user.get("is_root", False),
                }
            return None

    # -------------------------------------------------------------------------
    # User CRUD
    # -------------------------------------------------------------------------
    def create_user(self, username, password, role=Role.OPERATOR) -> dict:
        with self._lock:
            q = Query()
            username = username.strip().lower()
            if self.users_table.contains(q.username == username):
                raise ValueError(f"User '{username}' already exists")
            valid_roles = (Role.SUPERADMIN, Role.ADMIN, Role.OPERATOR, Role.AUDITOR)
            if role not in valid_roles:
                raise ValueError(f"Invalid role '{role}'")
            if len(password) < 6:
                raise ValueError("Password must be at least 6 characters")

            record = {
                "username": username,
                "password_hash": generate_password_hash(password),
                "role": role,
                "is_active": True,
                "is_root": False,
                "extra_permissions": [],
                "revoked_permissions": [],
                "groups": [],
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
            doc_id = self.users_table.insert(record)
            return {"id": doc_id, "username": username, "role": role, "is_active": True}

    def list_users(self) -> list[dict]:
        with self._lock:
            docs = self.users_table.all()
            result = []
            for d in docs:
                result.append({
                    "id": d.doc_id,
                    "username": d["username"],
                    "role": d["role"],
                    "is_active": d.get("is_active", True),
                    "is_root": d.get("is_root", False),
                    "extra_permissions": d.get("extra_permissions", []),
                    "revoked_permissions": d.get("revoked_permissions", []),
                    "groups": d.get("groups", []),
                    "created_at": d.get("created_at")
                })
            return result

    def get_user(self, username) -> dict | None:
        with self._lock:
            q = Query()
            user = self.users_table.get(q.username == username)
            if not user:
                return None
            return {
                "id": user.doc_id,
                "username": user["username"],
                "role": user["role"],
                "is_active": user.get("is_active", True),
                "is_root": user.get("is_root", False),
                "extra_permissions": user.get("extra_permissions", []),
                "revoked_permissions": user.get("revoked_permissions", []),
                "groups": user.get("groups", []),
                "created_at": user.get("created_at")
            }

    def update_user(self, username, role=None, is_active=None, password=None) -> bool:
        with self._lock:
            q = Query()
            updates = {}
            if role is not None:
                valid_roles = (Role.SUPERADMIN, Role.ADMIN, Role.OPERATOR, Role.AUDITOR)
                if role not in valid_roles:
                    raise ValueError(f"Invalid role: {role}")
                user = self.users_table.get(q.username == username)
                if user and user.get("is_root", False) and role != Role.SUPERADMIN:
                    raise ValueError("Cannot demote the root admin user")
                updates["role"] = role
            if is_active is not None:
                user = self.users_table.get(q.username == username)
                if user and user.get("is_root", False) and not is_active:
                    raise ValueError("Cannot deactivate the root admin user")
                updates["is_active"] = bool(is_active)
            if password:
                if len(password) < 6:
                    raise ValueError("Password must be at least 6 characters")
                updates["password_hash"] = generate_password_hash(password)

            if not updates:
                return True
            updated_ids = self.users_table.update(updates, q.username == username)
            return len(updated_ids) > 0

    def delete_user(self, username) -> bool:
        with self._lock:
            q = Query()
            user = self.users_table.get(q.username == username)
            if user and user.get("is_root", False):
                raise ValueError("Cannot delete the root admin user")
            removed = self.users_table.remove(q.username == username)
            return len(removed) > 0

    def batch_delete_users(self, usernames: list[str]) -> int:
        with self._lock:
            q = Query()
            count = 0
            for u in usernames:
                user = self.users_table.get(q.username == u)
                if user and not user.get("is_root", False):
                    removed = self.users_table.remove(q.username == u)
                    count += len(removed)
            return count

    def purge_users(self) -> int:
        with self._lock:
            q = Query()
            removed = self.users_table.remove(q.is_root != True)
            return len(removed)

    # -------------------------------------------------------------------------
    # Groups Management
    # -------------------------------------------------------------------------
    def create_group(self, name: str, permissions: list, created_by: str) -> dict:
        with self._lock:
            q = Query()
            if self.groups_table.contains(q.group_name == name):
                raise ValueError(f"Group '{name}' already exists")
            record = {
                "group_name": name,
                "permissions": permissions,
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "created_by": created_by
            }
            self.groups_table.insert(record)
            return record

    def delete_group(self, name: str) -> bool:
        with self._lock:
            q = Query()
            removed = self.groups_table.remove(q.group_name == name)
            if removed:
                # Remove group from all users
                docs = self.users_table.all()
                for d in docs:
                    groups = d.get("groups", [])
                    if name in groups:
                        updated = [g for g in groups if g != name]
                        self.users_table.update({"groups": updated}, doc_ids=[d.doc_id])
            return len(removed) > 0

    def list_groups(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "group_name": d["group_name"],
                    "permissions": d.get("permissions", []),
                    "created_at": d.get("created_at"),
                    "created_by": d.get("created_by")
                }
                for d in self.groups_table.all()
            ]

    def assign_user_to_group(self, username: str, group_name: str) -> bool:
        with self._lock:
            q = Query()
            user = self.users_table.get(q.username == username)
            if not user:
                return False
            group = self.groups_table.get(q.group_name == group_name)
            if not group:
                return False
            groups = user.get("groups", [])
            if group_name not in groups:
                groups.append(group_name)
                self.users_table.update({"groups": groups}, q.username == username)
            return True

    def remove_user_from_group(self, username: str, group_name: str) -> bool:
        with self._lock:
            q = Query()
            user = self.users_table.get(q.username == username)
            if not user:
                return False
            groups = user.get("groups", [])
            if group_name in groups:
                groups = [g for g in groups if g != group_name]
                self.users_table.update({"groups": groups}, q.username == username)
                return True
            return False

    # -------------------------------------------------------------------------
    # Per-user permission overrides
    # -------------------------------------------------------------------------
    def grant_permission(self, username: str, permission: str) -> bool:
        with self._lock:
            q = Query()
            user = self.users_table.get(q.username == username)
            if not user:
                return False
            extra = user.get("extra_permissions", [])
            if permission not in extra:
                extra.append(permission)
                self.users_table.update({"extra_permissions": extra}, q.username == username)
            # Also remove from revoked if present
            revoked = user.get("revoked_permissions", [])
            if permission in revoked:
                revoked = [p for p in revoked if p != permission]
                self.users_table.update({"revoked_permissions": revoked}, q.username == username)
            return True

    def revoke_permission(self, username: str, permission: str) -> bool:
        with self._lock:
            q = Query()
            user = self.users_table.get(q.username == username)
            if not user:
                return False
            revoked = user.get("revoked_permissions", [])
            if permission not in revoked:
                revoked.append(permission)
                self.users_table.update({"revoked_permissions": revoked}, q.username == username)
            # Also remove from extra if present
            extra = user.get("extra_permissions", [])
            if permission in extra:
                extra = [p for p in extra if p != permission]
                self.users_table.update({"extra_permissions": extra}, q.username == username)
            return True

    def get_effective_permissions(self, username: str) -> list[str]:
        with self._lock:
            q = Query()
            user = self.users_table.get(q.username == username)
            if not user:
                return []
            role = user.get("role", "")
            role_perms = set(ROLE_PERMISSIONS.get(role, []))
            extra_perms = set(user.get("extra_permissions", []))
            revoked_perms = set(user.get("revoked_permissions", []))

            # Collect group permissions
            group_perms: set[str] = set()
            for group_name in user.get("groups", []):
                grp = self.groups_table.get(q.group_name == group_name)
                if grp:
                    group_perms |= set(grp.get("permissions", []))

            effective = (role_perms | group_perms | extra_perms) - revoked_perms
            return sorted(effective)

    def count_users(self) -> int:
        with self._lock:
            return len(self.users_table.all())

    def close(self):
        with self._lock:
            self.db.close()
