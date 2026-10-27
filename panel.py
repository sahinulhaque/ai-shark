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

import os
from typing import List, TypedDict

from dotenv import load_dotenv, find_dotenv
from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, END

from schema import Verdict
from sharks import SHARKS, HOST

load_dotenv(find_dotenv(usecwd=True))

# The same model as Weeks 1, 2 and 4 — one model across the whole course.
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")


# max_retries: the free Groq tier allows 8000 tokens per MINUTE, and this agent's
# context grows with every tool call — by the later steps a single request is ~4k,
# so two of them inside one minute trips the limit on a single run, with nobody
# else on your key. langchain-groq defaults to 2 retries, which is not enough for
# a long run; the Groq SDK honours the retry-after header, so raising this simply
# waits out the window instead of failing the whole run.
def _llm(temperature: float = 0.4) -> ChatGroq:
    return ChatGroq(model=MODEL, temperature=temperature, max_retries=8)


class State(TypedDict):
    pitch: str
    round1: List[Verdict]
    final: List[Verdict]
    summary: str


def _format(verdicts: List[Verdict]) -> str:
    lines = []
    for v in verdicts:
        offer = f"  Offer: {v.offer}" if v.offer else ""
        lines.append(f"- {v.shark} — {v.stance}: {v.reasoning}{offer}")
    return "\n".join(lines)


def _evaluate(shark, pitch: str, others: str = "") -> Verdict:
    prompt = (
        shark.system
        + "\n\nYou are judging this startup pitch:\n\"\"\"\n" + pitch + "\n\"\"\"\n"
    )
    if others:
        prompt += ("\nThe other sharks have given their takes:\n" + others
                   + "\n\nReact to them where you disagree, then give YOUR verdict.\n")
    prompt += (f"\nRespond as {shark.name}. Decide INVEST or PASS, make an offer only if you "
               "invest, and give your reasoning in your own voice.")
    # method="json_schema" + strict=True = Groq's native structured outputs with
    # constrained decoding, so a Verdict cannot come back malformed. Drop either
    # argument and this panel's 6 calls per run start failing intermittently
    # (tool_use_failed without the method, json_validate_failed without strict).
    # Strict mode only exists on openai/gpt-oss-* models.
    verdict = _llm().with_structured_output(
        Verdict, method="json_schema", strict=True).invoke(prompt)
    verdict.shark = shark.name  # enforce correct attribution
    return verdict


def _panel(state: State) -> dict:
    return {"round1": [_evaluate(s, state["pitch"]) for s in SHARKS]}


def _debate(state: State) -> dict:
    # Each shark reacts to the OTHER sharks' round-1 takes — never its own, so it
    # isn't arguing with itself.
    return {"final": [
        _evaluate(s, state["pitch"], _format([v for v in state["round1"] if v.shark != s.name]))
        for s in SHARKS
    ]}


def _host(state: State) -> dict:
    prompt = (HOST + "\n\nThe pitch:\n" + state["pitch"]
              + "\n\nThe panel's final verdicts:\n" + _format(state["final"])
              + "\n\nGive your wrap-up.")
    return {"summary": _llm(0.5).invoke(prompt).content}


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
    return build_panel().invoke({"pitch": pitch})
