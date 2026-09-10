# policy/policy_engine.py
# Decides ALLOW or DENY for every action the assistant wants to take.

import yaml           # to read the rules.yaml file
import os              # to build the file path safely
import math
from typing import Any
from dataclasses import dataclass   # to create a simple result object

# Path to rules.yaml, assuming this file sits next to it in policy/
RULES_PATH = os.path.join(os.path.dirname(__file__), "rules.yaml")


@dataclass
class PolicyResult:
    # Holds the decision, reason, and the rule responsible for the decision
    allowed: bool
    reason: str
    rule_id: str = ""


def load_policy():
    # Opens rules.yaml and converts it into a Python dictionary
    with open(RULES_PATH, "r") as f:
        return yaml.safe_load(f)


import re

_RATE_FORMAT_RE = re.compile(r"^\d+(\.\d+)?\s*(kbit|mbit|gbit|kbps|mbps|mb/s|gbps)?$", re.IGNORECASE)
_CLIENT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")


def _is_valid_client_name(name: Any) -> bool:
    if not isinstance(name, str):
        return False
    cleaned = name.strip()
    return bool(_CLIENT_NAME_RE.match(cleaned)) and not cleaned.startswith("-")


def _parse_mbit(value):
    # Converts "5mbit" (text) or 5 (number) into a plain float, e.g. 5.0
    if isinstance(value, (int, float)):
        val = float(value)
        if not math.isfinite(val) or val <= 0:
            raise ValueError(f"Invalid rate number: {value}")
        return val
    text = str(value).strip().lower()
    if not _RATE_FORMAT_RE.match(text):
        raise ValueError(f"Invalid rate format: '{value}'")
    for suffix in ("mbit", "mbps", "mb/s"):
        text = text.replace(suffix, "")
    return float(text.strip())


def check_policy(action: str, params: dict) -> PolicyResult:
    # action = what the user wants to do, e.g. "block_client"
    # params = the details, e.g. {"client": "client1"}

    policy = load_policy()  # load the current rules

    # Step 1: reject immediately if the action isn't in our allowed list
    if action not in policy.get("allowed_actions", []):
        return PolicyResult(
            False,
            f"Action '{action}' is not in the list of allowed actions.",
            "action-not-allowed"
        )
    # Step 2: handle "block_client" requests
    if action == "block_client":
        client = params.get("client")

        # reject if client name is missing or invalid
        if not _is_valid_client_name(client):
            return PolicyResult(
                False,
                "A valid client name is required.",
                "invalid-client"
            )

        # reject if the client name doesn't exist in our network at all
        if client not in policy.get("known_clients", []):
            return PolicyResult(
                False,
                f"'{client}' is not a recognized client in this network.",
                "known-client"
            )

        # reject if the client is on the protected list
        if client in policy.get("protected_clients", []):
            return PolicyResult(
                False,
                f"'{client}' is a protected client and cannot be blocked.",
                "protected-client"
            )

        # otherwise it's allowed
        return PolicyResult(
            True,
            f"'{client}' is not protected. Block permitted.",
            "block-allowed"
        )

    # Step 3: handle "unblock_client" requests
    if action == "unblock_client":
        client = params.get("client")

        if not _is_valid_client_name(client):
            return PolicyResult(
                False,
                "A valid client name is required.",
                "invalid-client"
            )

        if client not in policy.get("known_clients", []):
            return PolicyResult(
                False,
                f"'{client}' is not a recognized client in this network.",
                "known-client"
            )

        # unblocking is always safe (it restores normal access), so just allow it
        return PolicyResult(
            True,
            f"Unblock permitted for '{client}'.",
            "unblock-allowed"
        )

    if action == "get_status":
        client = params.get("client")

        # Allow network-wide status query when target is omitted, empty, or all/network
        if not client or (isinstance(client, str) and client.strip().lower() in ("all", "network", "*", "status", "")):
            return PolicyResult(
                True,
                "Network status query permitted.",
                "status-query"
            )

        if not _is_valid_client_name(client):
            return PolicyResult(
                False,
                "A valid client name is required.",
                "invalid-client"
            )

        if client not in policy.get("known_clients", []):
            return PolicyResult(
                False,
                f"'{client}' is not a recognized client in this network.",
                "known-client"
            )

        return PolicyResult(
            True,
            f"Status query permitted for '{client}'.",
            "status-query"
        )

    # Step 4: handle "limit_bandwidth" requests
    if action == "limit_bandwidth":
        client = params.get("client")
        rate = params.get("rate")

        if not _is_valid_client_name(client):
            return PolicyResult(
                False,
                "A valid client name is required.",
                "invalid-client"
            )

        if client not in policy.get("known_clients", []):
            return PolicyResult(
                False,
                f"'{client}' is not a recognized client in this network.",
                "known-client"
            )

        if client in policy.get("protected_clients", []):
            return PolicyResult(
                False,
                f"'{client}' is protected; its bandwidth cannot be changed.",
                "protected-client"
            )

        # try converting the rate to a number; reject if it's not understandable
        try:
            rate_val = _parse_mbit(rate)
        except (TypeError, ValueError):
            return PolicyResult(False, f"Could not understand rate value '{rate}'.", "bandwidth-format")
        if not math.isfinite(rate_val) or rate_val <= 0:
            return PolicyResult(
                False,
                "Bandwidth rate must be a positive finite value.",
                "invalid-bandwidth"
            )
        # get the min/max limits from the rulebook
        min_val = _parse_mbit(policy["bandwidth"]["minimum"])
        max_val = _parse_mbit(policy["bandwidth"]["maximum"])

        # reject if the requested rate is outside the allowed range
        if not (min_val <= rate_val <= max_val):
            return PolicyResult(
                False,
                f"Requested rate {rate_val}mbit is outside the allowed range ({min_val}mbit - {max_val}mbit).",
                "bandwidth-bounds"
            )

        # otherwise it's allowed
        return PolicyResult(
            True,
            f"{rate_val}mbit is within allowed range. Limit permitted.",
            "bandwidth-allowed"
        )
    # fallback (should rarely be reached, since Step 1 already filters unknown actions)
    return PolicyResult(
        False,
        f"Unrecognized action '{action}'.",
        "unknown-action"
    )
