"""LLM backends for the assistant (workflow steps 2-3 and 12-13).

Any LLM can drive the assistant. It is chosen from the environment (``.env``,
see ``.env.example``):

    LLM_PROVIDER   anthropic | openai | openrouter | gemini | groq | ollama |
                   openai-compatible | none
    LLM_API_KEY    the provider's API key (anthropic: falls back to the SDK's
                   own credentials, e.g. ANTHROPIC_API_KEY or `ant auth login`)
    LLM_MODEL      model ID (anthropic default: claude-opus-5-5; others: required)
    LLM_BASE_URL   OpenAI-compatible endpoint; preset per provider, required for
                   openai-compatible
    LLM_EFFORT     anthropic effort level (default: low; empty = not sent)
    LLM_FALLBACKS  anthropic server-side refusal fallbacks: default | off
    LLM_TIMEOUT    seconds per request (default 30)

Adapters:

- :class:`AnthropicBackend`: Claude through the official ``anthropic`` SDK
  (Messages API tool use).
- :class:`OpenAICompatibleBackend`: any ``/chat/completions`` endpoint with
  OpenAI-format tools (OpenAI, OpenRouter, Gemini's OpenAI-compatible
  endpoint, Groq, Ollama, LM Studio, vLLM, ...), standard library only.
- :class:`RuleBasedBackend`: the deterministic regex parser; used when no LLM
  is configured and whenever an LLM call fails.

Flow with an LLM: ``interpret()`` gives the model the MCP server's tools (the
``tools/list`` schemas) through native function calling; the model chooses one
tool and its arguments, or asks a question. After the call ran, ``explain()``
appends the structured, command-free result view as that tool's result in the
same conversation, and the model writes the reply, which is shown only if it
echoes the real status.

Owner: Khushi (assistant).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from assistant import intent as intent_mod
from assistant.config import load_env_file
from assistant.intent import Intent
from assistant.prompts import (
    REPLY_INSTRUCTIONS,
    REPLY_SCHEMA,
    TOOL_SYSTEM_PROMPT,
    build_reply_prompt,
    build_user_prompt,
)
from assistant.result_view import mentions_command

MAX_REPLY_CHARS = 600
ANTHROPIC_DEFAULT_MODEL = "claude-opus-5-5"
# Models with server-side refusal fallbacks ("default" mode) on the Claude API.
ANTHROPIC_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}

OPENAI_COMPATIBLE_PRESETS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "groq": "https://api.groq.com/openai/v1",
    "ollama": "http://localhost:11434/v1",
}
_KEYLESS_PROVIDERS = {"ollama", "openai-compatible"}
_PLACEHOLDER_KEYS = {"", "your_api_key_here", "changeme"}


class LLMBackend:
    """Interface for both LLM jobs in the pipeline.

    - ``interpret`` (steps 2-3): request + known device names + the server's
      tool definitions -> :class:`Intent` (a tool call, or a question).
    - ``explain`` (steps 12-13): request + structured result view (+ the
      conversation from ``interpret``) -> reply text, or ``None`` to let the
      assistant use its deterministic template instead.
    """

    name = "base"

    def interpret(self, request: str, device_names: List[str],
                  tools: Optional[List[Dict[str, Any]]] = None) -> Intent:
        raise NotImplementedError

    def explain(self, request: str, result_view: dict,
                context: Optional[Dict[str, Any]] = None) -> Optional[str]:
        return None


class RuleBasedBackend(LLMBackend):
    """Deterministic backend built on the regex parser. The safe default."""

    name = "rule_based"

    def interpret(self, request: str, device_names: List[str],
                  tools: Optional[List[Dict[str, Any]]] = None) -> Intent:
        parsed = intent_mod.parse(request)
        parsed.parser = "rule_based"
        return parsed


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _intent_from_tool_call(name: str, arguments: Any, request: str, tools: List[Dict[str, Any]],
                           parser: str, context: Dict[str, Any]) -> Intent:
    """The model's tool choice as an Intent. Unknown tools become a question."""
    offered = {t["name"] for t in tools}
    if name not in offered:
        return Intent(action="unknown", raw_text=request, parser=parser, confidence=0.3,
                      needs_clarification=True,
                      clarification="Could you rephrase that as a specific network action?")
    args = dict(arguments) if isinstance(arguments, dict) else {}
    args.pop("confirm", None)  # only the human operator confirms a ban (client prompt)
    client = args.get("client")
    return Intent(action=name, target=client if isinstance(client, str) else None,
                  raw_text=request, parser=parser, confidence=0.9,
                  tool_arguments=args, llm_context=context)


def _intent_from_text(text: str, request: str, parser: str) -> Intent:
    """No tool call: the model asked a question (or declined)."""
    question = " ".join((text or "").split())[:300] or "Could you give me more detail?"
    return Intent(action="unknown", raw_text=request, parser=parser, confidence=0.5,
                  needs_clarification=True, clarification=question)


def _parse_json_object(text: str) -> Optional[dict]:
    """Parse a JSON object from model text, tolerating code fences or surrounding prose."""
    text = (text or "").strip()
    for candidate in (text, text[text.find("{"):text.rfind("}") + 1] if "{" in text else ""):
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


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


# ---------------------------------------------------------------------------
# Claude (official Anthropic SDK)
# ---------------------------------------------------------------------------

class AnthropicBackend(LLMBackend):
    """Claude via the official ``anthropic`` SDK, using Messages API tool use."""

    def __init__(self, model: str = ANTHROPIC_DEFAULT_MODEL, api_key: Optional[str] = None,
                 effort: Optional[str] = "low", fallbacks: bool = True,
                 timeout: float = 30.0, client: Any = None) -> None:
        if client is None:
            import anthropic  # optional dependency: pip install anthropic

            # No key given: the SDK resolves ANTHROPIC_API_KEY / an `ant auth login` profile.
            client = anthropic.Anthropic(api_key=api_key or None, timeout=timeout)
        self.client = client
        self.model = model
        self.effort = effort or None
        self.fallbacks = fallbacks and model in ANTHROPIC_FALLBACK_MODELS
        self.name = f"anthropic:{model}"

    def _create(self, **kwargs: Any) -> Any:
        output_config = dict(kwargs.pop("output_config", {}) or {})
        if self.effort:
            output_config["effort"] = self.effort
        if output_config:
            kwargs["output_config"] = output_config
        if self.fallbacks:
            # On a policy decline the API re-runs the request on a fallback model.
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        return self.client.beta.messages.create(model=self.model, max_tokens=16000, **kwargs)

    def interpret(self, request: str, device_names: List[str],
                  tools: Optional[List[Dict[str, Any]]] = None) -> Intent:
        api_tools = [{"name": t["name"], "description": t.get("description") or "",
                      "input_schema": t["input_schema"]} for t in (tools or [])]
        messages = [{"role": "user", "content": build_user_prompt(request, device_names)}]
        response = self._create(system=TOOL_SYSTEM_PROMPT, tools=api_tools,
                                tool_choice={"type": "auto"}, messages=messages)
        if response.stop_reason == "refusal":
            return _intent_from_text("I can't help with that request.", request, self.name)
        tool_use = next((b for b in response.content if b.type == "tool_use"), None)
        if tool_use is None:
            text = " ".join(b.text for b in response.content if b.type == "text")
            return _intent_from_text(text, request, self.name)
        context = {
            # Assistant content goes back unchanged (thinking blocks included).
            "messages": messages + [{"role": "assistant", "content": response.content}],
            "tool_use_id": tool_use.id,
            "tools": api_tools,
        }
        return _intent_from_tool_call(tool_use.name, tool_use.input, request, api_tools,
                                      self.name, context)

    def explain(self, request: str, result_view: dict,
                context: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if context:
            result_turn = {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": context["tool_use_id"],
                 "content": json.dumps(result_view, sort_keys=True),
                 "is_error": result_view.get("status") in ("denied", "failure")},
                {"type": "text", "text": REPLY_INSTRUCTIONS},
            ]}
            kwargs: Dict[str, Any] = dict(system=TOOL_SYSTEM_PROMPT, tools=context["tools"],
                                          tool_choice={"type": "none"},
                                          messages=context["messages"] + [result_turn])
        else:
            kwargs = dict(messages=[{"role": "user", "content": build_reply_prompt(request, result_view)}])
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": REPLY_SCHEMA}}
        response = self._create(**kwargs)
        if response.stop_reason == "refusal":
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        return _coerce_llm_reply(_parse_json_object(text), result_view)


# ---------------------------------------------------------------------------
# Any OpenAI-compatible /chat/completions endpoint (standard library only)
# ---------------------------------------------------------------------------

def _function_schema(schema: Any) -> Any:
    """JSON schema in the portable subset OpenAI-style providers accept.

    Drops ``title`` and ``pattern`` (not supported everywhere; the MCP server
    enforces patterns anyway) and turns ``anyOf: [X, null]`` into ``X``.
    """
    if isinstance(schema, list):
        return [_function_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    if "anyOf" in schema:
        options = [o for o in schema["anyOf"] if not (isinstance(o, dict) and o.get("type") == "null")]
        if len(options) == 1:
            merged = {**{k: v for k, v in schema.items() if k != "anyOf"}, **options[0]}
            return _function_schema(merged)
    return {k: _function_schema(v) for k, v in schema.items() if k not in ("title", "pattern")}


class OpenAICompatibleBackend(LLMBackend):
    """Tool calling against any OpenAI-compatible ``/chat/completions`` endpoint."""

    def __init__(self, base_url: str, model: str, api_key: Optional[str] = None,
                 provider: str = "openai-compatible", timeout: float = 30.0) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.provider = provider
        self.timeout = timeout
        self.name = f"{provider}:{model}"

    def _post(self, body: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.provider == "openrouter":
            headers["X-Title"] = "INA Network Assistant"
        req = urllib.request.Request(self.url, data=json.dumps({"model": self.model, **body}).encode("utf-8"),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    @staticmethod
    def _message(data: Dict[str, Any]) -> Dict[str, Any]:
        return (data.get("choices") or [{}])[0].get("message") or {}

    def interpret(self, request: str, device_names: List[str],
                  tools: Optional[List[Dict[str, Any]]] = None) -> Intent:
        api_tools = [{"type": "function", "function": {
            "name": t["name"], "description": t.get("description") or "",
            "parameters": _function_schema(t["input_schema"])}} for t in (tools or [])]
        messages = [{"role": "system", "content": TOOL_SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_prompt(request, device_names)}]
        message = self._message(self._post({"messages": messages, "tools": api_tools, "tool_choice": "auto"}))
        calls = message.get("tool_calls") or []
        if not calls:
            return _intent_from_text(message.get("content") or "", request, self.name)
        call = calls[0]
        function = call.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError):
            arguments = {}
        context = {
            # Only the call that will be executed goes back, so every tool_call_id gets a result.
            "messages": messages + [{"role": "assistant", "content": message.get("content"),
                                     "tool_calls": [call]}],
            "tool_call_id": call.get("id"),
            "tools": api_tools,
        }
        plain_tools = [{"name": t["function"]["name"]} for t in api_tools]
        return _intent_from_tool_call(function.get("name", ""), arguments, request, plain_tools,
                                      self.name, context)

    def explain(self, request: str, result_view: dict,
                context: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if context:
            messages = context["messages"] + [
                {"role": "tool", "tool_call_id": context["tool_call_id"],
                 "content": json.dumps(result_view, sort_keys=True)},
                {"role": "user", "content": REPLY_INSTRUCTIONS},
            ]
            body: Dict[str, Any] = {"messages": messages, "tools": context["tools"], "tool_choice": "none"}
        else:
            body = {"messages": [{"role": "user", "content": build_reply_prompt(request, result_view)}]}
        try:
            message = self._message(self._post(body))
        except (urllib.error.URLError, ValueError, OSError):
            return None
        return _coerce_llm_reply(_parse_json_object(message.get("content") or ""), result_view)


# ---------------------------------------------------------------------------
# Selection from the environment
# ---------------------------------------------------------------------------

def _warn(message: str) -> None:
    print(f"warning: {message}; using the rule-based parser.", file=sys.stderr)


def get_backend(prefer_llm: bool = True) -> LLMBackend:
    """Pick a backend from the environment (after loading .env)."""
    load_env_file()
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if not prefer_llm or provider in ("", "none", "rule_based"):
        return RuleBasedBackend()

    api_key = os.environ.get("LLM_API_KEY", "").strip()
    api_key = None if api_key.lower() in _PLACEHOLDER_KEYS else api_key
    model = os.environ.get("LLM_MODEL", "").strip()
    try:
        timeout = float(os.environ.get("LLM_TIMEOUT", "30"))
    except ValueError:
        timeout = 30.0

    if provider in ("anthropic", "claude"):
        try:
            return AnthropicBackend(
                model=model or ANTHROPIC_DEFAULT_MODEL, api_key=api_key,
                effort=os.environ.get("LLM_EFFORT", "low").strip() or None,
                fallbacks=os.environ.get("LLM_FALLBACKS", "default").strip().lower() != "off",
                timeout=timeout,
            )
        except ImportError:
            _warn("LLM_PROVIDER=anthropic needs the SDK (pip install anthropic)")
            return RuleBasedBackend()

    if provider in OPENAI_COMPATIBLE_PRESETS or provider == "openai-compatible":
        base_url = os.environ.get("LLM_BASE_URL", "").strip() or OPENAI_COMPATIBLE_PRESETS.get(provider, "")
        if not base_url:
            _warn("LLM_PROVIDER=openai-compatible needs LLM_BASE_URL")
        elif not model:
            _warn(f"LLM_PROVIDER={provider} needs LLM_MODEL")
        elif not api_key and provider not in _KEYLESS_PROVIDERS:
            _warn(f"LLM_PROVIDER={provider} needs LLM_API_KEY")
        else:
            return OpenAICompatibleBackend(base_url, model, api_key, provider=provider, timeout=timeout)
        return RuleBasedBackend()

    _warn(f"unknown LLM_PROVIDER '{provider}'")
    return RuleBasedBackend()
