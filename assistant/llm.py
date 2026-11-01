"""LLM backends for the assistant.

Two backends share one interface:

- :class:`RuleBasedBackend` (default): wraps the deterministic parser in
  ``assistant/intent.py``. Needs no API key, no network, and gives the same
  answer every time. This is what the tests and the evaluation use.
- :class:`GeminiBackend` (optional): calls Google's Gemini API over HTTPS using
  only the standard library (``urllib``), so it adds no pip dependency. It is
  used only when ``LLM_API_KEY`` is set. If the call fails for any reason, the
  client falls back to the rule-based parser, so the assistant never hard-fails
  because of the model.

Owner: Khushi (assistant).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import List, Optional

from assistant import intent as intent_mod
from assistant.contract import ToolName
from assistant.intent import Intent
from assistant.prompts import (
    build_reply_prompt,
    build_system_prompt,
    build_user_prompt,
    few_shot_text,
)
from assistant.result_view import mentions_command

MAX_REPLY_CHARS = 600


class LLMBackend:
    """Interface for both LLM jobs in the pipeline.

    - ``interpret`` (step 2): request + known device names -> :class:`Intent`.
    - ``explain`` (step 7): request + structured result view -> reply text, or
      ``None`` to let the assistant use its deterministic template instead.
    """

    name = "base"

    def interpret(self, request: str, device_names: List[str]) -> Intent:
        raise NotImplementedError

    def explain(self, request: str, result_view: dict) -> Optional[str]:
        return None


class RuleBasedBackend(LLMBackend):
    """Deterministic backend built on the regex parser. The safe default."""

    name = "rule_based"

    def interpret(self, request: str, device_names: List[str]) -> Intent:
        parsed = intent_mod.parse(request)
        parsed.parser = "rule_based"
        return parsed


def _coerce_llm_json_to_intent(payload: dict, request: str) -> Intent:
    """Turn the model's JSON answer into an :class:`Intent`, defensively."""
    action = str(payload.get("action", "")).strip().lower()
    device = payload.get("device")
    mbit = payload.get("mbit")
    question = payload.get("question")

    valid_actions = {t.value for t in ToolName}
    if action == "clarify" or action not in valid_actions:
        if action not in valid_actions and action != "clarify":
            # The model returned something off-contract; ask rather than act.
            return Intent(action="unknown", raw_text=request, parser="llm",
                          confidence=0.3, needs_clarification=True,
                          clarification=(question or
                                         "Could you rephrase that as a specific action?"))
        return Intent(action="unknown", raw_text=request, parser="llm",
                      confidence=0.5, needs_clarification=True,
                      clarification=(question or "Could you give me more detail?"))

    params = {}
    if action == ToolName.LIMIT_BANDWIDTH.value and isinstance(mbit, (int, float)):
        params["mbit"] = float(mbit)

    device = device if isinstance(device, str) and device.strip() else None
    return Intent(action=action, target=device, params=params,
                  raw_text=request, parser="llm", confidence=0.9)


def _coerce_llm_reply(payload: object, result_view: dict) -> Optional[str]:
    """Accept the model's reply only if it is consistent with the result it was given."""
    if not isinstance(payload, dict):
        return None
    reply = payload.get("reply")
    # The model must echo the real status; a mismatch means it misread the
    # result (e.g. describing a denial as a success), so it is not shown.
    if payload.get("status") != result_view.get("status") or not isinstance(reply, str):
        return None
    reply = " ".join(reply.split())
    if not reply or len(reply) > MAX_REPLY_CHARS or mentions_command(reply):
        return None
    return reply


class GeminiBackend(LLMBackend):
    """Calls Gemini over HTTPS using only the standard library."""

    name = "gemini"

    def __init__(self, api_key: str,
                 model: str = "gemini-2.0-flash",
                 timeout: float = 20.0) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def interpret(self, request: str, device_names: List[str]) -> Intent:
        prompt = (
            build_system_prompt()
            + "\n\nExamples:\n" + few_shot_text()
            + "\n\n" + build_user_prompt(request, device_names)
        )
        payload = json.loads(self._generate_json(prompt))
        return _coerce_llm_json_to_intent(payload, request)

    def explain(self, request: str, result_view: dict) -> Optional[str]:
        try:
            payload = json.loads(self._generate_json(build_reply_prompt(request, result_view)))
        except Exception:
            return None
        return _coerce_llm_reply(payload, result_view)

    def _generate_json(self, prompt: str) -> str:
        """One generateContent call with JSON output; returns the model's text."""
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.0,
                                 "responseMimeType": "application/json"},
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["candidates"][0]["content"]["parts"][0]["text"]


def get_backend(prefer_llm: bool = True) -> LLMBackend:
    """Pick a backend from the environment.

    Returns :class:`GeminiBackend` when ``prefer_llm`` is true and both
    ``LLM_PROVIDER=gemini`` and ``LLM_API_KEY`` are set. Otherwise returns the
    rule-based backend. The returned object always exposes ``interpret``.
    """
    if not prefer_llm:
        return RuleBasedBackend()
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    model = os.environ.get("LLM_MODEL", "gemini-2.0-flash").strip()
    if provider == "gemini" and api_key and api_key != "your_api_key_here":
        return GeminiBackend(api_key=api_key, model=model)
    return RuleBasedBackend()
