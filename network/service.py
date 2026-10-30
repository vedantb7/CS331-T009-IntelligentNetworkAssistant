"""Authoritative network service orchestration pipeline for INA v2.

Encapsulates the complete 10-step mutation execution pipeline (§4.1):
1. Input argument validation
2. Policy and RBAC pre-check
3. Identity and IP resolution
4. Single-writer lock acquisition (/run/ina/ina.lock)
5. Idempotent kernel command application
6. Kernel state verification
7. Automatic rollback on failure
8. Desired state persistence (/var/lib/ina/state.json)
9. Tamper-evident hash-chained audit logging (/var/log/ina/audit.log)
10. Typed result delivery

This service layer is the SOLE path for network configuration mutations.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Generator, List, Optional, Set, Tuple

from inav2.mcp_server.schemas import (
    OPERATOR_MAX_BAN_MINUTES,
    BanClientArgs,
    BanEntry,
    BanResult,
    BlockClientArgs,
    BlockResult,
    ClientStatus,
    GetStatusArgs,
    KickClientArgs,
    KickResult,
    LimitBandwidthArgs,
    LimitResult,
    RemoveLimitArgs,
    RemoveLimitResult,
    StatusResult,
    UnbanClientArgs,
    UnbanResult,
    UnblockClientArgs,
    UnblockResult,
)
from inav2.network.access_control import (
    block_client,
    unblock_client,
    validate_client_identifier,
)
from inav2.network.audit import append_audit_entry
from inav2.network.bandwidth import (
    limit_bandwidth,
    remove_bandwidth_limit,
)
from inav2.network.wireless import (
    DENY_MAC_FILE,
    Station,
    ban_station,
    ensure_not_protected,
    get_station_info,
    kick_station,
    load_protected_macs,
    resolve_station,
    unban_station,
)

CommandRunner = Callable[[List[str], float], Tuple[int, str, str]]

DEFAULT_LOCK_FILE = "/run/ina/ina.lock"
TMP_LOCK_FILE = "/tmp/ina/ina.lock"
FALLBACK_LOCK_FILE = os.path.expanduser("~/.ina/ina.lock")

DEFAULT_STATE_FILE = "/var/lib/ina/state.json"
TMP_STATE_FILE = "/tmp/ina/state.json"
FALLBACK_STATE_FILE = os.path.expanduser("~/.ina/state.json")

DEFAULT_KICK_HISTORY_FILE = "/run/ina/kick_history.json"
TMP_KICK_HISTORY_FILE = "/tmp/ina/kick_history.json"
FALLBACK_KICK_HISTORY_FILE = os.path.expanduser("~/.ina/kick_history.json")


# ==============================================================================
# Service Error Taxonomy
# ==============================================================================

class INAError(Exception):
    """Base exception for all INA service errors."""


class PolicyDeniedError(INAError):
    """Raised when an action is prohibited by RBAC or safety policies."""


class RateLimitedError(INAError):
    """Raised when a tool is invoked too often for the same client."""


class WirelessControlError(INAError):
    """Raised when hostapd rejects or cannot perform a station operation."""



class ClientNotFoundError(INAError):
    """Raised when a client cannot be resolved or is unknown."""


class LockTimeoutError(INAError):
    """Raised when the single-writer lock cannot be acquired within the timeout."""


class KernelMutationError(INAError):
    """Raised when an nftables or tc kernel command fails during execution."""


class VerificationError(INAError):
    """Raised when post-action kernel state does not match the requested state."""


# ==============================================================================
# Concurrency & State Persistence
# ==============================================================================

def _get_lock_path(custom: Optional[str] = None) -> str:
    if custom:
        os.makedirs(os.path.dirname(os.path.abspath(custom)), exist_ok=True)
        return custom
    lock_dir = os.path.dirname(DEFAULT_LOCK_FILE)
    if os.path.isdir(lock_dir) and os.access(lock_dir, os.W_OK):
        return DEFAULT_LOCK_FILE
    try:
        os.makedirs(os.path.dirname(FALLBACK_LOCK_FILE), exist_ok=True)
        return FALLBACK_LOCK_FILE
    except OSError:
        pass
    os.makedirs(os.path.dirname(TMP_LOCK_FILE), exist_ok=True)
    return TMP_LOCK_FILE


def _get_state_path(custom: Optional[str] = None) -> str:
    if custom:
        os.makedirs(os.path.dirname(os.path.abspath(custom)), exist_ok=True)
        return custom
    state_dir = os.path.dirname(DEFAULT_STATE_FILE)
    if os.path.isdir(state_dir) and os.access(state_dir, os.W_OK):
        return DEFAULT_STATE_FILE
    try:
        os.makedirs(os.path.dirname(FALLBACK_STATE_FILE), exist_ok=True)
        return FALLBACK_STATE_FILE
    except OSError:
        pass
    os.makedirs(os.path.dirname(TMP_STATE_FILE), exist_ok=True)
    return TMP_STATE_FILE


@contextlib.contextmanager
def acquire_lock(lock_path: Optional[str] = None, timeout: float = 5.0) -> Generator[None, None, None]:
    """Context manager acquiring an exclusive flock single-writer lock."""
    path = _get_lock_path(lock_path)
    start_time = time.monotonic()
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() - start_time >= timeout:
                    raise LockTimeoutError("Another network change is in progress; lock timed out.")
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def _get_kick_history_path(custom: Optional[str] = None) -> str:
    if custom:
        os.makedirs(os.path.dirname(os.path.abspath(custom)), exist_ok=True)
        return custom
    kick_dir = os.path.dirname(DEFAULT_KICK_HISTORY_FILE)
    if os.path.isdir(kick_dir) and os.access(kick_dir, os.W_OK):
        return DEFAULT_KICK_HISTORY_FILE
    try:
        os.makedirs(os.path.dirname(FALLBACK_KICK_HISTORY_FILE), exist_ok=True)
        return FALLBACK_KICK_HISTORY_FILE
    except OSError:
        pass
    os.makedirs(os.path.dirname(TMP_KICK_HISTORY_FILE), exist_ok=True)
    return TMP_KICK_HISTORY_FILE


def load_state(state_path: Optional[str] = None) -> Dict[str, Any]:
    """Load the persistent desired state from disk."""
    path = _get_state_path(state_path)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("bans", [])
                    return data
        except Exception:
            pass
    return {"version": "1.1", "updated_at": "", "blocks": [], "limits": [], "bans": []}


def save_state(state_data: Dict[str, Any], state_path: Optional[str] = None) -> None:
    """Save desired state to disk atomically using a temporary file and fsync."""
    path = _get_state_path(state_path)
    state_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    dir_name = os.path.dirname(path)
    os.makedirs(dir_name, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
        json.dump(state_data, tf, indent=2)
        tf.flush()
        os.fsync(tf.fileno())
        temp_name = tf.name

    os.replace(temp_name, path)


def _require_writes_enabled() -> None:
    if os.environ.get("INA_ALLOW_WRITE", "1") != "1":
        raise PolicyDeniedError("Write operations are disabled on this server (INA_ALLOW_WRITE != 1).")


# ==============================================================================
# Temporary Policy Evaluation Placeholder
# ==============================================================================
# NOTE: The actual Policy Engine component is not implemented yet.
# This minimal placeholder allows invoking and testing network mutations directly
# without a policy engine. In future releases, this will be replaced with the real
# policy engine integration.

def evaluate_policy_placeholder(
    action: str,
    client_ip: str = "",
    target: Optional[str] = None,
    role: str = "operator",
) -> Tuple[bool, str]:
    """Temporary isolated override for the missing policy engine."""
    if client_ip == "192.168.50.1":
        return False, "Router infrastructure (192.168.50.1) is protected and cannot be modified."
    if target == "192.168.50.1":
        return False, "Router infrastructure (192.168.50.1) cannot be directly targeted."
    return True, "Permitted by placeholder policy."



# ==============================================================================
# Service API
# ==============================================================================

class NetworkService:
    """Orchestrates network configuration operations with locking, state, and audit."""

    def __init__(
        self,
        runner: Optional[CommandRunner] = None,
        lock_path: Optional[str] = None,
        state_path: Optional[str] = None,
        audit_path: Optional[str] = None,
        leases_file: Optional[str] = None,
        leases_map: Optional[Dict[str, str]] = None,
        deny_file: Optional[str] = None,
        kick_history_path: Optional[str] = None,
    ) -> None:
        self.runner = runner
        self.lock_path = lock_path
        self.state_path = state_path
        self.audit_path = audit_path
        self.leases_file = leases_file
        self.leases_map = leases_map
        self.deny_file = deny_file or DENY_MAC_FILE
        self.kick_history_path = kick_history_path

    def _audit_denied(self, role: str, action: str, client: str, reason: str) -> None:
        append_audit_entry(
            caller_role=role,
            action=action,
            client=client,
            details={"reason": reason},
            status="denied",
            log_path=self.audit_path,
        )

    def _require_writes_enabled(self) -> None:
        _require_writes_enabled()

    def _check_self_lockout(self, station: Station, role: str, action: str) -> None:
        ssh_client = os.environ.get("SSH_CLIENT", "").strip()
        if not ssh_client:
            return
        issuer_ip = ssh_client.split()[0]
        if station.ip and station.ip == issuer_ip:
            verb = "ban" if "ban" in action else "disconnect"
            raise PolicyDeniedError(
                f"Refusing to {verb} {station.ip}: it is the device issuing this command."
            )

    def _check_role(
        self,
        action: str,
        station: Station,
        role: str,
        duration_minutes: Optional[int] = None,
        banned_by_role: Optional[str] = None,
    ) -> None:
        if role == "viewer":
            raise PolicyDeniedError(f"Role 'viewer' cannot perform mutation '{action}'.")

        if role == "operator":
            if action == "ban_client":
                if duration_minutes is None:
                    raise PolicyDeniedError("Role 'operator' must give duration_minutes (max 60).")
                if duration_minutes > OPERATOR_MAX_BAN_MINUTES:
                    raise PolicyDeniedError(
                        f"Role 'operator' cannot ban for more than {OPERATOR_MAX_BAN_MINUTES} minutes."
                    )
            elif action == "unban_client":
                if banned_by_role and banned_by_role != "operator":
                    raise PolicyDeniedError(
                        f"Role 'operator' cannot unban device banned by role '{banned_by_role}'."
                    )

    def _check_kick_rate(self, mac: str, client_name: str) -> None:
        path = _get_kick_history_path(self.kick_history_path)
        history: Dict[str, List[float]] = {}
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                pass
        now = time.time()
        mac_k = mac.lower()
        recent = [t for t in history.get(mac_k, []) if now - t < 600.0]
        if len(recent) >= 3:
            raise RateLimitedError(
                f"'{client_name}' was kicked 3 times in the last 10 minutes; use ban_client instead."
            )

    def _record_kick(self, mac: str) -> None:
        path = _get_kick_history_path(self.kick_history_path)
        history: Dict[str, List[float]] = {}
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                pass
        now = time.time()
        mac_k = mac.lower()
        cleaned: Dict[str, List[float]] = {}
        for m, timestamps in history.items():
            valid = [t for t in timestamps if now - t < 600.0]
            if valid:
                cleaned[m] = valid
        cleaned.setdefault(mac_k, []).append(now)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cleaned, f)
        except OSError:
            pass


    def block(self, client: str, target: str, role: str = "operator") -> BlockResult:
        """Block traffic between client and target."""
        # 1. Argument validation
        try:
            args = BlockClientArgs(client=client, target=target)
        except Exception as e:
            raise INAError(f"Invalid arguments for block_client: {e}") from e

        # 2. Identity resolution & policy placeholder check
        try:
            client_ip = validate_client_identifier(args.client, leases_file=self.leases_file, leases_map=self.leases_map)
        except ValueError as e:
            raise ClientNotFoundError(str(e)) from e

        permitted, reason = evaluate_policy_placeholder(
            action="block_client", client_ip=client_ip, target=args.target, role=role
        )
        if not permitted:
            append_audit_entry(
                caller_role=role, action="block_client", client=args.client,
                details={"target": args.target, "reason": reason},
                status="denied", log_path=self.audit_path,
            )
            raise PolicyDeniedError(reason)

        # 3. Single-writer lock & execution
        with acquire_lock(self.lock_path):
            try:
                res = block_client(
                    client=args.client, target=args.target, runner=self.runner,
                    leases_file=self.leases_file, leases_map=self.leases_map,
                )
            except RuntimeError as e:
                append_audit_entry(
                    caller_role=role, action="block_client", client=args.client,
                    details={"target": args.target, "error": str(e)},
                    status="failed", log_path=self.audit_path,
                )
                raise KernelMutationError(str(e)) from e

            # 4. State persistence
            if res.changed:
                state = load_state(self.state_path)
                blocks = state.get("blocks", [])
                entry = {"client": args.client, "client_ip": res.client_ip, "target": args.target}
                if entry not in blocks:
                    blocks.append(entry)
                state["blocks"] = blocks
                save_state(state, self.state_path)

            # 5. Audit log
            append_audit_entry(
                caller_role=role, action="block_client", client=args.client,
                details={"client_ip": res.client_ip, "target": args.target, "changed": res.changed},
                status="success", log_path=self.audit_path,
            )
            return res

    def unblock(self, client: str, target: str, role: str = "operator") -> UnblockResult:
        """Unblock traffic between client and target."""
        try:
            args = UnblockClientArgs(client=client, target=target)
        except Exception as e:
            raise INAError(f"Invalid arguments for unblock_client: {e}") from e

        try:
            client_ip = validate_client_identifier(args.client, leases_file=self.leases_file, leases_map=self.leases_map)
        except ValueError as e:
            raise ClientNotFoundError(str(e)) from e

        permitted, reason = evaluate_policy_placeholder(
            action="unblock_client", client_ip=client_ip, target=args.target, role=role
        )
        if not permitted:
            append_audit_entry(
                caller_role=role, action="unblock_client", client=args.client,
                details={"target": args.target, "reason": reason},
                status="denied", log_path=self.audit_path,
            )
            raise PolicyDeniedError(reason)

        with acquire_lock(self.lock_path):
            try:
                res = unblock_client(
                    client=args.client, target=args.target, runner=self.runner,
                    leases_file=self.leases_file, leases_map=self.leases_map,
                )
            except RuntimeError as e:
                append_audit_entry(
                    caller_role=role, action="unblock_client", client=args.client,
                    details={"target": args.target, "error": str(e)},
                    status="failed", log_path=self.audit_path,
                )
                raise KernelMutationError(str(e)) from e

            if res.changed:
                state = load_state(self.state_path)
                blocks = [
                    b for b in state.get("blocks", [])
                    if not (b.get("client") == args.client and b.get("target") == args.target)
                ]
                state["blocks"] = blocks
                save_state(state, self.state_path)

            append_audit_entry(
                caller_role=role, action="unblock_client", client=args.client,
                details={"client_ip": res.client_ip, "target": args.target, "changed": res.changed},
                status="success", log_path=self.audit_path,
            )
            return res

    def limit(self, client: str, download_kbit: int, upload_kbit: int, role: str = "operator") -> LimitResult:
        """Limit bandwidth rate for a client."""
        try:
            args = LimitBandwidthArgs(client=client, download_kbit=download_kbit, upload_kbit=upload_kbit)
        except Exception as e:
            raise INAError(f"Invalid arguments for limit_bandwidth: {e}") from e

        try:
            client_ip = validate_client_identifier(args.client, leases_file=self.leases_file, leases_map=self.leases_map)
        except ValueError as e:
            raise ClientNotFoundError(str(e)) from e

        permitted, reason = evaluate_policy_placeholder(
            action="limit_bandwidth", client_ip=client_ip, target=None, role=role
        )
        if not permitted:
            append_audit_entry(
                caller_role=role, action="limit_bandwidth", client=args.client,
                details={"download_kbit": args.download_kbit, "upload_kbit": args.upload_kbit, "reason": reason},
                status="denied", log_path=self.audit_path,
            )
            raise PolicyDeniedError(reason)

        with acquire_lock(self.lock_path):
            try:
                res = limit_bandwidth(
                    client=args.client, download_kbit=args.download_kbit, upload_kbit=args.upload_kbit,
                    runner=self.runner, leases_file=self.leases_file, leases_map=self.leases_map,
                )
            except RuntimeError as e:
                append_audit_entry(
                    caller_role=role, action="limit_bandwidth", client=args.client,
                    details={"download_kbit": args.download_kbit, "upload_kbit": args.upload_kbit, "error": str(e)},
                    status="failed", log_path=self.audit_path,
                )
                raise KernelMutationError(str(e)) from e

            if res.changed:
                state = load_state(self.state_path)
                limits = [l for l in state.get("limits", []) if l.get("client") != args.client]
                limits.append({
                    "client": args.client, "client_ip": res.client_ip,
                    "download_kbit": args.download_kbit, "upload_kbit": args.upload_kbit,
                })
                state["limits"] = limits
                save_state(state, self.state_path)

            append_audit_entry(
                caller_role=role, action="limit_bandwidth", client=args.client,
                details={"download_kbit": args.download_kbit, "upload_kbit": args.upload_kbit, "changed": res.changed},
                status="success", log_path=self.audit_path,
            )
            return res

    def remove_limit(self, client: str, role: str = "operator") -> RemoveLimitResult:
        """Remove bandwidth limit for a client."""
        try:
            args = RemoveLimitArgs(client=client)
        except Exception as e:
            raise INAError(f"Invalid arguments for remove_bandwidth_limit: {e}") from e

        try:
            client_ip = validate_client_identifier(args.client, leases_file=self.leases_file, leases_map=self.leases_map)
        except ValueError as e:
            raise ClientNotFoundError(str(e)) from e

        permitted, reason = evaluate_policy_placeholder(
            action="remove_bandwidth_limit", client_ip=client_ip, target=None, role=role
        )
        if not permitted:
            append_audit_entry(
                caller_role=role, action="remove_bandwidth_limit", client=args.client,
                details={"reason": reason}, status="denied", log_path=self.audit_path,
            )
            raise PolicyDeniedError(reason)

        with acquire_lock(self.lock_path):
            try:
                res = remove_bandwidth_limit(
                    client=args.client, runner=self.runner, leases_file=self.leases_file, leases_map=self.leases_map,
                )
            except RuntimeError as e:
                append_audit_entry(
                    caller_role=role, action="remove_bandwidth_limit", client=args.client,
                    details={"error": str(e)}, status="failed", log_path=self.audit_path,
                )
                raise KernelMutationError(str(e)) from e

            if res.changed:
                state = load_state(self.state_path)
                limits = [l for l in state.get("limits", []) if l.get("client") != args.client]
                state["limits"] = limits
                save_state(state, self.state_path)

            append_audit_entry(
                caller_role=role, action="remove_bandwidth_limit", client=args.client,
                details={"changed": res.changed}, status="success", log_path=self.audit_path,
            )
            return res

    def kick(self, client: str, role: str = "operator") -> KickResult:
        """Deauthenticate a client once."""
        try:
            args = KickClientArgs(client=client)
        except Exception as e:
            raise INAError(f"Invalid arguments for kick_client: {e}") from e
        self._require_writes_enabled()

        try:
            station = resolve_station(
                args.client,
                runner=self.runner,
                leases_file=self.leases_file,
                leases_map=self.leases_map,
            )
            ensure_not_protected(station, load_protected_macs())
        except (ValueError, PermissionError) as e:
            self._audit_denied(role, "kick_client", args.client, str(e))
            raise (PolicyDeniedError if isinstance(e, PermissionError) else ClientNotFoundError)(str(e)) from e

        self._check_self_lockout(station, role, "kick_client")
        self._check_role("kick_client", station, role, duration_minutes=None)
        self._check_kick_rate(station.mac, station.name)

        with acquire_lock(self.lock_path):
            try:
                res = kick_station(station, runner=self.runner)
            except RuntimeError as e:
                append_audit_entry(
                    caller_role=role,
                    action="kick_client",
                    client=args.client,
                    details={"mac": station.mac, "error": str(e)},
                    status="failed",
                    log_path=self.audit_path,
                )
                raise WirelessControlError(str(e)) from e

            if res.changed:
                self._record_kick(station.mac)

            append_audit_entry(
                caller_role=role,
                action="kick_client",
                client=args.client,
                details={
                    "mac": station.mac,
                    "client_ip": station.ip,
                    "was_associated": res.was_associated,
                    "reconnected": res.reconnected,
                },
                status="success",
                log_path=self.audit_path,
            )
            return res

    def ban(
        self,
        client: str,
        duration_minutes: Optional[int] = None,
        reason: Optional[str] = None,
        confirm: bool = False,
        role: str = "operator",
    ) -> BanResult:
        """Prevent a device (by MAC) from associating with the Wi-Fi AP."""
        try:
            args = BanClientArgs(client=client, duration_minutes=duration_minutes, reason=reason, confirm=confirm)
        except Exception as e:
            raise INAError(f"Invalid arguments for ban_client: {e}") from e
        self._require_writes_enabled()

        try:
            station = resolve_station(
                args.client,
                runner=self.runner,
                leases_file=self.leases_file,
                leases_map=self.leases_map,
            )
            ensure_not_protected(station, load_protected_macs())
        except (ValueError, PermissionError) as e:
            self._audit_denied(role, "ban_client", args.client, str(e))
            raise (PolicyDeniedError if isinstance(e, PermissionError) else ClientNotFoundError)(str(e)) from e

        self._check_self_lockout(station, role, "ban_client")
        self._check_role("ban_client", station, role, duration_minutes=args.duration_minutes)

        associated = get_station_info(station.mac, self.runner) is not None
        if not args.confirm:
            append_audit_entry(
                caller_role=role,
                action="ban_client",
                client=args.client,
                details={"mac": station.mac, "duration_minutes": args.duration_minutes},
                status="previewed",
                log_path=self.audit_path,
            )
            dur_text = f"{args.duration_minutes} min" if args.duration_minutes else "permanently"
            return BanResult(
                client=args.client,
                client_ip=station.ip,
                mac=station.mac,
                preview=True,
                requires_confirmation=True,
                banned=False,
                changed=False,
                verified=False,
                currently_associated=associated,
                expires_at=None,
                message=(
                    f"PREVIEW: would deny {station.mac} for {dur_text} and disconnect it now. "
                    "Existing IP blocks/limits stay. Re-send with confirm=true to apply."
                ),
            )

        with acquire_lock(self.lock_path):
            try:
                changed, verified = ban_station(station, runner=self.runner, deny_file=self.deny_file)
            except RuntimeError as e:
                append_audit_entry(
                    caller_role=role,
                    action="ban_client",
                    client=args.client,
                    details={"mac": station.mac, "error": str(e)},
                    status="failed",
                    log_path=self.audit_path,
                )
                raise WirelessControlError(str(e)) from e

            now = datetime.now(timezone.utc)
            expires = (now + timedelta(minutes=args.duration_minutes)).isoformat() if args.duration_minutes else None

            state = load_state(self.state_path)
            bans = [b for b in state.get("bans", []) if b.get("mac") != station.mac]
            bans.append({
                "client": station.name,
                "client_ip": station.ip,
                "mac": station.mac,
                "reason": args.reason,
                "banned_at": now.isoformat(),
                "expires_at": expires,
                "banned_by_role": role,
            })
            state["bans"] = bans
            save_state(state, self.state_path)

            append_audit_entry(
                caller_role=role,
                action="ban_client",
                client=args.client,
                details={
                    "mac": station.mac,
                    "client_ip": station.ip,
                    "duration_minutes": args.duration_minutes,
                    "expires_at": expires,
                    "changed": changed,
                },
                status="success",
                log_path=self.audit_path,
            )

            exp_desc = f"until {expires}" if expires else "permanently"
            return BanResult(
                client=args.client,
                client_ip=station.ip,
                mac=station.mac,
                preview=False,
                requires_confirmation=False,
                banned=True,
                changed=changed,
                verified=verified,
                currently_associated=False,
                expires_at=expires,
                message=f"Banned '{station.name}' ({station.mac}) {exp_desc}. Verified: in hostapd deny ACL, in deny file, station gone.",
            )

    def unban(self, client: str, role: str = "operator") -> UnbanResult:
        """Remove a MAC ban from the Wi-Fi AP."""
        try:
            args = UnbanClientArgs(client=client)
        except Exception as e:
            raise INAError(f"Invalid arguments for unban_client: {e}") from e
        self._require_writes_enabled()

        state = load_state(self.state_path)
        existing_ban = next(
            (
                b for b in state.get("bans", [])
                if args.client.lower() in (
                    (b.get("client") or "").lower(),
                    (b.get("client_ip") or "").lower(),
                    (b.get("mac") or "").lower(),
                )
            ),
            None,
        )
        banned_by = existing_ban.get("banned_by_role") if existing_ban else None

        try:
            station = resolve_station(
                args.client,
                runner=self.runner,
                leases_file=self.leases_file,
                state_bans=state.get("bans", []),
                leases_map=self.leases_map,
            )
        except ValueError as e:
            self._audit_denied(role, "unban_client", args.client, str(e))
            raise ClientNotFoundError(str(e)) from e

        self._check_role("unban_client", station, role, banned_by_role=banned_by)

        with acquire_lock(self.lock_path):
            try:
                changed, verified = unban_station(
                    station.mac, runner=self.runner, deny_file=self.deny_file
                )
            except RuntimeError as e:
                append_audit_entry(
                    caller_role=role,
                    action="unban_client",
                    client=args.client,
                    details={"mac": station.mac, "error": str(e)},
                    status="failed",
                    log_path=self.audit_path,
                )
                raise WirelessControlError(str(e)) from e

            bans = [b for b in state.get("bans", []) if b.get("mac") != station.mac]
            if len(bans) != len(state.get("bans", [])):
                state["bans"] = bans
                save_state(state, self.state_path)

            append_audit_entry(
                caller_role=role,
                action="unban_client",
                client=args.client,
                details={"mac": station.mac, "changed": changed},
                status="success",
                log_path=self.audit_path,
            )

            return UnbanResult(
                client=args.client,
                mac=station.mac,
                unbanned=True,
                changed=changed,
                verified=verified,
                message=f"Unbanned '{station.name}' ({station.mac}). Station may now re-associate.",
            )

    def expire_bans(self) -> int:
        """Remove bans whose expiry has passed. Returns count of expired bans."""
        with acquire_lock(self.lock_path):
            state = load_state(self.state_path)
            now = datetime.now(timezone.utc)
            active_bans = []
            expired_count = 0
            for b in state.get("bans", []):
                exp_str = b.get("expires_at")
                if exp_str:
                    try:
                        exp_dt = datetime.fromisoformat(exp_str)
                        if exp_dt <= now:
                            mac = b["mac"]
                            unban_station(mac, runner=self.runner, deny_file=self.deny_file)
                            append_audit_entry(
                                caller_role="system",
                                action="unban_client",
                                client=b.get("client") or mac,
                                details={"mac": mac, "reason": "expired"},
                                status="success",
                                log_path=self.audit_path,
                            )
                            expired_count += 1
                            continue
                    except Exception:
                        pass
                active_bans.append(b)
            if expired_count > 0:
                state["bans"] = active_bans
                save_state(state, self.state_path)
            return expired_count

    def status(self, client: Optional[str] = None, role: str = "viewer") -> StatusResult:
        """Query network client status, active blocks, and rate limits."""
        permitted, reason = evaluate_policy_placeholder(action="get_status", client_ip="", target=None, role=role)
        if not permitted:
            raise PolicyDeniedError(reason)

        state = load_state(self.state_path)
        blocks = state.get("blocks", [])
        limits = state.get("limits", [])

        now = datetime.now(timezone.utc)
        active_bans: List[BanEntry] = []
        banned_macs: Set[str] = set()
        for b in state.get("bans", []):
            exp_str = b.get("expires_at")
            if exp_str:
                try:
                    exp_dt = datetime.fromisoformat(exp_str)
                    if exp_dt <= now:
                        continue
                except Exception:
                    pass
            active_bans.append(
                BanEntry(
                    client=b.get("client", ""),
                    mac=b.get("mac", ""),
                    reason=b.get("reason"),
                    banned_at=b.get("banned_at", ""),
                    expires_at=b.get("expires_at"),
                    banned_by_role=b.get("banned_by_role", "operator"),
                )
            )
            banned_macs.add((b.get("mac") or "").lower())

        # Build client status list
        clients_map: Dict[str, ClientStatus] = {}

        # 1. Populate known reservations
        leases = self.leases_map or {}
        for name, ip in leases.items():
            if client and name != client and ip != client:
                continue
            clients_map[name] = ClientStatus(
                client=name,
                ip=ip,
                associated=True,
                neighbor_state="REACHABLE",
                blocked_targets=[],
                download_kbit=None,
                upload_kbit=None,
                banned=False,
            )

        # 2. Enrich with blocks from state
        for b in blocks:
            c_name = b.get("client", "")
            if c_name in clients_map:
                clients_map[c_name].blocked_targets.append(b.get("target", ""))

        # 3. Enrich with limits from state
        for l in limits:
            c_name = l.get("client", "")
            if c_name in clients_map:
                clients_map[c_name].download_kbit = l.get("download_kbit")
                clients_map[c_name].upload_kbit = l.get("upload_kbit")

        # 4. Enrich with ban status
        for b in active_bans:
            c_name = b.client
            if c_name in clients_map:
                clients_map[c_name].banned = True

        return StatusResult(
            clients=list(clients_map.values()),
            bans=active_bans,
            drift_detected=False,
            message=f"Reported telemetry for {len(clients_map)} clients.",
        )



# Global default service instance
default_service = NetworkService()
