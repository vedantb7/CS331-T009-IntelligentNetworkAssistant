"""The structured result the LLM sees (workflow steps 6 and 7).

The MCP server returns a typed result. Before any LLM reads it, the assistant
reduces it to this fixed shape, built from a whitelist, so the model only ever
sees what the action did - never how. In particular it never contains the
commands that ran on the router (argv, stdout, stderr), which live only in the
root-side command log.

    {
      "version": 1,
      "tool": "limit_bandwidth",
      "device": "guest-phone",
      "status": "success" | "preview" | "denied" | "failure",
      "verified": true | false | null,   # server read the kernel state back
      "summary": "..." | null,           # server's one-line description (success/preview)
      "reason": "..." | null,            # why it was denied or failed (with a reference)
      "outcome": {...},                  # whitelisted result fields, rates in Mbit/s
      "devices": [...]                   # get_status only
    }

Owner: Khushi (assistant).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from assistant.contract import ToolName, ToolResult

RESULT_VIEW_VERSION = 1

# Result fields the LLM may see (anything else the server returns is dropped).
_OUTCOME_FIELDS = (
    "changed", "blocked", "unblocked", "target", "client_ip",
    "kicked", "was_associated", "reconnected",
    "banned", "unbanned", "expires_at", "currently_associated", "requires_confirmation",
)
_RATE_FIELDS = (("download_kbit", "download_mbps"), ("upload_kbit", "upload_mbps"))

# Text that names a system command; if a server string contains one it is withheld.
_COMMAND_WORDS = re.compile(
    r"\b(nft|nftables|tc|qdisc|iptables|hostapd_cli|deny_acl|ip\s+neigh|sudo|ina-netctl|"
    r"argv|stdout|stderr|exit code)\b",
    re.IGNORECASE,
)

_GENERIC_FAILURE = "The change could not be completed on the router."


def mentions_command(text: Optional[str]) -> bool:
    """True if text names a system command or its output."""
    return bool(text) and bool(_COMMAND_WORDS.search(text))


def _safe_text(text: Optional[str], fallback: Optional[str] = None) -> Optional[str]:
    text = (text or "").strip()
    if not text:
        return fallback
    return fallback if mentions_command(text) else text[:300]


def _mbps(kbit: Any) -> Optional[float]:
    return round(kbit / 1000, 3) if isinstance(kbit, (int, float)) else None


def _device_rows(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for r in data.get("devices", []) or []:
        if not isinstance(r, dict):
            continue
        row: Dict[str, Any] = {"name": r.get("name")}
        if r.get("class"):
            row["class"] = r["class"]
        if "associated" in r:
            row["associated"] = r.get("associated")
        blocked = r.get("blocked")
        row["blocked"] = blocked if isinstance(blocked, (list, bool)) else bool(blocked)
        down = _mbps(r.get("download_kbit"))
        if down is None and isinstance(r.get("limit_mbit"), (int, float)):
            down = float(r["limit_mbit"])
        row["limit_mbps"] = down
        row["banned"] = bool(r.get("banned", False))
        rows.append(row)
    return rows


def llm_result_view(result: ToolResult) -> Dict[str, Any]:
    """Reduce a ToolResult to the structured, command-free view given to the LLM."""
    data = result.data or {}
    status = result.status if result.status in ("success", "preview", "denied", "failure") else "failure"

    outcome: Dict[str, Any] = {k: data[k] for k in _OUTCOME_FIELDS if k in data}
    for kbit_key, mbps_key in _RATE_FIELDS:
        if isinstance(data.get(kbit_key), (int, float)):
            outcome[mbps_key] = _mbps(data[kbit_key])
    if isinstance(data.get("limit_mbit"), (int, float)):
        outcome["download_mbps"] = outcome["upload_mbps"] = float(data["limit_mbit"])

    view: Dict[str, Any] = {
        "version": RESULT_VIEW_VERSION,
        "tool": result.tool,
        "device": result.device,
        "status": status,
        "verified": result.verification.passed if result.verification else None,
        "summary": None,
        "reason": None,
        "outcome": outcome,
    }
    if status in ("success", "preview"):
        view["summary"] = _safe_text(result.detail)
    elif status == "denied":
        reason = result.decision.reason if result.decision and result.decision.reason else result.detail
        view["reason"] = _safe_text(reason, "The request was not allowed.")
    else:
        view["reason"] = _safe_text(result.detail, _GENERIC_FAILURE)

    if result.tool in (ToolName.GET_STATUS.value, ToolName.LIST_DEVICES.value):
        view["devices"] = _device_rows(data)
    return view
