"""Request logger for INA v2 assistant.

Logs every natural-language request made to the assistant into the ``logs/`` directory:
- ``logs/requests.log``: Formatted, human-readable timestamped log lines.
- ``logs/requests.jsonl``: Structured, machine-readable JSONL records containing request,
  intent, arguments, outcome, reply, and execution time.

The default directory is ``code/logs/`` (or configured via ``INA_LOGS_DIR``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


def get_logs_dir() -> Path:
    """Resolve the directory where INA logs should be stored."""
    env_dir = os.environ.get("INA_LOGS_DIR")
    if env_dir:
        p = Path(env_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    # Primary: code/logs/ relative to this file
    primary = Path(__file__).resolve().parent.parent / "logs"
    if primary.exists() and primary.is_dir():
        return primary

    # Secondary: logs/ in current working directory
    cwd_logs = Path.cwd() / "logs"
    if cwd_logs.exists() and cwd_logs.is_dir():
        return cwd_logs

    # Fallback: create and return primary
    primary.mkdir(parents=True, exist_ok=True)
    return primary


class RequestLogger:
    """Thread-safe request logger that records each interaction in both text and JSONL formats."""

    def __init__(self, logs_dir: Optional[Union[str, Path]] = None, enabled: bool = True) -> None:
        self.logs_dir = Path(logs_dir) if logs_dir else get_logs_dir()
        self.enabled = enabled
        self._lock = threading.Lock()
        self.text_log_path = self.logs_dir / "requests.log"
        self.jsonl_log_path = self.logs_dir / "requests.jsonl"

    def log(self, record: Any, role: str = "operator") -> None:
        """Log a TurnRecord to requests.log and requests.jsonl.

        Never raises an exception; logging failure must never disrupt assistant operations.
        """
        if not self.enabled:
            return

        try:
            now = datetime.now(timezone.utc)
            iso_time = now.isoformat()
            display_time = now.strftime("%Y-%m-%d %H:%M:%S UTC")

            # Extract fields from TurnRecord (or duck-typed object/dict)
            if hasattr(record, "to_dict"):
                rec_dict = record.to_dict()
            elif isinstance(record, dict):
                rec_dict = record
            else:
                rec_dict = {
                    "request_id": getattr(record, "request_id", "unknown"),
                    "raw_text": getattr(record, "raw_text", ""),
                    "intent": getattr(record, "intent", {}),
                    "outcome": getattr(record, "outcome", "unknown"),
                    "reply": getattr(record, "reply", ""),
                    "result": getattr(record, "result", None),
                    "elapsed_s": getattr(record, "elapsed_s", 0.0),
                    "reply_source": getattr(record, "reply_source", "template"),
                }

            request_id = rec_dict.get("request_id", "unknown")
            raw_text = rec_dict.get("raw_text", "")
            intent = rec_dict.get("intent") or {}
            outcome = rec_dict.get("outcome", "unknown")
            reply = rec_dict.get("reply", "")
            elapsed_s = rec_dict.get("elapsed_s", 0.0)
            result = rec_dict.get("result")
            reply_source = rec_dict.get("reply_source", "template")

            action = intent.get("action", "unknown") if isinstance(intent, dict) else "unknown"
            args_summary = ""
            if isinstance(intent, dict) and intent.get("tool_arguments"):
                args_summary = f" args={intent['tool_arguments']}"
            elif isinstance(intent, dict) and intent.get("target"):
                args_summary = f" target={intent['target']}"

            # Level tag based on outcome
            level = "INFO"
            if outcome in ("denied", "error"):
                level = "WARN"
            elif outcome == "failure":
                level = "ERROR"

            # 1. Format text log line
            text_entry = (
                f"{display_time} [{level}] [req:{request_id}] role={role} outcome={outcome} "
                f"elapsed={elapsed_s:.3f}s source={reply_source}\n"
                f"  Request: \"{raw_text}\"\n"
                f"  Action:  {action}{args_summary}\n"
                f"  Reply:   \"{reply}\"\n"
            )

            # 2. Format JSONL entry
            jsonl_entry = {
                "timestamp": iso_time,
                "request_id": request_id,
                "role": role,
                "raw_text": raw_text,
                "action": action,
                "intent": intent,
                "outcome": outcome,
                "reply": reply,
                "reply_source": reply_source,
                "elapsed_s": round(elapsed_s, 3),
                "result": result,
            }
            jsonl_line = json.dumps(jsonl_entry, ensure_ascii=False) + "\n"

            # 3. Thread-safe append
            with self._lock:
                self.logs_dir.mkdir(parents=True, exist_ok=True)
                with open(self.text_log_path, "a", encoding="utf-8") as f_text:
                    f_text.write(text_entry)
                with open(self.jsonl_log_path, "a", encoding="utf-8") as f_jsonl:
                    f_jsonl.write(jsonl_line)

        except Exception as err:
            # Silently tolerate or log to debug
            if os.environ.get("INA_DEBUG_LOGGING"):
                print(f"[RequestLogger error] Could not write log: {err}", file=sys.stderr)

    def tail(self, lines: int = 10, jsonl: bool = False) -> List[str]:
        """Read the last N entries from the request log."""
        target_path = self.jsonl_log_path if jsonl else self.text_log_path
        if not target_path.exists():
            return []
        try:
            with open(target_path, "r", encoding="utf-8") as fh:
                content = fh.readlines()
            return content[-lines:]
        except Exception:
            return []


# Global singleton logger instance
_DEFAULT_LOGGER: Optional[RequestLogger] = None


def get_default_logger() -> RequestLogger:
    global _DEFAULT_LOGGER
    if _DEFAULT_LOGGER is None:
        _DEFAULT_LOGGER = RequestLogger()
    return _DEFAULT_LOGGER


def log_request(record: Any, role: str = "operator") -> None:
    """Convenience function to log a request using the default RequestLogger."""
    get_default_logger().log(record, role=role)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="assistant.logger",
        description="Inspect and tail INA v2 request logs from the logs folder."
    )
    parser.add_argument("--tail", "-n", type=int, default=10,
                        help="Number of recent log lines to display (default: 10).")
    parser.add_argument("--jsonl", action="store_true",
                        help="Display entries from requests.jsonl instead of requests.log.")
    parser.add_argument("--path", action="store_true",
                        help="Print the absolute paths of the log files.")
    parser.add_argument("--clear", action="store_true",
                        help="Clear the current request logs.")
    args = parser.parse_args(argv)

    logger = get_default_logger()

    if args.path:
        print(f"Logs directory: {logger.logs_dir.resolve()}")
        print(f"Text log:       {logger.text_log_path.resolve()}")
        print(f"JSONL log:      {logger.jsonl_log_path.resolve()}")
        return 0

    if args.clear:
        try:
            if logger.text_log_path.exists():
                logger.text_log_path.unlink()
            if logger.jsonl_log_path.exists():
                logger.jsonl_log_path.unlink()
            print("Request logs cleared.")
        except Exception as e:
            print(f"Error clearing logs: {e}", file=sys.stderr)
            return 1
        return 0

    recent = logger.tail(lines=args.tail, jsonl=args.jsonl)
    if not recent:
        target = "requests.jsonl" if args.jsonl else "requests.log"
        print(f"No log entries found in {logger.logs_dir}/{target}.")
        return 0

    for line in recent:
        print(line, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())

