import datetime
import functools
import jwt
from flask import request, jsonify
from backend.config import Config

class Role:
    SUPERADMIN = "SUPERADMIN"   # Full CRUDIRC + system management (root)
    ADMIN = "ADMIN"             # Administrative access
    OPERATOR = "OPERATOR"       # Operational access
    AUDITOR = "AUDITOR"         # Read-only access

class Permission:
    # Records Permissions (CRUDIRC model)
    RECORDS_READ = "records:read"
    RECORDS_CREATE = "records:create"
    RECORDS_UPDATE = "records:update"
    RECORDS_DELETE_SINGLE = "records:delete:single"
    RECORDS_DELETE_BATCH = "records:delete:batch"
    RECORDS_PURGE_ALL = "records:purge:all"

    # User / IAM Permissions
    USERS_READ = "users:read"
    USERS_CREATE = "users:create"
    USERS_UPDATE = "users:update"
    USERS_DELETE_SINGLE = "users:delete:single"
    USERS_DELETE_BATCH = "users:delete:batch"
    USERS_PURGE_ALL = "users:purge:all"
    USERS_INVOKE = "users:invoke"          # trigger re-auth / force logout
    USERS_REVOKE = "users:revoke"          # revoke individual permissions
    USERS_CUSTOMIZE = "users:customize"    # assign extra permissions / groups

    # Group Management
    GROUPS_MANAGE = "groups:manage"        # create/delete groups

    # System & Hardware
    PORTS_MANAGE = "system:ports"
    WORKERS_MANAGE = "system:workers"
    RULES_MANAGE = "system:rules"
    SYSTEM_EXPORT = "system:export"        # export full DB
    SYSTEM_CUSTOMIZE = "system:customize"  # change system settings

# All available permissions (for SUPERADMIN)
_ALL_PERMISSIONS = [
    Permission.RECORDS_READ, Permission.RECORDS_CREATE, Permission.RECORDS_UPDATE,
    Permission.RECORDS_DELETE_SINGLE, Permission.RECORDS_DELETE_BATCH, Permission.RECORDS_PURGE_ALL,
    Permission.USERS_READ, Permission.USERS_CREATE, Permission.USERS_UPDATE,
    Permission.USERS_DELETE_SINGLE, Permission.USERS_DELETE_BATCH, Permission.USERS_PURGE_ALL,
    Permission.USERS_INVOKE, Permission.USERS_REVOKE, Permission.USERS_CUSTOMIZE,
    Permission.GROUPS_MANAGE,
    Permission.PORTS_MANAGE, Permission.WORKERS_MANAGE, Permission.RULES_MANAGE,
    Permission.SYSTEM_EXPORT, Permission.SYSTEM_CUSTOMIZE,
]

# Role-to-Permissions Mapping (RBAC Matrix)
ROLE_PERMISSIONS = {
    Role.SUPERADMIN: list(_ALL_PERMISSIONS),
    Role.ADMIN: [
        Permission.RECORDS_READ, Permission.RECORDS_CREATE, Permission.RECORDS_UPDATE,
        Permission.RECORDS_DELETE_SINGLE, Permission.RECORDS_DELETE_BATCH, Permission.RECORDS_PURGE_ALL,
        Permission.USERS_READ, Permission.USERS_CREATE, Permission.USERS_UPDATE,
        Permission.USERS_DELETE_SINGLE, Permission.USERS_DELETE_BATCH, Permission.USERS_PURGE_ALL,
        Permission.PORTS_MANAGE, Permission.WORKERS_MANAGE, Permission.RULES_MANAGE
    ],
    Role.OPERATOR: [
        Permission.RECORDS_READ, Permission.RECORDS_CREATE, Permission.RECORDS_UPDATE,
        Permission.RECORDS_DELETE_SINGLE, Permission.RECORDS_DELETE_BATCH,
        Permission.PORTS_MANAGE, Permission.RULES_MANAGE
    ],
    Role.AUDITOR: [
        Permission.RECORDS_READ,
        Permission.USERS_READ
    ]
}


def create_jwt_token(
    username: str,
    role: str,
    extra_permissions: list = None,
    revoked_permissions: list = None,
    groups: list = None,
    effective_permissions: list = None,
) -> str:
    """
    Create a JWT token.

    If `effective_permissions` is provided, it is used directly (pre-computed by user_manager).
    Otherwise, effective permissions are computed from role + extra_permissions - revoked_permissions.
    """
    now = datetime.datetime.now(datetime.timezone.utc)

    if effective_permissions is not None:
        final_permissions = sorted(effective_permissions)
    else:
        extra = set(extra_permissions or [])
        revoked = set(revoked_permissions or [])
        base = set(ROLE_PERMISSIONS.get(role, []))
        final_permissions = sorted((base | extra) - revoked)

    payload = {
        "sub": username,
        "role": role,
        "permissions": final_permissions,
        "groups": groups or [],
        "iat": now,
        "exp": now + datetime.timedelta(hours=Config.JWT_EXPIRATION_HOURS)
    }
    return jwt.encode(payload, Config.JWT_SECRET, algorithm="HS256")


def verify_jwt_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, Config.JWT_SECRET, algorithms=["HS256"])
        return payload
    except (jwt.PyJWTError, Exception):
        return None


def extract_token_from_request() -> str | None:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    # Also support query parameter 'token' for WebSocket handshakes / downloads
    return request.args.get("token")


def require_auth(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        token = extract_token_from_request()
        if not token:
            return jsonify({"error": "Unauthorized: Missing authentication token"}), 401
        payload = verify_jwt_token(token)
        if not payload:
            return jsonify({"error": "Unauthorized: Invalid or expired token"}), 401
        request.current_user = payload
        return f(*args, **kwargs)
    return decorated


def require_permission(permission: str):
    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            token = extract_token_from_request()
            if not token:
                return jsonify({"error": "Unauthorized: Missing authentication token"}), 401
            payload = verify_jwt_token(token)
            if not payload:
                return jsonify({"error": "Unauthorized: Invalid or expired token"}), 401

            user_permissions = payload.get("permissions", [])
            if permission not in user_permissions:
                return jsonify({
                    "error": f"Forbidden: You lack permission '{permission}' for this action."
                }), 403

            request.current_user = payload
            return f(*args, **kwargs)
        return decorated
    return decorator
