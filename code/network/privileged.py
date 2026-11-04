"""Privilege boundary client for INA v2 (unprivileged side).

The MCP server runs as the unprivileged 'ina' user. It never runs nft, tc or
hostapd_cli itself, and never touches the root-owned state, lock and audit
files. Instead, every operation is delegated to the single privileged command
allowed by sudoers:

    /etc/sudoers.d/ina:  ina ALL=(root) NOPASSWD: /usr/local/sbin/ina-netctl

:class:`NetctlService` has the same method surface as
:class:`inav2.network.service.NetworkService`, so the MCP tools call it the
same way. Each method runs, without a shell:

    sudo -n /usr/local/sbin/ina-netctl --json --role <role> <command> -- <args>

and turns the JSON envelope printed by 'ina-netctl --json' back into the
typed result model, or re-raises the same service error class (so the MCP
tools keep reporting "Denied" vs "Failed" correctly).

The command output comes back too: the envelope's 'commands' (argv, exit code,
stdout, stderr, duration of every nft/tc/hostapd_cli call, already written to
the root-side command log) is added to the current request trace
(network/command_log.py), on success and on error alike. The request ID the
MCP policy guard assigned is passed as '--request-id', so the policy audit,
the command log and the service audit share it.

The role passed is the one the MCP server's policy guard resolved from the
caller's token; it is never taken from the AI.

Environment:
    INA_NETCTL_COMMAND  command prefix (default: 'sudo -n /usr/local/sbin/ina-netctl')
    INA_EXECUTOR        'netctl' or 'direct' (default: 'direct' when running as
                        root, otherwise 'netctl')
"""

from __future__ import annotations

import json
import os
import shlex
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from inav2.mcp_server.schemas import (
    BanResult,
    BlockResult,
    KickResult,
    LimitResult,
    RemoveLimitResult,
    StatusResult,
    UnbanResult,
    UnblockResult,
)
from inav2.network.command_log import add_to_trace, current_request_id, new_request_id
from inav2.network.service import (
    ClientNotFoundError,
    INAError,
    KernelMutationError,
    LockTimeoutError,
    NetworkService,
    PolicyDeniedError,
    RateLimitedError,
    VerificationError,
    WirelessControlError,
)

CommandRunner = Callable[[List[str], float], Tuple[int, str, str]]

DEFAULT_NETCTL_COMMAND = "sudo -n /usr/local/sbin/ina-netctl"
NETCTL_TIMEOUT_S = 30.0

# Error classes ina-netctl may report; anything else is treated as a failure.
_ERRORS: Dict[str, Type[INAError]] = {
    cls.__name__: cls
    for cls in (
        INAError,
        PolicyDeniedError,
        RateLimitedError,
        ClientNotFoundError,
        WirelessControlError,
        LockTimeoutError,
        KernelMutationError,
        VerificationError,
    )
}


def _default_runner(args: List[str], timeout: float = NETCTL_TIMEOUT_S) -> Tuple[int, str, str]:
    from inav2.network.device_discovery import default_command_runner
    return default_command_runner(args, timeout)


def _build(model: Type[Any], data: Dict[str, Any]) -> Any:
    """Rebuild a result model (pydantic or the dataclass fallback) from its dict form."""
    if hasattr(model, "model_validate"):
        return model.model_validate(data)
    return model(**data)


class NetctlService:
    """Runs every network operation through 'sudo ina-netctl' (the privilege boundary)."""

    def __init__(
        self,
        command: Optional[str] = None,
        runner: Optional[CommandRunner] = None,
        timeout: float = NETCTL_TIMEOUT_S,
    ) -> None:
        self.command = shlex.split(
            command or os.environ.get("INA_NETCTL_COMMAND") or DEFAULT_NETCTL_COMMAND
        )
        self.runner = runner or _default_runner
        self.timeout = timeout

    def _invoke(self, role: str, command: str, positionals: List[str],
                options: Optional[List[str]] = None) -> Dict[str, Any]:
        request_id = current_request_id() or new_request_id()
        # '--' keeps client/target values from ever being parsed as options.
        argv = [*self.command, "--json", "--request-id", request_id, "--role", role,
                command, *(options or []), "--", *positionals]
        code, stdout, stderr = self.runner(argv, self.timeout)
        try:
            envelope = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else None
        except (json.JSONDecodeError, IndexError):
            envelope = None
        if not isinstance(envelope, dict) or "ok" not in envelope:
            detail = (stderr or stdout).strip() or f"exit code {code}"
            raise KernelMutationError(f"ina-netctl did not return a result ({detail}).")
        commands = envelope.get("commands")
        if isinstance(commands, list):
            add_to_trace(commands)
        if envelope["ok"]:
            return envelope.get("result") or {}
        error_cls = _ERRORS.get(str(envelope.get("error")), KernelMutationError)
        raise error_cls(str(envelope.get("message", "ina-netctl reported an error.")))

    # -- operations (same signatures as NetworkService) -------------------------
    def block(self, client: str, target: str, role: str = "operator") -> BlockResult:
        return _build(BlockResult, self._invoke(role, "block", [client, target]))

    def unblock(self, client: str, target: str, role: str = "operator") -> UnblockResult:
        return _build(UnblockResult, self._invoke(role, "unblock", [client, target]))

    def limit(self, client: str, download_kbit: int, upload_kbit: int,
              role: str = "operator") -> LimitResult:
        return _build(LimitResult, self._invoke(
            role, "limit", [client, str(int(download_kbit)), str(int(upload_kbit))]))

    def remove_limit(self, client: str, role: str = "operator") -> RemoveLimitResult:
        return _build(RemoveLimitResult, self._invoke(role, "unlimit", [client]))

    def kick(self, client: str, role: str = "operator") -> KickResult:
        return _build(KickResult, self._invoke(role, "kick", [client]))

    def ban(self, client: str, duration_minutes: Optional[int] = None,
            reason: Optional[str] = None, confirm: bool = False,
            role: str = "operator") -> BanResult:
        options: List[str] = []
        if duration_minutes is not None:
            options += ["--minutes", str(int(duration_minutes))]
        if reason is not None:
            options += ["--reason", reason]
        if confirm:
            options.append("--yes")
        return _build(BanResult, self._invoke(role, "ban", [client], options))

    def unban(self, client: str, role: str = "operator") -> UnbanResult:
        return _build(UnbanResult, self._invoke(role, "unban", [client]))

    def ban_owner_role(self, client: str) -> Optional[str]:
        # Read-only lookup in the root-owned state file; role does not affect it.
        return self._invoke("viewer", "ban-owner", [client]).get("banned_by_role")

    def status(self, client: Optional[str] = None, role: str = "viewer") -> StatusResult:
        return _build(StatusResult, self._invoke(role, "status", [client] if client else []))


def default_executor() -> Any:
    """The service the MCP tools should use in this process.

    Unprivileged processes (the deployed MCP server) go through ina-netctl.
    A process that is already root (ina-netctl itself, or a root dev shell)
    runs the service layer directly.
    """
    mode = os.environ.get("INA_EXECUTOR", "").strip().lower()
    if mode not in ("netctl", "direct"):
        mode = "direct" if os.geteuid() == 0 else "netctl"
    return NetctlService() if mode == "netctl" else NetworkService()
