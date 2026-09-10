# policy/audit_log.py
# Records every ALLOW/DENY decision to a log file, so it can be
# explained later if someone asks "why did you do that?"

import json                      # to save each entry in a readable format
import os                         # to build the file path
from datetime import datetime     # to timestamp each entry

# The log file lives next to this script, inside policy/
LOG_PATH = os.path.join(os.path.dirname(__file__), "audit_log.jsonl")


def log_action(action: str, params: dict, status: str, reason: str = ""):
    # Builds one log entry as a dictionary
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),  # current time
        "action": action,      # what was requested, e.g. "block_client"
        "params": params,       # the details, e.g. {"client": "client1"}
        "status": status,        # "ALLOWED", "DENIED", or "APPLIED"
        "reason": reason,         # why this decision was made
    }

    # Appends the entry as one line of JSON text to the log file
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")

    return entry


def get_all_logs():
    # Reads every logged entry back into a list of dictionaries
    if not os.path.exists(LOG_PATH):
        return []   # no logs yet

    with open(LOG_PATH, "r") as f:
        return [json.loads(line) for line in f if line.strip()]


def explain_action(index: int = -1):
    # Turns the most recent (or a specific) log entry into a readable sentence
    logs = get_all_logs()

    if not logs:
        return "No actions have been logged yet."

    entry = logs[index]  # -1 means "the last one"

    # Convert the internal status word into a natural verb
    verb = {
        "ALLOWED": "allowed",
        "DENIED": "denied",
        "APPLIED": "applied",
    }.get(entry["status"], entry["status"].lower())

    return f"I {verb} the request to {entry['action']} with params {entry['params']} because: {entry['reason']}"
