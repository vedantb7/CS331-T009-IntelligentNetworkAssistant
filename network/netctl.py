"""CLI administrative helper ('ina-netctl') for INA v2.

Provides the single privilege boundary command for root-delegated execution
(e.g. via sudoers: 'ina ALL=(root) NOPASSWD: /usr/local/sbin/ina-netctl').
Every privileged action INA takes - nft, tc, hostapd_cli, and the state, lock
and audit files under /var - runs inside this command. The unprivileged MCP
server reaches it through 'network/privileged.py' (NetctlService), which calls
'sudo -n ina-netctl --json --role <role> <command> -- <args>' and parses the
JSON envelope printed on stdout:

    {"ok": true,  "result": {...}}                          exit 0
    {"ok": false, "error": "<ErrorClass>", "message": "..."} exit 1

Also used by 'ina-reconcile.service' to restore state on boot and by
'ina-expire.service' to lift expired bans.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import List, Optional

from inav2.network.access_control import (
    apply_block_rule,
    ensure_ina_filter_table,
    validate_client_identifier,
    validate_target_identifier,
)
from inav2.network.bandwidth import (
    apply_bandwidth_limit,
    ensure_tc_infrastructure,
    remove_bandwidth_limit_rules,
)
from inav2.network.service import (
    default_service,
    load_state,
)
from inav2.network.wireless import (
    DENY_MAC_FILE,
    deny_acl_add,
    write_deny_file,
)

ROLES = ("admin", "operator", "viewer")


def reconcile(deny_file: Optional[str] = None) -> int:
    """Restore desired blocks, rate limits, and MAC bans from state.json on system boot."""
    state = load_state()
    blocks: List[dict] = state.get("blocks", [])
    limits: List[dict] = state.get("limits", [])
    bans: List[dict] = state.get("bans", [])

    print(f"Reconciling INA network state: {len(blocks)} blocks, {len(limits)} limits, {len(bans)} bans...")

    # 1. Re-initialize nftables table and sets
    ensure_ina_filter_table()

    # 2. Re-apply block rules
    for b in blocks:
        client_ip = b.get("client_ip")
        target = b.get("target")
        if client_ip and target:
            try:
                t_type, t_val = validate_target_identifier(target, client_ip)
                apply_block_rule(client_ip, t_type, t_val)
                print(f"  Restored block: {client_ip} -> {target}")
            except Exception as e:
                print(f"  Warning: failed to restore block {client_ip} -> {target}: {e}", file=sys.stderr)

    # 3. Re-initialize tc infrastructure and limits
    ensure_tc_infrastructure()
    for l in limits:
        client_ip = l.get("client_ip")
        down = l.get("download_kbit")
        up = l.get("upload_kbit")
        if client_ip and down and up:
            try:
                apply_bandwidth_limit(client_ip, down, up)
                print(f"  Restored limit: {client_ip} -> {down}k down / {up}k up")
            except Exception as e:
                print(f"  Warning: failed to restore limit {client_ip}: {e}", file=sys.stderr)

    # 4. Re-apply MAC bans (deny file first, then runtime ACL), skipping expired ones
    now = datetime.now(timezone.utc)
    active = [
        b for b in bans
        if not b.get("expires_at") or datetime.fromisoformat(b["expires_at"]) > now
    ]
    target_deny_file = deny_file or DENY_MAC_FILE
    try:
        write_deny_file({b["mac"] for b in active}, path=target_deny_file)
    except Exception as e:
        print(f"  Warning: failed to write deny file: {e}", file=sys.stderr)

    for b in active:
        ok, err = deny_acl_add(b["mac"])
        if ok:
            print(f"  Restored ban: {b['mac']} ({b.get('client')})")
        else:
            print(f"  Warning: failed to restore ban {b['mac']}: {err}", file=sys.stderr)

    print("Reconciliation complete.")
    return 0


def _result_dict(res: object) -> dict:
    if isinstance(res, dict):
        return res
    if hasattr(res, "model_dump"):
        return res.model_dump()
    if hasattr(res, "to_dict"):
        return res.to_dict()
    return dict(res.__dict__)


def _dump_result(res: object) -> str:
    return json.dumps(_result_dict(res), indent=2)


def _run_command(parsed: argparse.Namespace) -> object:
    """Execute one parsed command through the service layer and return its result."""
    role = parsed.role
    if parsed.command == "block":
        return default_service.block(parsed.client, parsed.target, role=role)
    if parsed.command == "unblock":
        return default_service.unblock(parsed.client, parsed.target, role=role)
    if parsed.command == "limit":
        return default_service.limit(parsed.client, parsed.download_kbit, parsed.upload_kbit, role=role)
    if parsed.command == "unlimit":
        return default_service.remove_limit(parsed.client, role=role)
    if parsed.command == "kick":
        return default_service.kick(parsed.client, role=role)
    if parsed.command == "ban":
        return default_service.ban(
            parsed.client,
            duration_minutes=parsed.minutes,
            reason=parsed.reason,
            confirm=parsed.yes,
            role=role,
        )
    if parsed.command == "unban":
        return default_service.unban(parsed.client, role=role)
    if parsed.command == "ban-owner":
        return {"banned_by_role": default_service.ban_owner_role(parsed.client)}
    if parsed.command in ("expire", "expire-bans"):
        return {"expired": default_service.expire_bans()}
    if parsed.command == "status":
        return default_service.status(parsed.client, role=role)
    raise ValueError(f"Unknown command: {parsed.command}")


def main(args: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="ina-netctl", description="INA Network Control CLI")
    parser.add_argument("--json", action="store_true",
                        help="Print a JSON envelope ({ok, result} or {ok, error, message}) on stdout")
    parser.add_argument("--role", choices=ROLES, default="admin",
                        help="Role whose rules the service layer applies (default: admin, "
                             "for the root administrator; the MCP server passes the role "
                             "its policy guard resolved from the caller's token)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # block
    p_block = subparsers.add_parser("block", help="Block traffic to a target")
    p_block.add_argument("client", help="Client name or IP")
    p_block.add_argument("target", help="Target client, gateway, internet, or all")

    # unblock
    p_unblock = subparsers.add_parser("unblock", help="Remove a block rule")
    p_unblock.add_argument("client", help="Client name or IP")
    p_unblock.add_argument("target", help="Target client or scope keyword")

    # limit
    p_limit = subparsers.add_parser("limit", help="Apply bandwidth rate limit")
    p_limit.add_argument("client", help="Client name or IP")
    p_limit.add_argument("download_kbit", type=int, help="Download rate ceiling in kbit/s")
    p_limit.add_argument("upload_kbit", type=int, help="Upload rate ceiling in kbit/s")

    # unlimit
    p_unlimit = subparsers.add_parser("unlimit", help="Remove bandwidth limit")
    p_unlimit.add_argument("client", help="Client name or IP")

    # kick
    p_kick = subparsers.add_parser("kick", help="Deauthenticate a client once")
    p_kick.add_argument("client", help="Client name, IP or MAC")

    # ban
    p_ban = subparsers.add_parser("ban", help="Deny a client's MAC on the AP")
    p_ban.add_argument("client", help="Client name, IP or MAC")
    p_ban.add_argument("--minutes", type=int, default=None, help="Ban duration (omit for permanent)")
    p_ban.add_argument("--reason", default=None, help="Short reason recorded in the audit log")
    p_ban.add_argument("--yes", action="store_true", help="Apply (without it, only preview)")

    # unban
    p_unban = subparsers.add_parser("unban", help="Remove a MAC ban")
    p_unban.add_argument("client", help="Client name, IP or MAC")

    # ban-owner (read-only; trusted context for the policy guard's unban check)
    p_owner = subparsers.add_parser("ban-owner", help="Show which role created a client's ban")
    p_owner.add_argument("client", help="Client name, IP or MAC")

    # expire / expire-bans
    subparsers.add_parser("expire", help="Remove bans whose expiry has passed")
    subparsers.add_parser("expire-bans", help="Remove bans whose expiry has passed (alias for expire)")

    # status
    p_status = subparsers.add_parser("status", help="Show client status and limits")
    p_status.add_argument("client", nargs="?", default=None, help="Optional client filter")

    # reconcile
    subparsers.add_parser("reconcile", help="Re-apply state.json to kernel after boot")

    parsed = parser.parse_args(args)

    if parsed.command == "reconcile":
        return reconcile()

    try:
        res = _run_command(parsed)
    except Exception as err:
        if parsed.json:
            print(json.dumps({"ok": False, "error": type(err).__name__, "message": str(err)}))
        else:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    if parsed.json:
        print(json.dumps({"ok": True, "result": _result_dict(res)}))
    elif parsed.command in ("expire", "expire-bans"):
        print(f"Expired {res['expired']} ban(s).")
    else:
        print(_dump_result(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())

