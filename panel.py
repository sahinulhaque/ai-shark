"""The multi-agent panel, as a LangGraph graph.

Three stages, each a node:
  panel  → every shark judges the pitch independently   (round 1)
  debate → every shark re-judges after seeing the others (they react)
  host   → the host synthesises the final verdicts

Sharks return STRUCTURED verdicts via Groq's native structured outputs
(method="json_schema", strict=True). The host writes a free-text wrap-up.
Needs a free Groq API key as GROQ_API_KEY (see .env.example).
"""
from __future__ import annotations

import time
import uuid
from contextvars import ContextVar
from typing import Any, Callable, TypedDict, cast

from groq import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.graph import END, StateGraph
from pydantic import ValidationError

from abuse_controls import (
    GROQ_MAX_OUTPUT_TOKENS,
    GROQ_MAX_RETRIES,
    GROQ_MODEL,
    ModelOutputError,
    ProviderRequestError,
    check_provider_panel_budget,
    ensure_panel_time_remaining,
    groq_request_timeout_seconds,
    panel_execution_slot,
    panel_time_budget,
    validate_pitch,
    validate_provider_config,
)
from schema import Verdict
from sharks import HOST, SHARKS
from logger import AISharkLogger, SUCCESS

_log = AISharkLogger("ai_shark.panel")
_log.configure_logging()
_correlation_id: ContextVar[str] = ContextVar("ai_shark_correlation_id", default="unassigned")
_stage: ContextVar[str] = ContextVar("ai_shark_stage", default="workflow")
_shark: ContextVar[str] = ContextVar("ai_shark_shark", default="unknown")
_call_counter: ContextVar[dict[str, int] | None] = ContextVar(
    "ai_shark_call_counter", default=None
)


def _next_call_number() -> int:
    counter = _call_counter.get()
    if counter is None:
        counter = {"count": 0}
        _call_counter.set(counter)
    counter["count"] += 1
    return counter["count"]


def _calls_started() -> int:
    counter = _call_counter.get()
    return counter["count"] if counter is not None else 0


def _llm(temperature: float = 0.4) -> ChatGroq:
    return ChatGroq(
        model=GROQ_MODEL,
        temperature=temperature,
        max_retries=GROQ_MAX_RETRIES,
        max_tokens=GROQ_MAX_OUTPUT_TOKENS,
        timeout=groq_request_timeout_seconds(),
    )


class State(TypedDict):
    pitch: str
    round1: list[Verdict]
    final: list[Verdict]
    summary: str


def _format(verdicts: list[Verdict]) -> str:
    lines = []
    for v in verdicts:
        offer = f"  Offer: {v.offer}" if v.offer else ""
        lines.append(f"- {v.shark} — {v.stance}: {v.reasoning}{offer}")
    return "\n".join(lines)


def _evaluate(shark, pitch: str, others: str = "") -> Verdict:
    # prompt = (
    #     shark.system
    #     + "\n\nYou are judging this startup pitch:\n\"\"\"\n" + pitch + "\n\"\"\"\n"
    # )
    # if others:
    #     prompt += ("\nThe other sharks have given their takes:\n" + others
    #                + "\n\nReact to them where you disagree, then give YOUR verdict.\n")
    # prompt += (f"\nRespond as {shark.name}. Decide INVEST or PASS, make an offer only if you "
    #            "invest, and give your reasoning in your own voice.")

    others_message = f"\nThe other sharks have given their takes:\n{others}\n\nReact to them where you disagree, then give YOUR verdict.\n" if others else ""
    messages = [
        SystemMessage(content=shark.system),
        HumanMessage(content=(
            "You are judging this startup pitch:\n\n"
            f"<pitch>\n{pitch}\n</pitch>\n\n"
            f"{others_message}"
            f"\nRespond as {shark.name}. Decide INVEST or PASS, make an offer only if you "
            "invest, and give your reasoning in your own voice."
        ))
    ]

    # method="json_schema" + strict=True = Groq's native structured outputs with
    # constrained decoding, so a Verdict cannot come back malformed. Drop either
    # argument and this panel's 6 calls per run start failing intermittently
    # (tool_use_failed without the method, json_validate_failed without strict).
    # Strict mode only exists on openai/gpt-oss-* models.
    call_number = _next_call_number()
    metadata = {
        "correlation_id": _correlation_id.get(), "stage": _stage.get(),
        "shark": shark.name, "call_number": call_number,
    }
    _log.event("LLM invocation started", **metadata)
    runnable = _llm().with_structured_output(
        Verdict, method="json_schema", strict=True)
    started = time.perf_counter()
    try:
        response = runnable.invoke(messages)
    except Exception as exc:
        _log.exception("LLM invocation failed", type(exc),
                       duration_ms=round((time.perf_counter() - started) * 1000, 1),
                       **metadata)
        raise
    _log.event("LLM invocation completed", SUCCESS,
               duration_ms=round((time.perf_counter() - started) * 1000, 1),
               outcome="success", **metadata)
    verdict = cast(Verdict, response)
    verdict.shark = shark.name  # enforce correct attribution
    return verdict


def _panel(state: State) -> dict:
    _log.event("Initial evaluations started", correlation_id=_correlation_id.get(),
               stage="panel", outcome="sequential")
    verdicts = []
    token = _stage.set("initial_evaluation")
    try:
        for s in SHARKS:
            shark_token = _shark.set(s.name)
            _log.event("Shark evaluation started", correlation_id=_correlation_id.get(),
                       stage="initial", shark=s.name)
            try:
                verdicts.append(_evaluate(s, state["pitch"]))
                _log.event("Shark evaluation completed", SUCCESS,
                           correlation_id=_correlation_id.get(), stage="initial",
                           shark=s.name, outcome="success")
            finally:
                _shark.reset(shark_token)
    finally:
        _stage.reset(token)
    return {"round1": verdicts}


def _debate(state: State) -> dict:
    # Each shark reacts to the OTHER sharks' round-1 takes — never its own, so it
    # isn't arguing with itself.
    verdicts = []
    token = _stage.set("debate")
    try:
        for s in SHARKS:
            shark_token = _shark.set(s.name)
            _log.event("Shark re-evaluation started", correlation_id=_correlation_id.get(),
                       stage="debate", shark=s.name)
            try:
                verdicts.append(_evaluate(s, state["pitch"], _format(
                    [v for v in state["round1"] if v.shark != s.name])))
                _log.event("Shark re-evaluation completed", SUCCESS,
                           correlation_id=_correlation_id.get(), stage="debate",
                           shark=s.name, outcome="success")
            finally:
                _shark.reset(shark_token)
    finally:
        _stage.reset(token)
    return {"final": verdicts}


def _host(state: State) -> dict:
    # prompt = (HOST + "\n\nThe pitch:\n" + state["pitch"]
    #           + "\n\nThe panel's final verdicts:\n" + _format(state["final"])
    #           + "\n\nGive your wrap-up.")
    # return {"summary": _llm(0.5).invoke(prompt).content}

    messages = [
        SystemMessage(content=HOST),
        HumanMessage(content=(
            "The pitch:\n\n"
            f"<pitch>\n{state["pitch"]}\n</pitch>"
            "\n\nThe panel's final verdicts:\n\n"
            f"<final_verdicts>\n{_format(state["final"])}\n</final_verdicts>"
            "\n\nGive your wrap-up."
        ))
    ]
    metadata = {"correlation_id": _correlation_id.get(), "stage": "host",
                "shark": "Host", "call_number": _next_call_number()}
    _log.event("Host synthesis started", **metadata)
    runnable = _llm(0.5)
    started = time.perf_counter()
    try:
        response = runnable.invoke(messages)
    except Exception as exc:
        _log.exception("LLM invocation failed", type(exc),
                       duration_ms=round((time.perf_counter() - started) * 1000, 1),
                       **metadata)
        raise
    _log.event("LLM invocation completed", SUCCESS,
               duration_ms=round((time.perf_counter() - started) * 1000, 1),
               outcome="success", **metadata)
    _log.event("Host synthesis completed", SUCCESS, correlation_id=_correlation_id.get(),
               stage="host", shark="Host", outcome="success")
    return {"summary": response.content}


def _logged_node(
    name: str, function: Callable[[State], dict[str, Any]]
) -> Callable[[State], dict[str, Any]]:
    def execute(state: State) -> dict[str, Any]:
        _log.event("Workflow node started", correlation_id=_correlation_id.get(), stage=name)
        started = time.perf_counter()
        try:
            result = function(state)
        except Exception as exc:
            _log.exception("Workflow node failed", type(exc),
                           correlation_id=_correlation_id.get(), stage=name,
                           duration_ms=round((time.perf_counter() - started) * 1000, 1))
            raise
        _log.event("Workflow node completed", SUCCESS,
                   correlation_id=_correlation_id.get(), stage=name,
                   outcome="success",
                   duration_ms=round((time.perf_counter() - started) * 1000, 1))
        return result
    return execute


def build_panel():
    _log.event("LangGraph construction started", correlation_id=_correlation_id.get(),
               stage="graph")
    g = StateGraph(State)
    g.add_node("panel", _logged_node("panel", _panel))
    g.add_node("debate", _logged_node("debate", _debate))
    g.add_node("host", _logged_node("host", _host))
    g.set_entry_point("panel")
    g.add_edge("panel", "debate")
    g.add_edge("debate", "host")
    g.add_edge("host", END)
    graph = g.compile()
    _log.event("LangGraph construction completed", SUCCESS,
               correlation_id=_correlation_id.get(), stage="graph", outcome="success")
    return graph


def run_panel(pitch: str) -> dict:
    """Run the full panel and return {'final': [Verdict...], 'summary': str, ...}."""
    correlation_id = uuid.uuid4().hex[:12]
    token = _correlation_id.set(correlation_id)
    started = time.perf_counter()
    _call_counter.set({"count": 0})
    _log.event("Panel execution started", correlation_id=correlation_id, stage="workflow")
    try:
        try:
            validate_pitch(pitch)
            _log.event("Pitch validation passed", correlation_id=correlation_id,
                       stage="validation", outcome="success")
            validate_provider_config()
            _log.event("Provider configuration validation passed",
                       correlation_id=correlation_id, stage="validation", outcome="success")
        except Exception as exc:
            _log.exception("Validation failed", type(exc), correlation_id=correlation_id,
                           stage="validation")
            raise
        with panel_execution_slot():
            check_provider_panel_budget()
            _log.event("Execution guardrails passed", correlation_id=correlation_id,
                       stage="guardrails", outcome="success")
            with panel_time_budget():
                try:
                    graph = build_panel()
                    _log.event("Graph execution started", correlation_id=correlation_id,
                               stage="graph", outcome="sequential")
                    node_started = time.perf_counter()
                    result = graph.invoke(cast(State, {"pitch": pitch}))
                    _log.event("Graph execution completed", SUCCESS,
                               correlation_id=correlation_id, stage="graph", outcome="success",
                               calls=_calls_started(),
                               duration_ms=round((time.perf_counter() - node_started) * 1000, 1))
                    ensure_panel_time_remaining()
                    _log.event("Panel execution completed", SUCCESS,
                               correlation_id=correlation_id, stage="workflow",
                               outcome="success", calls=_calls_started(),
                               duration_ms=round((time.perf_counter() - started) * 1000, 1))
                    return result
                except AuthenticationError:
                    raise ProviderRequestError(
                        "Groq rejected the configured API key. Check GROQ_API_KEY."
                    ) from None
                except RateLimitError:
                    raise ProviderRequestError(
                        "Groq's request limit was reached. Please try again later."
                    ) from None
                except APITimeoutError:
                    raise ProviderRequestError(
                        "A Groq request timed out. Please try again."
                    ) from None
                except APIConnectionError:
                    raise ProviderRequestError(
                        "Could not connect to Groq. Check connectivity and try again."
                    ) from None
                except APIError:
                    raise ProviderRequestError(
                        "Groq could not complete the request. Check the model setting or try again."
                    ) from None
                except (ValidationError, OutputParserException):
                    raise ModelOutputError(
                        "Groq returned a response that did not match the expected verdict format. Please retry."
                    ) from None
    except BaseException as exc:
        # Safe category only; exception text can contain provider or user data.
        _log.exception("Panel execution failed", type(exc), correlation_id=correlation_id,
                       stage="workflow",
                       duration_ms=round((time.perf_counter() - started) * 1000, 1))
        raise
    finally:
        _correlation_id.reset(token)
