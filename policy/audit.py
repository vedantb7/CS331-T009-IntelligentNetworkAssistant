"""Append-only audit log: who asked for what, what was decided, what happened.

One JSON object per line in ``logs/policy_audit.jsonl`` (override with the
``INA_POLICY_AUDIT_LOG`` environment variable). Two records are written per
request, linked by ``request_id``:

    event="decision"  identity (principal = role:token-id), action (tool), device,
                      target, sanitised arguments, decision, rule, reason
    event="outcome"   status (success/preview/denied/failure), detail,
                      verification_passed

This log answers "who asked for what, and why was it allowed or denied". The
service layer keeps its own execution log (network/audit.py,
/var/log/ina/audit.log); the shared ``request_id`` links the two.

A "decision" with no later "outcome" means the process stopped mid-action, which
is itself useful evidence.

Records form a hash chain (each holds ``prev`` and its own ``hash``), so editing
or deleting a line in the middle is detected by ``verify()``. This does not stop
someone with full control of the Pi from rewriting the whole file; copy the log
elsewhere if you need that guarantee.

CLI:
    python -m policy.audit --tail 20
    python -m policy.audit --denied
    python -m policy.audit --request <id>      # why was this denied?
    python -m policy.audit --verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union

from .models import PolicyDecision
from .validation import clean_for_log

GENESIS = "0" * 64
ENV_PATH = "INA_POLICY_AUDIT_LOG"
DEFAULT_PATH = str(Path(__file__).resolve().parent.parent / "logs" / "policy_audit.jsonl")
OUTCOME_STATUSES = ("success", "preview", "denied", "failure")


def default_path() -> str:
    return os.environ.get(ENV_PATH) or DEFAULT_PATH


class AuditError(Exception):
    """The audit log cannot be written or is corrupt."""


def _digest(record: Mapping[str, Any]) -> str:
    body = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


_TEXT_ARGS = ("client", "device", "target", "reason")
_NUMBER_ARGS = ("download_kbit", "upload_kbit", "mbit", "duration_minutes", "minutes")


def _safe_arguments(arguments: Any) -> Dict[str, Any]:
    """Keep only the argument keys we know about, with untrusted text cleaned."""
    if not isinstance(arguments, Mapping):
        return {}
    out: Dict[str, Any] = {}
    for key in _TEXT_ARGS:
        if key in arguments:
            out[key] = clean_for_log(arguments[key])
    for key in _NUMBER_ARGS:
        if key in arguments:
            value = arguments[key]
            if (isinstance(value, (int, float)) and not isinstance(value, bool)
                    and value == value and abs(value) != float("inf")):
                out[key] = value
            else:
                out[key] = clean_for_log(value)
    if "confirm" in arguments:
        value = arguments["confirm"]
        out["confirm"] = value if isinstance(value, bool) else clean_for_log(value)
    extra = sorted(clean_for_log(k, 24) for k in arguments
                   if k not in _TEXT_ARGS + _NUMBER_ARGS + ("confirm",))
    if extra:
        out["unexpected_keys"] = extra[:10]
    return out


class AuditLog:
    def __init__(self, path: Union[str, Path, None] = None,
                 clock: Optional[Callable[[], datetime]] = None) -> None:
        self.path = Path(path if path is not None else default_path())
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AuditError(f"cannot create log directory: {exc}") from exc
        self._seq, self._last_hash = self._load_tail()

    # -- writing -----------------------------------------------------------
    def _load_tail(self) -> Tuple[int, str]:
        if not self.path.exists():
            return 0, GENESIS
        last = None
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        last = line
        except OSError as exc:
            raise AuditError(f"cannot read audit log: {exc}") from exc
        if last is None:
            return 0, GENESIS
        try:
            rec = json.loads(last)
            return int(rec["seq"]), str(rec["hash"])
        except (ValueError, KeyError, TypeError) as exc:
            raise AuditError("existing audit log is corrupt; refusing to append") from exc

    def _append(self, record: Dict[str, Any]) -> None:
        with self._lock:
            record["seq"] = self._seq + 1
            record["ts"] = self._clock().isoformat(timespec="milliseconds")
            record["prev"] = self._last_hash
            record["hash"] = _digest(record)
            line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            try:
                new_file = not self.path.exists()
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(line)
                    handle.flush()
                    os.fsync(handle.fileno())
                if new_file:
                    os.chmod(self.path, 0o600)
            except OSError as exc:
                raise AuditError(f"cannot write audit log: {exc}") from exc
            self._seq = record["seq"]
            self._last_hash = record["hash"]

    def log_decision(self, request_id: str, principal_label: Optional[str],
                     tool: Any, arguments: Any, decision: PolicyDecision,
                     device_name: Optional[str] = None,
                     target: Optional[str] = None) -> None:
        self._append({
            "event": "decision",
            "request_id": clean_for_log(request_id, 40),
            "principal": principal_label,
            "tool": clean_for_log(tool, 40),
            "device": clean_for_log(device_name) if device_name else None,
            "target": clean_for_log(target) if target else None,
            "arguments": _safe_arguments(arguments),
            "decision": decision.decision,
            "rule": decision.rule,
            "reason": clean_for_log(decision.reason, 300),
        })

    def log_outcome(self, request_id: str, status: str, detail: str = "",
                    verification_passed: Optional[bool] = None,
                    commands: Optional[List[Mapping[str, Any]]] = None) -> None:
        record: Dict[str, Any] = {
            "event": "outcome",
            "request_id": clean_for_log(request_id, 40),
            "status": status if status in OUTCOME_STATUSES else "failure",
            "detail": clean_for_log(detail, 300),
            "verification_passed": verification_passed,
        }
        if commands is not None:
            # Summary only (seq, argv, exit code); full output is in the command log.
            record["commands"] = [
                {
                    "seq": c.get("seq") if isinstance(c.get("seq"), int) else None,
                    "argv": [clean_for_log(a, 80) for a in list(c.get("argv") or [])[:16]],
                    "exit_code": c.get("exit_code") if isinstance(c.get("exit_code"), int) else None,
                }
                for c in commands[:50] if isinstance(c, Mapping)
            ]
        self._append(record)

    # -- reading -----------------------------------------------------------
    def read_all(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        records: List[Dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    records.append(json.loads(line))
        return records

    def find(self, request_id: str) -> List[Dict[str, Any]]:
        return [r for r in self.read_all() if r.get("request_id") == request_id]

    def explain(self, request_id: str) -> str:
        """A plain-language answer to 'what happened to request X, and why?'"""
        recs = self.find(request_id)
        if not recs:
            return f"No record of request {request_id}."
        lines = []
        for r in recs:
            if r["event"] == "decision":
                args = r.get("arguments", {})
                target = r.get("device") or args.get("client") or args.get("device") or "-"
                if r.get("target"):
                    target += f" -> {r['target']}"
                lines.append(
                    f"{r['ts']}  {r.get('principal') or 'unauthenticated'} asked "
                    f"{r['tool']} on {target}: {r['decision']} ({r['rule']}) - {r['reason']}")
            else:
                ok = {True: "verified", False: "verification FAILED", None: "no verification"}[
                    r.get("verification_passed")]
                lines.append(f"{r['ts']}  outcome: {r['status']} ({ok}) {r.get('detail', '')}".rstrip())
        return "\n".join(lines)

    def verify(self) -> Tuple[bool, Optional[int]]:
        """Check the hash chain. Return (ok, first_bad_seq)."""
        prev, expected_seq = GENESIS, 1
        for rec in self.read_all():
            claimed = rec.get("hash")
            body = {k: v for k, v in rec.items() if k != "hash"}
            if rec.get("prev") != prev or rec.get("seq") != expected_seq or _digest(body) != claimed:
                return False, rec.get("seq")
            prev, expected_seq = claimed, expected_seq + 1
        return True, None


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect the INA audit log.")
    parser.add_argument("--path", default=None, help="default: $INA_POLICY_AUDIT_LOG or "
                        "logs/policy_audit.jsonl")
    parser.add_argument("--tail", type=int, metavar="N", help="show the last N records")
    parser.add_argument("--denied", action="store_true", help="show only denied decisions")
    parser.add_argument("--request", metavar="ID", help="explain one request")
    parser.add_argument("--verify", action="store_true", help="check the hash chain")
    args = parser.parse_args(argv)
    log = AuditLog(args.path)
    if args.verify:
        ok, bad = log.verify()
        print("audit log OK" if ok else f"audit log TAMPERED at seq {bad}")
        return 0 if ok else 1
    if args.request:
        print(log.explain(args.request))
        return 0
    records = log.read_all()
    if args.denied:
        records = [r for r in records if r.get("event") == "decision" and r.get("decision") == "DENY"]
    if args.tail:
        records = records[-args.tail:]
    for rec in records:
        print(json.dumps(rec, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
