"""Tamper-evident audit logging for INA v2.

Implements an append-only JSON-lines log with SHA-256 cryptographic hash chaining.
Every mutation or access denial is permanently recorded with its sequence number,
timestamp, caller role, and cryptographic proof of log integrity.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from inav2.network.command_log import current_request_id

GENESIS_HASH = "0" * 64
DEFAULT_AUDIT_LOG = "/var/log/ina/audit.log"
TMP_AUDIT_LOG = "/tmp/ina/audit.log"
FALLBACK_AUDIT_LOG = os.path.expanduser("~/.ina/audit.log")


def _get_log_path(custom_path: Optional[str] = None) -> str:
    """Determine the writable audit log path."""
    if custom_path:
        os.makedirs(os.path.dirname(os.path.abspath(custom_path)), exist_ok=True)
        return custom_path

    # 1. Try system path if writable
    log_dir = os.path.dirname(DEFAULT_AUDIT_LOG)
    if os.path.isdir(log_dir) and os.access(log_dir, os.W_OK):
        return DEFAULT_AUDIT_LOG

    # 2. Try user home directory
    try:
        os.makedirs(os.path.dirname(FALLBACK_AUDIT_LOG), exist_ok=True)
        return FALLBACK_AUDIT_LOG
    except OSError:
        pass

    # 3. Fall back to /tmp/ina/
    os.makedirs(os.path.dirname(TMP_AUDIT_LOG), exist_ok=True)
    return TMP_AUDIT_LOG


def get_last_audit_entry(log_path: str) -> Optional[Dict[str, Any]]:
    """Read the last record in the audit log to retrieve sequence and hash."""
    if not os.path.isfile(log_path) or os.path.getsize(log_path) == 0:
        return None

    last_line = ""
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last_line = line.strip()
        if last_line:
            return json.loads(last_line)
    except Exception:
        pass
    return None


def calculate_entry_hash(
    prev_hash: str,
    sequence: int,
    timestamp: str,
    caller_role: str,
    action: str,
    client: str,
    details: Dict[str, Any],
    status: str,
) -> str:
    """Compute the SHA-256 cryptographic hash of an audit log entry."""
    payload = {
        "prev_hash": prev_hash,
        "sequence": sequence,
        "timestamp": timestamp,
        "caller_role": caller_role,
        "action": action,
        "client": client,
        "details": details,
        "status": status,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def append_audit_entry(
    caller_role: str,
    action: str,
    client: str,
    details: Dict[str, Any],
    status: str,
    log_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Append a cryptographically chained entry to the audit log.

    Args:
        caller_role: Caller role ('admin', 'operator', 'viewer').
        action: Executed action ('block_client', 'limit_bandwidth', etc.).
        client: Client identifier.
        details: Action parameters and results.
        status: Status string ('success', 'denied', 'failed', 'rolled_back').
        log_path: Optional override for audit log path.

    Returns:
        The written audit record dictionary.
    """
    path = _get_log_path(log_path)
    last_entry = get_last_audit_entry(path)

    # Correlate with the policy decision and the command log (same request ID).
    request_id = current_request_id()
    if request_id and "request_id" not in details:
        details = {**details, "request_id": request_id}

    if last_entry:
        sequence = last_entry.get("sequence", 0) + 1
        prev_hash = last_entry.get("hash", GENESIS_HASH)
    else:
        sequence = 1
        prev_hash = GENESIS_HASH

    now_iso = datetime.now(timezone.utc).isoformat()
    entry_hash = calculate_entry_hash(
        prev_hash=prev_hash,
        sequence=sequence,
        timestamp=now_iso,
        caller_role=caller_role,
        action=action,
        client=client,
        details=details,
        status=status,
    )

    record = {
        "sequence": sequence,
        "prev_hash": prev_hash,
        "timestamp": now_iso,
        "caller_role": caller_role,
        "action": action,
        "client": client,
        "details": details,
        "status": status,
        "hash": entry_hash,
    }

    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass

    return record


def verify_audit_log_integrity(log_path: str) -> Tuple[bool, str]:
    """Verify that every entry in the audit log correctly chains to the next.

    Args:
        log_path: Path to the audit log file.

    Returns:
        Tuple of (valid: bool, message: str).
    """
    if not os.path.isfile(log_path):
        return True, "Audit log does not exist yet (clean)."

    expected_prev = GENESIS_HASH
    expected_seq = 1

    with open(log_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                return False, f"Line {line_num}: Malformed JSON in audit log."

            seq = entry.get("sequence")
            prev_hash = entry.get("prev_hash")
            entry_hash = entry.get("hash")

            if seq != expected_seq:
                return False, f"Line {line_num}: Sequence mismatch (expected {expected_seq}, found {seq})."
            if prev_hash != expected_prev:
                return False, f"Line {line_num}: Hash chain broken (expected prev {expected_prev}, found {prev_hash})."

            recomputed = calculate_entry_hash(
                prev_hash=prev_hash,
                sequence=seq,
                timestamp=entry.get("timestamp", ""),
                caller_role=entry.get("caller_role", ""),
                action=entry.get("action", ""),
                client=entry.get("client", ""),
                details=entry.get("details", {}),
                status=entry.get("status", ""),
            )
            if recomputed != entry_hash:
                return False, f"Line {line_num}: Cryptographic hash verification failed for entry {seq}."

            expected_prev = entry_hash
            expected_seq += 1

    return True, f"Audit log verified: {expected_seq - 1} records valid."
