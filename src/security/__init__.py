"""Security, 5-role RBAC authentication, and 4-hour incident hold package (SEC-01 to SEC-08)."""

from src.security.auth import (
    MAX_CONCURRENT_SESSIONS,
    VALID_ROLES,
    AuthContext,
    SessionRecord,
    SessionStore,
    resolve_auth_context,
)
from src.security.incident_hold import (
    HOLD_STALE_DATA,
    INCIDENT_NOTIFY_SLA_HOURS,
    activate_emergency_hold,
    commit_local_emergency_block,
    ensure_incident_tables,
    get_active_incident_hold,
    record_purchaser_notification,
    release_emergency_hold,
)
from src.security.rbac import (
    ROLE_PERMISSIONS,
    has_permission,
    is_rbac_enforced_on_legacy,
    is_rbac_enforced_on_v1,
    require_roles,
)

__all__ = [
    "AuthContext",
    "HOLD_STALE_DATA",
    "INCIDENT_NOTIFY_SLA_HOURS",
    "MAX_CONCURRENT_SESSIONS",
    "ROLE_PERMISSIONS",
    "SessionRecord",
    "SessionStore",
    "VALID_ROLES",
    "activate_emergency_hold",
    "commit_local_emergency_block",
    "ensure_incident_tables",
    "get_active_incident_hold",
    "has_permission",
    "is_rbac_enforced_on_legacy",
    "is_rbac_enforced_on_v1",
    "record_purchaser_notification",
    "release_emergency_hold",
    "require_roles",
    "resolve_auth_context",
]

