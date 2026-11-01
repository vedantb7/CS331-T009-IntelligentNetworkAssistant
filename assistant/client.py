"""Assistant orchestrator.

Ties the pieces together for one request:

    request (plain English)
      -> interpret with an LLM or the rule-based parser  (assistant/llm.py)
      -> if unclear, ask a question and stop
      -> build a tool call and attach the role token
      -> send it to the server via a transport            (assistant/transport.py)
      -> read the uniform result (decision + verification)
      -> reduce it to the structured, command-free view   (assistant/result_view.py)
      -> the LLM writes the reply from that view; the deterministic template
         is used when no LLM is configured or its reply fails the checks

The assistant never attaches a role, never runs a command, and only ever sees
admin-assigned device names. Every request, interpretation and result is kept
in a transcript so the evaluation can measure behaviour and so we have a record
alongside the server's audit log.

Owner: Khushi (assistant).

CLI:
    python -m assistant                     # interactive
    python -m assistant "block guest-phone" # one request
    python -m assistant --role admin "limit lab-laptop-3 to 5 mbps"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from assistant.contract import ToolCall, ToolName, ToolResult
from assistant.intent import Intent
from assistant.llm import LLMBackend, get_backend
from assistant.result_view import llm_result_view
from assistant.transport import MockTransport, Transport

# Map a role name to the token the client will send. In a real deployment the
# operator's client holds exactly one token; here we allow picking for the demo.
ROLE_TOKENS = {"operator": "operator-token", "admin": "admin-token"}


@dataclass
class TurnRecord:
    """One request and everything that happened to it."""

    request_id: str
    raw_text: str
    intent: Dict[str, Any]
    outcome: str                     # "clarify", "success", "denied", "failure", "error"
    reply: str
    result: Optional[Dict[str, Any]] = None
    elapsed_s: float = 0.0
    llm_view: Optional[Dict[str, Any]] = None   # exactly what the LLM was shown
    reply_source: str = "template"              # "llm" or "template"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "raw_text": self.raw_text,
            "intent": self.intent,
            "outcome": self.outcome,
            "reply": self.reply,
            "result": self.result,
            "elapsed_s": round(self.elapsed_s, 3),
            "llm_view": self.llm_view,
            "reply_source": self.reply_source,
        }


class NetworkAssistant:
    """The operator-facing assistant."""

    def __init__(self,
                 transport: Optional[Transport] = None,
                 backend: Optional[LLMBackend] = None,
                 role: str = "operator",
                 prefer_llm: bool = True) -> None:
        self.transport = transport or MockTransport()
        self.backend = backend or get_backend(prefer_llm=prefer_llm)
        self.role = role
        self.token = ROLE_TOKENS.get(role, "operator-token")
        self.transcript: List[TurnRecord] = []

    # -- device names the model is allowed to see --------------------------
    def _device_names(self) -> List[str]:
        getter = getattr(self.transport, "device_names", None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                return []
        return []

    # -- interpret, with a safe fallback to the rule-based parser ----------
    def _interpret(self, request: str) -> Intent:
        names = self._device_names()
        try:
            return self.backend.interpret(request, names)
        except Exception:
            # Any LLM/transport error -> deterministic parser, never a crash.
            from assistant import intent as intent_mod
            parsed = intent_mod.parse(request)
            parsed.parser = "rule_based_fallback"
            return parsed

    def _build_call(self, intent: Intent, request_id: str) -> ToolCall:
        args: Dict[str, Any] = {}
        if intent.target is not None:
            args["device"] = intent.target
            args["client"] = intent.target  # real server uses client= not device=
        # block_client and unblock_client require a target argument
        if intent.action in (ToolName.BLOCK_CLIENT.value, ToolName.UNBLOCK_CLIENT.value):
            if "target" not in args:
                args["target"] = "all"
        if intent.action == ToolName.LIMIT_BANDWIDTH.value and "mbit" in intent.params:
            mbit = intent.params["mbit"]
            args["mbit"] = mbit
            # Real server expects kbit integers
            args["download_kbit"] = int(mbit * 1000)
            args["upload_kbit"] = int(mbit * 1000)
        if intent.action == ToolName.BAN_CLIENT.value:
            if "minutes" in intent.params:
                args["minutes"] = intent.params["minutes"]
                args["duration_minutes"] = intent.params["minutes"]
            if "permanent" in intent.params:
                args["permanent"] = intent.params["permanent"]
        return ToolCall(tool=intent.action, arguments=args,
                        token=self.token, request_id=request_id)

    # -- plain-language replies --------------------------------------------
    def _reply(self, request: str, result: ToolResult):
        """Step 6-7: give the LLM the structured view, take its reply, or fall back.

        Returns (reply, source, view). The LLM never sees the raw ToolResult,
        only the whitelisted view (no commands, no command output).
        """
        view = llm_result_view(result)
        try:
            text = self.backend.explain(request, view)
        except Exception:
            text = None
        if text:
            return text, "llm", view
        return self._explain(result), "template", view

    def _explain(self, result: ToolResult) -> str:
        if result.status == "preview" or (result.data or {}).get("requires_confirmation"):
            # Nothing has changed yet; never say "Done" for a preview.
            return result.detail or "This change needs your confirmation before it is applied."
        if result.status == "denied":
            reason = result.decision.reason if result.decision else result.detail
            return f"I can't do that: {reason}"
        if result.status == "failure":
            v = result.verification
            if v and not v.passed:
                return (f"The change was applied but did not verify "
                        f"(expected {v.expected}, measured {v.measured}). "
                        f"It may need to be rolled back.")
            return f"That didn't succeed: {result.detail}"
        # success
        if result.tool in (ToolName.GET_STATUS.value, ToolName.LIST_DEVICES.value):
            rows = result.data.get("devices", [])
            if not rows:
                return "No devices are on the network right now."
            lines = []
            for r in rows:
                state = []
                if r.get("blocked"):
                    state.append("blocked")
                if r.get("limit_mbit"):
                    state.append(f"limited to {r['limit_mbit']:g} Mbit/s")
                elif r.get("download_kbit"):
                    state.append(f"limited to {r['download_kbit'] / 1000:g} Mbit/s")
                if r.get("banned"):
                    state.append("banned")
                tag = f" ({', '.join(state)})" if state else ""
                cls = f" [{r['class']}]" if r.get("class") else ""
                lines.append(f"  - {r['name']}{cls}{tag}")
            return "Devices on the network:\n" + "\n".join(lines)
        v = result.verification
        proof = ""
        if v and v.measured:
            proof = f" Verified: {v.measured}."
        return f"Done. {result.detail}{proof}"

    # -- the main entry point ----------------------------------------------
    def handle(self, request: str, interactive: bool = False) -> TurnRecord:
        request_id = "r-" + uuid.uuid4().hex[:6]
        started = time.time()
        intent = self._interpret(request)

        if intent.needs_clarification or intent.action == "unknown":
            rec = TurnRecord(
                request_id=request_id, raw_text=request,
                intent=intent.to_dict(), outcome="clarify",
                reply=intent.clarification or "Could you give me more detail?",
                elapsed_s=time.time() - started,
            )
            self.transcript.append(rec)
            return rec

        call = self._build_call(intent, request_id)
        try:
            result = self.transport.call(call)
        except Exception as err:
            rec = TurnRecord(
                request_id=request_id, raw_text=request,
                intent=intent.to_dict(), outcome="error",
                reply=f"Could not reach the network service: {err}",
                elapsed_s=time.time() - started,
            )
            self.transcript.append(rec)
            return rec

        # Check if result requires confirmation (preview)
        is_preview = (
            result.status == "preview"
            or (result.data and result.data.get("requires_confirmation"))
        )
        if is_preview:
            reply, source, view = self._reply(request, result)
            if interactive and sys.stdin.isatty():
                print(reply)
                try:
                    ans = input("Proceed? [y/N]: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    ans = "n"
                if ans == "y":
                    call.arguments["confirm"] = True
                    result = self.transport.call(call)
                    reply, source, view = self._reply(request, result)
                    outcome = result.status
                else:
                    outcome = "preview"
                    reply, source = "Cancelled by user.", "template"
            else:
                outcome = "preview"
        else:
            reply, source, view = self._reply(request, result)
            outcome = result.status

        rec = TurnRecord(
            request_id=request_id, raw_text=request, intent=intent.to_dict(),
            outcome=outcome, reply=reply,
            result=_result_to_dict(result), elapsed_s=time.time() - started,
            llm_view=view, reply_source=source,
        )
        self.transcript.append(rec)
        return rec

    def save_transcript(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for rec in self.transcript:
                fh.write(json.dumps(rec.to_dict()) + "\n")


def _result_to_dict(result: ToolResult) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "status": result.status, "tool": result.tool,
        "device": result.device, "detail": result.detail,
        "data": result.data,
    }
    if result.decision:
        out["decision"] = {"decision": result.decision.decision,
                           "reason": result.decision.reason,
                           "rule": result.decision.rule}
    if result.verification:
        out["verification"] = {"passed": result.verification.passed,
                              "expected": result.verification.expected,
                              "measured": result.verification.measured,
                              "checks": result.verification.checks}
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_transport(args: argparse.Namespace) -> Transport:
    if args.server == "mcp":
        # Real pipeline: MCP client -> INA MCP server (validation + policy) -> command.
        # No silent fallback to the mock: a failure here must be visible.
        from assistant.transport import McpTransport
        return McpTransport(token=ROLE_TOKENS.get(args.role, "operator-token"),
                            command=args.mcp_command or None)
    if args.server == "service":
        # Connect directly to the local NetworkService (on Pi)
        try:
            from assistant.transport import ServiceTransport
            return ServiceTransport()
        except Exception as e:
            print(f"Warning: Could not connect to NetworkService ({e}), using mock.")
            return MockTransport()
    if args.server:
        from assistant.transport import HttpTransport
        return HttpTransport(base_url=args.server)
    return MockTransport()


def _print_banner(assistant: NetworkAssistant) -> None:
    backend = assistant.backend.name
    kind = "mock network" if isinstance(assistant.transport, MockTransport) else "live server"
    if type(assistant.transport).__name__ == "McpTransport":
        kind = "INA MCP server"
    print("=" * 60)
    print("INA v2 - Operator Assistant")
    print(f"  Role:      {assistant.role}")
    print(f"  Interpreter: {backend}")
    print(f"  Target:    {kind}")
    print("=" * 60)
    names = assistant._device_names()
    if names:
        print("Known devices: " + ", ".join(names))
    print("Type a request, or 'exit' to quit. Examples:")
    print("  list devices")
    print("  limit lab-laptop-3 to 5 mbps")
    print("  block guest-phone")
    print("  restore guest-phone")
    print()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="assistant",
        description="INA v2 natural-language operator assistant.",
    )
    parser.add_argument("request", nargs="*", help="A single request to run, "
                        "non-interactively. If omitted, starts an interactive session.")
    parser.add_argument("--role", choices=list(ROLE_TOKENS), default="operator",
                        help="Role to act as (default: operator).")
    parser.add_argument("--server", default=os.environ.get("INA_SERVER", ""),
                        help="'mcp' to use the INA MCP server (stdio), 'service' for the "
                        "local service layer, or an HTTP base URL. If omitted, uses "
                        "the built-in mock network.")
    parser.add_argument("--mcp-command", default=os.environ.get("INA_MCP_COMMAND", ""),
                        help="With --server mcp: command that starts the MCP server on "
                        "stdio (default: python -m inav2.mcp_server.server; e.g. "
                        "'ssh ina@ina-router').")
    parser.add_argument("--no-llm", action="store_true",
                        help="Force the rule-based parser even if an API key is set.")
    parser.add_argument("--transcript", default="",
                        help="Write the session transcript to this file.")
    args = parser.parse_args(argv)

    assistant = NetworkAssistant(
        transport=_build_transport(args),
        role=args.role,
        prefer_llm=not args.no_llm,
    )

    if args.request:
        rec = assistant.handle(" ".join(args.request))
        print(rec.reply)
        if args.transcript:
            assistant.save_transcript(args.transcript)
        return 0

    _print_banner(assistant)
    try:
        while True:
            try:
                text = input("operator> ").strip()
            except EOFError:
                break
            if text.lower() in ("exit", "quit", "q"):
                break
            if not text:
                continue
            rec = assistant.handle(text)
            print(rec.reply + "\n")
    except KeyboardInterrupt:
        print()
    if args.transcript:
        assistant.save_transcript(args.transcript)
    print("Goodbye.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
