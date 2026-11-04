"""Command execution log for INA v2 (workflow step 5).

Every command INA runs on the router (nft, tc, hostapd_cli, ip) is recorded
the moment it finishes, and that same record is handed back to the MCP server:

1. Logged: appended to the command log (default /var/log/ina/commands.jsonl,
   override with INA_COMMAND_LOG) - append-only, hash-chained and fsynced per
   record, so the trail survives a crash in the middle of an operation.
2. Returned: added to the current request's trace. Inside 'ina-netctl' the
   trace goes into the JSON envelope; NetctlService (network/privileged.py)
   puts it back into the MCP server's trace, where the MCP tools log a summary
   in the policy audit outcome.

All logs carry the same request ID - the one the MCP policy guard assigned -
so a policy decision, the commands it caused, and the service audit entry can
be joined.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
import threading
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

CommandRunner = Callable[[List[str], float], Tuple[int, str, str]]

DEFAULT_COMMAND_LOG = "/var/log/ina/commands.jsonl"
TMP_COMMAND_LOG = "/tmp/ina/commands.jsonl"
FALLBACK_COMMAND_LOG = os.path.expanduser("~/.ina/commands.jsonl")
ENV_COMMAND_LOG = "INA_COMMAND_LOG"

GENESIS_HASH = "0" * 64
MAX_OUTPUT_CHARS = 4000
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_request_id: ContextVar[Optional[str]] = ContextVar("ina_request_id", default=None)
_trace: ContextVar[Optional[List[Dict[str, Any]]]] = ContextVar("ina_command_trace", default=None)


# ==============================================================================
# Request Scope (request ID + per-request trace)
# ==============================================================================

def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def valid_request_id(request_id: Optional[str]) -> Optional[str]:
    """Return request_id if it is safe to log and pass on a command line, else None."""
    if isinstance(request_id, str) and REQUEST_ID_RE.match(request_id):
        return request_id
    return None


@contextlib.contextmanager
def request_scope(request_id: Optional[str]) -> Iterator[List[Dict[str, Any]]]:
    """Set the request ID and collect the command records of one operation."""
    trace: List[Dict[str, Any]] = []
    rid_token = _request_id.set(valid_request_id(request_id) or new_request_id())
    trace_token = _trace.set(trace)
    try:
        yield trace
    finally:
        _trace.reset(trace_token)
        _request_id.reset(rid_token)


def current_request_id() -> Optional[str]:
    return _request_id.get()


def add_to_trace(records: List[Dict[str, Any]]) -> None:
    """Add records received from across the privilege boundary to the current trace."""
    trace = _trace.get()
    if trace is not None:
        trace.extend(r for r in records if isinstance(r, dict))


def summarize(trace: List[Dict[str, Any]], limit: int = 50) -> List[Dict[str, Any]]:
    """Compact per-command summary (no output) for the policy audit outcome."""
    return [
        {
            "seq": r.get("seq"),
            "argv": [str(a)[:80] for a in (r.get("argv") or [])[:16]],
            "exit_code": r.get("exit_code"),
            "duration_ms": r.get("duration_ms"),
        }
        for r in trace[:limit]
    ]


# ==============================================================================
# Command Log (append-only, hash-chained JSONL)
# ==============================================================================

def _log_path() -> str:
    custom = os.environ.get(ENV_COMMAND_LOG)
    if custom:
        os.makedirs(os.path.dirname(os.path.abspath(custom)), exist_ok=True)
        return custom
    log_dir = os.path.dirname(DEFAULT_COMMAND_LOG)
    if os.path.isdir(log_dir) and os.access(log_dir, os.W_OK):
        return DEFAULT_COMMAND_LOG
    try:
        os.makedirs(os.path.dirname(FALLBACK_COMMAND_LOG), exist_ok=True)
        return FALLBACK_COMMAND_LOG
    except OSError:
        pass
    os.makedirs(os.path.dirname(TMP_COMMAND_LOG), exist_ok=True)
    return TMP_COMMAND_LOG


def _digest(record: Dict[str, Any]) -> str:
    body = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _last_record(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 65536))
            lines = [l for l in f.read().splitlines() if l.strip()]
        return json.loads(lines[-1]) if lines else None
    except (OSError, ValueError):
        return None


_append_lock = threading.Lock()


def append_record(record: Dict[str, Any], path: Optional[str] = None) -> Dict[str, Any]:
    """Chain and append one record; returns it with seq, prev and hash set."""
    path = path or _log_path()
    with _append_lock:
        last = _last_record(path)
        record = dict(record)
        record["seq"] = (last or {}).get("seq", 0) + 1
        record["prev"] = (last or {}).get("hash", GENESIS_HASH)
        record["hash"] = _digest(record)
        new_file = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())
        if new_file:
            os.chmod(path, 0o600)
    return record


def verify_command_log(path: Optional[str] = None) -> Tuple[bool, Optional[int]]:
    """Check the hash chain. Return (ok, first_bad_seq)."""
    path = path or _log_path()
    prev, expected_seq = GENESIS_HASH, 1
    if not os.path.isfile(path):
        return True, None
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            body = {k: v for k, v in rec.items() if k != "hash"}
            if rec.get("prev") != prev or rec.get("seq") != expected_seq or _digest(body) != rec.get("hash"):
                return False, rec.get("seq")
            prev, expected_seq = rec["hash"], expected_seq + 1
    return True, None


# ==============================================================================
# Recording Runner
# ==============================================================================

def _clip(text: str) -> Tuple[str, bool]:
    text = text or ""
    return (text[:MAX_OUTPUT_CHARS], True) if len(text) > MAX_OUTPUT_CHARS else (text, False)


class RecordingRunner:
    """CommandRunner wrapper: runs the command, logs it, and adds it to the request trace."""

    def __init__(self, inner: CommandRunner) -> None:
        self.inner = inner

    def __call__(self, args: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
        started = datetime.now(timezone.utc)
        t0 = time.monotonic()
        code, stdout, stderr = self.inner(args, timeout)
        out, out_cut = _clip(stdout)
        err, err_cut = _clip(stderr)
        record: Dict[str, Any] = {
            "request_id": current_request_id(),
            "started_at": started.isoformat(timespec="milliseconds"),
            "duration_ms": round((time.monotonic() - t0) * 1000, 1),
            "argv": [str(a) for a in args],
            "exit_code": code,
            "stdout": out,
            "stderr": err,
            "truncated": out_cut or err_cut,
        }
        try:
            record = append_record(record)
        except OSError as e:
            # The command already ran; never hide its result because logging failed.
            record["log_error"] = str(e)
            print(f"warning: command log unavailable: {e}", file=sys.stderr)
        trace = _trace.get()
        if trace is not None:
            trace.append(record)
        return code, stdout, stderr


def ensure_recording(service: Any) -> None:
    """Route a NetworkService's commands through a RecordingRunner (idempotent).

    The wrapper reads the request ID and trace from context variables at call
    time, so wrapping once is safe across requests and threads.
    """
    from inav2.network.service import NetworkService

    # Only the service that really executes commands; a NetctlService's runner
    # is the 'sudo ina-netctl' hop, whose commands are recorded on the root side.
    if not isinstance(service, NetworkService):
        return
    runner = service.runner
    if isinstance(runner, RecordingRunner):
        return
    if runner is None:
        from inav2.network.device_discovery import default_command_runner
        runner = default_command_runner
    service.runner = RecordingRunner(runner)
