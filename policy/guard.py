"""The single entry point the MCP server calls before executing a tool.

    guard = PolicyGuard.from_config()               # at server start-up
    ...
    result = guard.check(token, tool, arguments, request_id, context)
    if not result.allowed:
        return denied_tool_result(result.decision)  # nothing is changed
    ...apply the change via NetworkService using the validated values...
    guard.record_outcome(result, "success", detail, verification_passed=True)

What ``check`` does: token -> role (never from the AI) -> policy engine -> write
the decision to the audit log. It fails closed: if anything unexpected goes
wrong, or the audit log cannot be written, the answer is DENY.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Union

from .audit import AuditError, AuditLog
from .authorization import Principal, TokenAuthenticator
from .engine import PolicyEngine
from .models import Device, PolicyDecision, R_AUDIT, R_INTERNAL, R_TOKEN, deny


@dataclass(frozen=True)
class GuardResult:
    request_id: str
    decision: PolicyDecision
    principal: Optional[Principal] = None
    tool: Optional[str] = None               # canonical tool name
    device: Optional[Device] = None          # resolved client (name, mac, ip, class)
    target: Optional[str] = None             # validated block/unblock target
    params: Mapping[str, Any] = field(default_factory=dict)  # validated kbit / minutes / reason

    @property
    def allowed(self) -> bool:
        return self.decision.allowed

    @property
    def role(self) -> Optional[str]:
        return self.principal.role if self.principal else None


class PolicyGuard:
    def __init__(self, engine: PolicyEngine, authenticator: TokenAuthenticator,
                 audit: AuditLog) -> None:
        self.engine = engine
        self.authenticator = authenticator
        self.audit = audit

    @classmethod
    def from_config(cls, policy_dir: Union[str, Path, None] = None,
                    audit_path: Union[str, Path, None] = None,
                    env: Optional[Mapping[str, str]] = None) -> "PolicyGuard":
        return cls(PolicyEngine.from_files(policy_dir),
                   TokenAuthenticator.from_env(env),
                   AuditLog(audit_path))

    def check(self, token: Any, tool: Any, arguments: Any,
              request_id: Optional[str] = None,
              context: Optional[Mapping[str, Any]] = None) -> GuardResult:
        """Authenticate, evaluate and audit one proposed tool call.

        ``context`` is trusted server-side data (see PolicyEngine.evaluate),
        e.g. ``{"banned_by_role": "operator"}`` for unban_client. Never pass
        anything the AI produced in it.
        """
        rid = request_id or uuid.uuid4().hex[:12]
        principal = self.authenticator.authenticate(token)
        label = principal.label if principal else None

        if principal is None:
            result = GuardResult(rid, deny(R_TOKEN, "Unknown or missing role token."))
        else:
            try:
                ev = self.engine.evaluate(principal.role, tool, arguments, context)
                result = GuardResult(rid, ev.decision, principal, ev.tool, ev.device,
                                     ev.target, dict(ev.params) if ev.allowed else {})
            except Exception:  # noqa: BLE001 - fail closed on any engine bug
                result = GuardResult(rid, deny(R_INTERNAL, "The policy check failed; "
                                                           "request denied."), principal)

        try:
            self.audit.log_decision(rid, label, tool, arguments, result.decision,
                                    result.device.name if result.device else None,
                                    result.target)
        except AuditError:
            # No audit record, no action.
            return GuardResult(rid, deny(R_AUDIT, "Audit log unavailable; request denied."),
                               principal)
        return result

    def record_outcome(self, result: GuardResult, status: str, detail: str = "",
                       verification_passed: Optional[bool] = None) -> None:
        """Log what actually happened. Call once per allowed request after executing.

        status: "success", "preview" (ban_client without confirm), or "failure".
        """
        self.audit.log_outcome(result.request_id, status, detail, verification_passed)

    def tools_for_token(self, token: Any) -> list:
        """MCP tools to advertise to this caller (empty for a bad token)."""
        principal = self.authenticator.authenticate(token)
        return self.engine.tools_for_role(principal.role) if principal else []
