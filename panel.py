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

from typing import TypedDict, cast

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
    response = _llm().with_structured_output(
        Verdict, method="json_schema", strict=True).invoke(messages)
    verdict = cast(Verdict, response)
    verdict.shark = shark.name  # enforce correct attribution
    return verdict


def _panel(state: State) -> dict:
    return {"round1": [_evaluate(s, state["pitch"]) for s in SHARKS]}


def _debate(state: State) -> dict:
    # Each shark reacts to the OTHER sharks' round-1 takes — never its own, so it
    # isn't arguing with itself.
    return {"final": [
        _evaluate(s, state["pitch"], _format(
            [v for v in state["round1"] if v.shark != s.name]))
        for s in SHARKS
    ]}


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
    return {"summary": _llm(0.5).invoke(messages).content}


def build_panel():
    g = StateGraph(State)
    g.add_node("panel", _panel)
    g.add_node("debate", _debate)
    g.add_node("host", _host)
    g.set_entry_point("panel")
    g.add_edge("panel", "debate")
    g.add_edge("debate", "host")
    g.add_edge("host", END)
    return g.compile()


def run_panel(pitch: str) -> dict:
    """Run the full panel and return {'final': [Verdict...], 'summary': str, ...}."""
    validate_pitch(pitch)
    validate_provider_config()
    with panel_execution_slot():
        check_provider_panel_budget()
        with panel_time_budget():
            try:
                result = build_panel().invoke(cast(State, {"pitch": pitch}))
                ensure_panel_time_remaining()
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
