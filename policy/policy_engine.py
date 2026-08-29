# policy/policy_engine.py
# Decides ALLOW or DENY for every action the assistant wants to take.

import yaml           # to read the rules.yaml file
import os              # to build the file path safely
from dataclasses import dataclass   # to create a simple result object

# Path to rules.yaml, assuming this file sits next to it in policy/
RULES_PATH = os.path.join(os.path.dirname(__file__), "rules.yaml")


@dataclass
class PolicyResult:
    # Holds the decision (True/False) and the reason behind it
    allowed: bool
    reason: str


def load_policy():
    # Opens rules.yaml and converts it into a Python dictionary
    with open(RULES_PATH, "r") as f:
        return yaml.safe_load(f)


def _parse_mbit(value):
    # Converts "5mbit" (text) or 5 (number) into a plain float, e.g. 5.0
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value).replace("mbit", "").strip())


def check_policy(action: str, params: dict) -> PolicyResult:
    # action = what the user wants to do, e.g. "block_client"
    # params = the details, e.g. {"client": "client1"}

    policy = load_policy()  # load the current rules

    # Step 1: reject immediately if the action isn't in our allowed list
    if action not in policy.get("allowed_actions", []):
        return PolicyResult(False, f"Action '{action}' is not in the list of allowed actions.")

    # Step 2: handle "block_client" requests
    if action == "block_client":
        client = params.get("client")

        # reject if the client name doesn't exist in our network at all
        if client not in policy.get("known_clients", []):
            return PolicyResult(False, f"'{client}' is not a recognized client in this network.")

        # reject if the client is on the protected list
        if client in policy.get("protected_clients", []):
            return PolicyResult(False, f"'{client}' is a protected client and cannot be blocked.")

        # otherwise it's allowed
        return PolicyResult(True, f"'{client}' is not protected. Block permitted.")

    # Step 3: handle "unblock_client" requests
    if action == "unblock_client":
        client = params.get("client")

        if client not in policy.get("known_clients", []):
            return PolicyResult(False, f"'{client}' is not a recognized client in this network.")

        # unblocking is always safe (it restores normal access), so just allow it
        return PolicyResult(True, f"Unblock permitted for '{client}'.")

    # Step 4: handle "limit_bandwidth" requests
    if action == "limit_bandwidth":
        client = params.get("client")
        rate = params.get("rate")

        if client not in policy.get("known_clients", []):
            return PolicyResult(False, f"'{client}' is not a recognized client in this network.")

        if client in policy.get("protected_clients", []):
            return PolicyResult(False, f"'{client}' is protected; its bandwidth cannot be changed.")

        # try converting the rate to a number; reject if it's not understandable
        try:
            rate_val = _parse_mbit(rate)
        except (TypeError, ValueError):
            return PolicyResult(False, f"Could not understand rate value '{rate}'.")

        # get the min/max limits from the rulebook
        min_val = _parse_mbit(policy["bandwidth"]["minimum"])
        max_val = _parse_mbit(policy["bandwidth"]["maximum"])

        # reject if the requested rate is outside the allowed range
        if not (min_val <= rate_val <= max_val):
            return PolicyResult(
                False,
                f"Requested rate {rate_val}mbit is outside the allowed range ({min_val}mbit - {max_val}mbit)."
            )

        # otherwise it's allowed
        return PolicyResult(True, f"{rate_val}mbit is within allowed range. Limit permitted.")

    # fallback (should rarely be reached, since Step 1 already filters unknown actions)
    return PolicyResult(False, f"Unrecognized action '{action}'.")
