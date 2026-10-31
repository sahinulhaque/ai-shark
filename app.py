"""Portfolio page + live AI-Shark demo.

Run: streamlit run app.py
Needs GROQ_API_KEY in the environment / .env.

This file is only the presentation layer. Agent definitions live in sharks.py,
workflow logic in panel.py and request limits in abuse_controls.py.

Page structure
  1. Live demo first: a short headline and the pitch tool, with the results
     directly underneath it.
  2. A sticky "hire me" card beside the demo, so the freelance offer is
     always on screen while someone tries the product.
  3. Below the fold: how the workflow runs, who is on the panel, the services
     on offer, how projects run, and a closing call to action.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from html import escape

import streamlit as st

from abuse_controls import (
    MAX_PITCH_CHARS,
    PITCH_RATE_LIMIT_REQUESTS,
    PITCH_RATE_LIMIT_WINDOW_SECONDS,
    ModelOutputError,
    PanelBusyError,
    PitchTooLongError,
    ProviderConfigurationError,
    ProviderRequestError,
    RateLimitExceededError,
    check_pitch_rate_limit,
    validate_pitch,
    validate_provider_config,
)
from panel import run_panel
from sharks import SHARKS

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "sample_pitch.txt"), "r", encoding="utf-8") as f:
    SAMPLE = f.read()

GITHUB_URL = "https://github.com/sahinulhaque/ai-shark"

# TODO: replace this placeholder with your real "Hire me" destination before
# you publish. Any link works: a contact form, a booking page, your Upwork or
# LinkedIn profile, or "mailto:you@example.com".
HIRE_ME_URL = "https://example.com/hire-me"
_HIRE_HREF = escape(HIRE_ME_URL, quote=True)

st.set_page_config(
    page_title="Sahinul Haque | AI & Agentic AI Developer",
    page_icon="🦈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# -----------------------------------------------------------------------------
# Design system
#
# Direction: deep-sea. A navy "ink", one teal accent, and a signal green / red
# pair that is used ONLY for INVEST / PASS so the colour always means a decision.
# Headlines use Bricolage Grotesque, body copy IBM Plex Sans, and money amounts
# use IBM Plex Mono so offers read like a term sheet.
# -----------------------------------------------------------------------------
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700;12..96,800&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@500;600&display=swap');

:root {
  --paper: #f3f6f8;
  --surface: #ffffff;
  --tint: #e8f0f3;
  --ink: #0a2540;
  --ink-2: #2b4258;
  --muted: #5a6b7c;
  --faint: #8a98a7;
  --line: #dde5ec;
  --line-strong: #c5d1db;
  --sea: #0e7c86;
  --sea-dark: #0a626b;
  --sea-soft: #e3f3f4;
  --deep: #0a2540;
  --deep-2: #123a5a;
  --invest: #137a49;
  --invest-bg: #e6f5ec;
  --invest-line: #b6dfc6;
  --pass: #b8332c;
  --pass-bg: #fceceb;
  --pass-line: #f1c1be;
  --display: 'Bricolage Grotesque', 'Segoe UI', system-ui, sans-serif;
  --body: 'IBM Plex Sans', system-ui, -apple-system, 'Segoe UI', sans-serif;
  --mono: 'IBM Plex Mono', ui-monospace, 'SFMono-Regular', Menlo, monospace;
}

/* ---------- Streamlit chrome ---------- */
html { scroll-behavior: smooth; }
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
  background: var(--paper) !important;
  color: var(--ink);
  font-family: var(--body);
  color-scheme: light;
}
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stSidebar"], [data-testid="collapsedControl"], #MainMenu, footer {
  display: none !important;
}
[data-testid="stMainBlockContainer"] {
  max-width: 1240px;
  padding: 1.25rem 2rem 4rem;
}
[data-testid="stCaptionContainer"] { color: var(--muted); }
.anchor { scroll-margin-top: 1rem; height: 0; }

/* ---------- Navigation ---------- */
.nav {
  display: flex; align-items: center; justify-content: space-between;
  gap: 1rem; padding: .25rem 0 1.5rem;
}
.brand { display: flex; align-items: center; gap: .75rem; }
.brand-mark {
  width: 40px; height: 40px; display: grid; place-items: center;
  border-radius: 12px; background: var(--deep); font-size: 1.15rem;
}
.brand-name { font-family: var(--display); font-weight: 700; font-size: 1.02rem; color: var(--ink); }
.brand-role { font-size: .76rem; color: var(--muted); margin-top: 1px; }
.nav-links { display: flex; align-items: center; gap: 1.5rem; }
.stApp .nav-links a {
  color: var(--ink-2) !important; font-size: .86rem; font-weight: 500;
  text-decoration: none !important;
}
.stApp .nav-links a:hover { color: var(--sea-dark) !important; }
.stApp .nav-links a.nav-cta {
  background: var(--deep); color: #fff !important; font-weight: 600;
  padding: .55rem 1.05rem; border-radius: 999px;
}
.stApp .nav-links a.nav-cta:hover { background: var(--sea-dark); }

/* ---------- Hero copy ---------- */
.hero { padding: .2rem 0 .4rem; max-width: 720px; }
.hero-title {
  font-family: var(--display); font-weight: 800;
  font-size: clamp(2.1rem, 3.8vw, 3.2rem); line-height: 1.06;
  letter-spacing: -.025em; color: var(--ink); margin: 0 0 .85rem;
}
.hero-lede { margin: 0; font-size: 1.04rem; line-height: 1.65; color: var(--ink-2); max-width: 62ch; }

/* ---------- Pitch tool ---------- */
.st-key-pitch-card {
  background: var(--surface);
  border: 1px solid var(--line-strong);
  border-radius: 20px;
  padding: 1.5rem 1.5rem 1.25rem;
  box-shadow: 0 18px 44px rgba(10, 37, 64, .09);
  gap: .75rem;
}
.tool-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; }
.live-badge {
  display: inline-flex; align-items: center; gap: .45rem;
  font-size: .78rem; font-weight: 600; color: var(--sea-dark);
}
.live-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--invest); }
.tool-title {
  font-family: var(--display); font-weight: 700; font-size: 1.45rem;
  letter-spacing: -.015em; color: var(--ink); margin: .3rem 0 .25rem;
}
.tool-sub { font-size: .9rem; line-height: 1.55; color: var(--muted); max-width: 52ch; }
.tool-facts { display: flex; gap: 1.1rem; flex: 0 0 auto; }
.tool-fact b { display: block; font-family: var(--display); font-size: 1.35rem; line-height: 1; color: var(--ink); }
.tool-fact span { display: block; margin-top: .25rem; font-size: .72rem; color: var(--muted); }
.privacy-note {
  padding: .65rem .85rem; border-radius: 10px;
  background: var(--sea-soft); color: #145a61;
  font-size: .82rem; line-height: 1.5;
}
.tool-help { display: flex; justify-content: space-between; gap: 1rem; font-size: .8rem; color: var(--muted); }
.tool-help b { color: var(--ink-2); font-weight: 600; }

.stApp .stTextArea textarea {
  font-family: var(--body) !important;
  font-size: .92rem !important; line-height: 1.6 !important;
  color: var(--ink) !important; background: #fbfcfd !important;
  border-radius: 10px !important;
}
.stApp .stTextArea [data-baseweb="textarea"] {
  border-radius: 10px !important; background: #fbfcfd !important;
  border: 1px solid var(--line-strong) !important;
}
.stApp .stTextArea [data-baseweb="textarea"]:focus-within {
  border-color: var(--sea) !important;
  box-shadow: 0 0 0 3px rgba(14, 124, 134, .16) !important;
}

/* ---------- Buttons ---------- */
.stApp .stButton > button, .stApp .stLinkButton > a {
  min-height: 2.9rem; border-radius: 10px !important;
  font-family: var(--body); font-weight: 600 !important;
  transition: background-color .15s ease, border-color .15s ease;
}
.stApp .stLinkButton > a {
  background: var(--surface) !important; color: var(--ink) !important;
  border: 1px solid var(--line-strong) !important;
}
.stApp .stLinkButton > a:hover { border-color: var(--sea) !important; color: var(--sea-dark) !important; }
.stApp .stButton > button p, .stApp .stLinkButton > a p { color: inherit !important; }
.stApp .stButton > button[kind="primary"], .stApp .stButton > button[data-testid="stBaseButton-primary"],
.stApp .stLinkButton > a[kind="primary"], .stApp .stLinkButton > a[data-testid="stBaseLinkButton-primary"] {
  background: var(--sea) !important; color: #fff !important;
  border: 1px solid var(--sea-dark) !important;
}
.stApp .stButton > button[kind="primary"]:hover, .stApp .stButton > button[data-testid="stBaseButton-primary"]:hover,
.stApp .stLinkButton > a[kind="primary"]:hover, .stApp .stLinkButton > a[data-testid="stBaseLinkButton-primary"]:hover {
  background: var(--sea-dark) !important; color: #fff !important;
}
.stApp .stButton > button:disabled { opacity: .72; }
.st-key-pitch-card .stButton > button { min-height: 3.2rem; font-size: 1rem; }

/* ---------- Results ---------- */
.results-head { margin-top: 1.6rem; }
.results-title { font-family: var(--display); font-weight: 700; font-size: 1.5rem; letter-spacing: -.015em; color: var(--ink); }
.tally { margin: .6rem 0 1rem; }
.tally-text { font-size: .9rem; color: var(--ink-2); margin-bottom: .45rem; }
.tally-bar { display: flex; gap: 3px; height: 8px; }
.tally-bar span { border-radius: 4px; }
.tally-bar .seg-invest { background: var(--invest); }
.tally-bar .seg-pass { background: var(--pass); }
.v-grid {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: .9rem; align-items: stretch; animation: reveal .4s ease-out both;
}
.vcard {
  display: flex; flex-direction: column; gap: .8rem; padding: 1.1rem;
  background: var(--surface); border: 1px solid var(--line);
  border-left: 5px solid var(--line-strong); border-radius: 14px;
}
.vcard.invest { border-left-color: var(--invest); }
.vcard.pass { border-left-color: var(--pass); }
.vtop { display: flex; align-items: center; gap: .7rem; }
.vavatar {
  width: 38px; height: 38px; display: grid; place-items: center; flex: 0 0 auto;
  border-radius: 10px; background: var(--deep); color: #fff;
  font-family: var(--display); font-weight: 700;
}
.vname { font-family: var(--display); font-weight: 700; font-size: 1.1rem; line-height: 1.15; color: var(--ink); }
.vtitle { font-size: .78rem; color: var(--muted); }
.vbadge {
  margin-left: auto; padding: .3rem .6rem; border-radius: 6px;
  font-size: .76rem; font-weight: 600; letter-spacing: .04em; border: 1px solid;
}
.vbadge.invest { color: var(--invest); background: var(--invest-bg); border-color: var(--invest-line); }
.vbadge.pass { color: var(--pass); background: var(--pass-bg); border-color: var(--pass-line); }
.voffer { padding: .6rem .75rem; border-radius: 8px; background: var(--tint); }
.voffer small { display: block; font-size: .72rem; color: var(--muted); margin-bottom: .15rem; }
.voffer b { font-family: var(--mono); font-weight: 600; font-size: .95rem; color: var(--ink); }
.vlabel { font-size: .76rem; font-weight: 600; color: var(--muted); }
.vtext { margin: 0; font-size: .92rem; line-height: 1.62; color: var(--ink-2); }

.st-key-host-summary {
  margin-top: .9rem; background: var(--surface);
  border: 1px solid var(--line); border-left: 5px solid var(--sea);
  border-radius: 14px; padding: 1.15rem 1.25rem;
  animation: reveal .4s ease-out both;
}
.host-title { font-family: var(--display); font-weight: 700; font-size: 1.2rem; letter-spacing: -.01em; color: var(--ink); margin: .15rem 0 .35rem; }

/* ---------- Hire card ---------- */
.st-key-hire-rail {
  position: sticky; top: 1rem;
  max-height: calc(100vh - 2rem); overflow-y: auto;
  background: var(--deep); color: #fff;
  border-radius: 20px; padding: 1.5rem;
  box-shadow: 0 18px 44px rgba(10, 37, 64, .22);
  gap: .75rem;
}
[data-testid="stColumn"]:has(.st-key-hire-rail) > [data-testid="stVerticalBlock"] { height: 100%; }
.rail-top { display: flex; align-items: center; gap: .8rem; }
.rail-avatar {
  width: 48px; height: 48px; display: grid; place-items: center; flex: 0 0 auto;
  border-radius: 14px; background: var(--sea); color: #fff;
  font-family: var(--display); font-weight: 800; font-size: 1rem;
}
.rail-name { font-family: var(--display); font-weight: 700; font-size: 1.15rem; color: #fff; line-height: 1.15; }
.rail-role { font-size: .8rem; color: #a9c0d3; margin-top: 2px; }
.rail-status {
  display: inline-flex; align-items: center; gap: .45rem; align-self: flex-start;
  margin-top: .85rem; padding: .35rem .7rem; border-radius: 999px;
  background: rgba(255, 255, 255, .09); color: #d7e6f1; font-size: .78rem; font-weight: 500;
}
.rail-status i { width: 7px; height: 7px; border-radius: 50%; background: #4fd18b; }
.rail-title {
  font-family: var(--display); font-weight: 700; font-size: 1.45rem; line-height: 1.15;
  letter-spacing: -.015em; color: #fff; margin: 1rem 0 .5rem;
}
.rail-copy { margin: 0; font-size: .88rem; line-height: 1.6; color: #b9cddd; }
.rail-list { margin: 1rem 0 .25rem; display: grid; gap: .5rem; }
.rail-list div { display: flex; gap: .55rem; font-size: .88rem; color: #e4eef6; }
.rail-list b { color: #4fd18b; font-weight: 600; }
.rail-label { margin: 1rem 0 .5rem; font-size: .76rem; font-weight: 600; color: #8fb0c8; }
.chips { display: flex; flex-wrap: wrap; gap: .35rem; }
.chip {
  padding: .28rem .6rem; border-radius: 999px; font-size: .76rem; font-weight: 500;
  background: var(--tint); color: var(--ink-2);
}
.st-key-hire-rail .chip { background: rgba(255, 255, 255, .09); color: #d7e6f1; }
.st-key-hire-rail .stLinkButton > a:not([kind="primary"]):not([data-testid="stBaseLinkButton-primary"]) {
  background: transparent !important; color: #fff !important;
  border-color: rgba(255, 255, 255, .28) !important;
}
.st-key-hire-rail .stLinkButton > a:not([kind="primary"]):not([data-testid="stBaseLinkButton-primary"]):hover {
  border-color: #fff !important; color: #fff !important;
}

/* ---------- Sections ---------- */
.sec { margin-top: 4rem; }
.sec-title {
  font-family: var(--display); font-weight: 700;
  font-size: clamp(1.55rem, 2.6vw, 2.15rem); line-height: 1.12;
  letter-spacing: -.02em; color: var(--ink); max-width: 24ch;
}
.sec-copy { margin: .6rem 0 1.4rem; max-width: 66ch; font-size: .98rem; line-height: 1.65; color: var(--muted); }

/* How it works: a real sequence, so the steps are numbered */
.flow { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .9rem; }
.flow-step { padding: 1.1rem; background: var(--surface); border: 1px solid var(--line); border-radius: 14px; }
.flow-step.hl { background: var(--deep); border-color: var(--deep); }
.flow-num {
  width: 28px; height: 28px; display: grid; place-items: center; border-radius: 50%;
  background: var(--tint); color: var(--ink); font-size: .8rem; font-weight: 600;
}
.flow-step.hl .flow-num { background: var(--sea); color: #fff; }
.flow-name { margin: .7rem 0 .3rem; font-family: var(--display); font-weight: 700; font-size: 1.05rem; color: var(--ink); }
.flow-text { font-size: .86rem; line-height: 1.55; color: var(--muted); }
.flow-step.hl .flow-name { color: #fff; }
.flow-step.hl .flow-text { color: #b9cddd; }
.eng { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1.5rem; margin-top: 1.5rem; padding-top: 1.25rem; border-top: 1px solid var(--line-strong); }
.eng b { display: block; font-size: .9rem; font-weight: 600; color: var(--ink); margin-bottom: .2rem; }
.eng span { font-size: .84rem; line-height: 1.5; color: var(--muted); }

/* Panel */
.panel-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .9rem; }
.pcard { padding: 1.15rem; background: var(--surface); border: 1px solid var(--line); border-radius: 14px; }
.pcard.host { background: var(--sea-soft); border-color: #c4e3e5; }
.pavatar {
  width: 42px; height: 42px; display: grid; place-items: center; border-radius: 12px;
  background: var(--deep); color: #fff; font-family: var(--display); font-weight: 700; font-size: 1.05rem;
}
.pcard.host .pavatar { background: var(--sea); }
.pname { margin-top: .8rem; font-family: var(--display); font-weight: 700; font-size: 1.15rem; color: var(--ink); }
.ptitle { font-size: .8rem; color: var(--muted); }
.pfocus { margin-top: .7rem; font-size: .88rem; font-weight: 600; color: var(--ink-2); }
.pdesc { margin: .35rem 0 .8rem; font-size: .86rem; line-height: 1.58; color: var(--muted); }

/* Services: a ruled list rather than another grid of cards */
.svc { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 3rem; }
.svc-item { padding: 1.15rem 0; border-top: 1px solid var(--line-strong); }
.svc-name { font-family: var(--display); font-weight: 700; font-size: 1.15rem; color: var(--ink); }
.svc-text { margin-top: .3rem; font-size: .92rem; line-height: 1.62; color: var(--muted); max-width: 52ch; }

/* How projects run: also a real sequence */
.steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .9rem; margin-top: 1.6rem; }
.step { display: flex; gap: .8rem; padding: 1.1rem; background: var(--surface); border: 1px solid var(--line); border-radius: 14px; }
.step-num {
  width: 30px; height: 30px; flex: 0 0 auto; display: grid; place-items: center;
  border-radius: 50%; background: var(--deep); color: #fff; font-size: .82rem; font-weight: 600;
}
.step b { display: block; font-family: var(--display); font-size: 1.05rem; color: var(--ink); }
.step span { display: block; margin-top: .2rem; font-size: .86rem; line-height: 1.55; color: var(--muted); }

/* Closing call to action */
.st-key-final-cta {
  margin-top: 4rem; padding: 2rem; border-radius: 20px;
  background: var(--deep); gap: .9rem;
}
.cta-title {
  font-family: var(--display); font-weight: 800; font-size: clamp(1.6rem, 3vw, 2.3rem);
  line-height: 1.1; letter-spacing: -.02em; color: #fff; max-width: 22ch;
}
.cta-copy { margin: .6rem 0 .4rem; max-width: 60ch; font-size: .98rem; line-height: 1.65; color: #b9cddd; }
.st-key-final-cta .stLinkButton > a:not([kind="primary"]):not([data-testid="stBaseLinkButton-primary"]) {
  background: transparent !important; color: #fff !important;
  border-color: rgba(255, 255, 255, .3) !important;
}
.st-key-final-cta .stLinkButton > a:not([kind="primary"]):not([data-testid="stBaseLinkButton-primary"]):hover {
  border-color: #fff !important; color: #fff !important;
}

.footer { padding: 2rem 0 0; text-align: center; font-size: .8rem; color: var(--faint); }

/* ---------- Motion: only results appear with a short reveal ---------- */
@keyframes reveal { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; scroll-behavior: auto !important; }
}

/* ---------- Keyboard focus ---------- */
.stApp a:focus-visible, .stApp button:focus-visible { outline: 3px solid rgba(14, 124, 134, .45); outline-offset: 2px; }

/* ---------- Responsive ---------- */
@media (max-width: 1100px) {
  .panel-grid, .flow, .eng { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .v-grid { grid-template-columns: 1fr; }
  .tool-facts { display: none; }
}
@media (max-width: 800px) {
  .svc, .steps { grid-template-columns: 1fr; }
  .st-key-hire-rail { position: static; max-height: none; }
}
@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] { padding: 1rem 1rem 3rem; }
  .nav-links a:not(.nav-cta) { display: none; }
  .brand-role { display: none; }
  .panel-grid, .flow, .eng { grid-template-columns: 1fr; }
  .tool-help { flex-direction: column; gap: .2rem; }
  .st-key-pitch-card, .st-key-final-cta { padding: 1.1rem; }
}
</style>
""",
    unsafe_allow_html=True,
)


# -----------------------------------------------------------------------------
# Helpers and session state
# -----------------------------------------------------------------------------
def _html(markup: str) -> None:
    """Render an HTML fragment as a single markdown block.

    Streamlit's markdown parser ends an HTML block at the first blank line and
    treats lines indented by four spaces as code. Collapsing the whitespace
    between tags keeps each fragment one block, so CSS grids apply to all of
    their children.
    """
    st.markdown(re.sub(r"\s*\n\s*", " ", markup.strip()),
                unsafe_allow_html=True)


def _anchor(anchor: str) -> None:
    _html(f'<div id="{anchor}" class="anchor"></div>')


def _text(value: str) -> str:
    """Escape model or persona text for use inside an HTML fragment."""
    return escape(value).replace("\r", "").replace("\n", "<br>")


st.session_state.setdefault("_review_running", False)
st.session_state.setdefault("_review_requested", False)
st.session_state.setdefault("_review_result", None)
st.session_state.setdefault("_review_notice", None)


def _request_review() -> None:
    """Disable repeat submissions before the provider call starts."""
    st.session_state["_review_running"] = True
    st.session_state["_review_requested"] = True
    st.session_state["_review_result"] = None
    st.session_state["_review_notice"] = None


# Presentation metadata lives here so sharks.py stays the single source of truth
# for names, titles and prompts while the page can add plain-language copy.
SHARK_PRESENTATION = {
    "Nikhil": {
        "focus": "Unit economics and financial discipline",
        "description": "Pressure-tests CAC, LTV, margins, burn, valuation and the path to a sustainable business.",
        "tags": ["CAC / LTV", "Margins", "Burn", "Valuation"],
    },
    "Veena": {
        "focus": "Market size and upside",
        "description": "Looks for the 10x opportunity: market timing, ambition, growth potential and a possible defensible advantage.",
        "tags": ["Vision", "Market", "Growth", "Moat"],
    },
    "Sameer": {
        "focus": "Competition, risk and failure modes",
        "description": "Searches for the reason the company could fail: differentiation, execution, competition and defensibility.",
        "tags": ["Competition", "Risk", "Moat", "Execution"],
    },
}
SHARK_BY_NAME = {shark.name: shark for shark in SHARKS}
SHARK_DISPLAY_ORDER = [s for s in SHARKS if s.name in SHARK_PRESENTATION]

SERVICES = [
    ("Agentic AI systems",
     "Multi-agent workflows, orchestration, state, tool use and structured hand-offs."),
    ("LLM applications",
     "Decision-support tools, copilots, extraction, RAG-style workflows and AI-assisted operations."),
    ("Production-minded prototypes",
     "Fast, polished builds that prove a product loop before a larger engineering investment."),
    ("AI reliability layer",
     "Validation, structured outputs, guardrails, testing, rate limits and failure handling."),
]
TOOLKIT = [
    "Python", "LangGraph", "LangChain", "LLMs", "Groq", "Pydantic",
    "RAG", "FastAPI", "Streamlit", "Structured outputs", "Testing",
]

# -----------------------------------------------------------------------------
# Navigation
# -----------------------------------------------------------------------------
_html(
    f"""
    <div class="nav">
      <div class="brand">
        <div class="brand-mark">🦈</div>
        <div>
          <div class="brand-name">Sahinul Haque</div>
          <div class="brand-role">AI &amp; Agentic AI Developer, Technical Architect</div>
        </div>
      </div>
      <div class="nav-links">
        <a href="#demo">Live demo</a>
        <a href="#panel">The panel</a>
        <a href="#services">Services</a>
        <a class="nav-cta" href="{_HIRE_HREF}">Hire me</a>
      </div>
    </div>
    """
)

# -----------------------------------------------------------------------------
# Above the fold: the demo on the left, the hire card on the right
# -----------------------------------------------------------------------------
left, right = st.columns([1.7, 0.9], gap="large", vertical_alignment="top")

with left:
    _anchor("demo")
    _html(
        """
        <div class="hero">
          <div class="hero-title">Pitch your startup to three AI investors.</div>
          <p class="hero-lede">
            Paste a pitch and run the panel. Three investor agents judge it, challenge each other,
            and a host sums up the result. This is AI-Shark, a multi-agent system I built, and a
            working sample of the AI projects I take on.
          </p>
        </div>
        """
    )

    with st.container(key="pitch-card"):
        _html(
            """
            <div class="tool-head">
              <div>
                <span class="live-badge"><span class="live-dot"></span>Live demo, running the real workflow</span>
                <div class="tool-title">Run the AI-Shark panel</div>
                <div class="tool-sub">
                  Include the customer problem, solution, market, business model, traction and funding ask
                  for the strongest result.
                </div>
              </div>
              <div class="tool-facts">
                <div class="tool-fact"><b>3</b><span>investors</span></div>
                <div class="tool-fact"><b>7</b><span>model calls</span></div>
              </div>
            </div>
            """
        )
        _html(
            '<div class="privacy-note">Your pitch and the panel responses are sent to Groq '
            "for analysis. Do not submit confidential or sensitive information.</div>"
        )
        pitch = st.text_area(
            "Pitch details",
            value=SAMPLE,
            height=260,
            key="pitch_input",
            label_visibility="collapsed",
            placeholder="Describe the customer problem, solution, market, business model, traction and funding ask.",
        )
        if len(pitch) > MAX_PITCH_CHARS:
            st.caption(
                f"⚠ {len(pitch):,} of {MAX_PITCH_CHARS:,} characters. Shorten the pitch to run the panel.")
        else:
            st.caption(f"{len(pitch):,} of {MAX_PITCH_CHARS:,} characters")
        st.button(
            "The panel is reviewing your pitch…" if st.session_state[
                "_review_running"] else "Run the panel",
            type="primary",
            width="stretch",
            disabled=st.session_state["_review_running"],
            on_click=_request_review,
        )
        _html(
            '<div class="tool-help"><span><b>You get:</b> three final investor decisions and a host summary</span>'
            "<span>A review takes a short while to run</span></div>"
        )

    # -------------------------------------------------------------------------
    # Review execution (behaviour unchanged)
    # -------------------------------------------------------------------------
    if st.session_state["_review_requested"]:
        st.session_state["_review_requested"] = False
        try:
            validate_pitch(pitch)
            validate_provider_config()
            client_identity = st.context.ip_address or st.session_state.setdefault(
                "_rate_limit_session_id", secrets.token_urlsafe(24)
            )
            client_key = hashlib.sha256(
                client_identity.encode("utf-8")).hexdigest()
            check_pitch_rate_limit(client_key)
            with st.spinner("The panel is thinking across three investor lenses…"):
                st.session_state["_review_result"] = run_panel(pitch)
        except (ProviderConfigurationError, ProviderRequestError, ModelOutputError) as exc:
            st.session_state["_review_notice"] = ("error", str(exc))
        except PitchTooLongError as exc:
            st.session_state["_review_notice"] = ("error", str(exc))
        except ValueError as exc:
            st.session_state["_review_notice"] = ("error", str(exc))
        except RateLimitExceededError as exc:
            window = (
                f"{PITCH_RATE_LIMIT_WINDOW_SECONDS // 60} minutes"
                if PITCH_RATE_LIMIT_WINDOW_SECONDS >= 60
                else f"{PITCH_RATE_LIMIT_WINDOW_SECONDS} seconds"
            )
            st.session_state["_review_notice"] = (
                "warning",
                f"Review limit reached. Try again in about {exc.retry_after_seconds} seconds. "
                f"Current limit: {PITCH_RATE_LIMIT_REQUESTS} submissions per {window}.",
            )
        except PanelBusyError:
            st.session_state["_review_notice"] = (
                "info", "The review panel is busy. Please try again shortly."
            )
        except Exception:
            st.session_state["_review_notice"] = (
                "error", "The review could not be completed. Please try again."
            )
        finally:
            st.session_state["_review_running"] = False
        st.rerun()

    notice = st.session_state["_review_notice"]
    if notice:
        level, message = notice
        if level == "warning":
            st.warning(message)
        elif level == "info":
            st.info(message)
        else:
            st.error(message)

    # -------------------------------------------------------------------------
    # Results, directly under the tool
    # -------------------------------------------------------------------------
    result = st.session_state["_review_result"]
    if result:
        verdicts = result["final"]
        total = len(verdicts)
        invest_count = sum(1 for v in verdicts if v.stance == "INVEST")
        pass_count = total - invest_count

        segments = ""
        if invest_count:
            segments += f'<span class="seg-invest" style="flex:{invest_count}"></span>'
        if pass_count:
            segments += f'<span class="seg-pass" style="flex:{pass_count}"></span>'

        cards = []
        for verdict in verdicts:
            investing = verdict.stance == "INVEST"
            state = "invest" if investing else "pass"
            shark = SHARK_BY_NAME.get(verdict.shark)
            title = shark.title if shark else ""
            offer = (
                f'<div class="voffer"><small>Proposed offer</small><b>{_text(verdict.offer)}</b></div>'
                if verdict.offer else ""
            )
            cards.append(
                f"""
                <div class="vcard {state}">
                  <div class="vtop">
                    <div class="vavatar">{_text(verdict.shark[:1].upper())}</div>
                    <div>
                      <div class="vname">{_text(verdict.shark)}</div>
                      <div class="vtitle">{_text(title)}</div>
                    </div>
                    <span class="vbadge {state}">{"INVEST" if investing else "PASS"}</span>
                  </div>
                  {offer}
                  <div class="vlabel">Rationale</div>
                  <p class="vtext">{_text(verdict.reasoning)}</p>
                </div>
                """
            )

        _html(
            f"""
            <div class="results-head">
              <div class="results-title">Investor decisions</div>
              <div class="tally">
                <div class="tally-text">{invest_count} of {total} investors would invest.
                  Each one judged alone, then reconsidered after hearing the others.</div>
                <div class="tally-bar">{segments}</div>
              </div>
            </div>
            """
        )
        _html(f'<div class="v-grid">{"".join(cards)}</div>')

        with st.container(key="host-summary"):
            _html(
                '<div class="vlabel">Host summary</div>'
                '<div class="host-title">Where the panel agrees, and where it doesn’t</div>'
            )
            st.write(str(result["summary"]).replace("$", r"\$"))

with right:
    with st.container(key="hire-rail"):
        _html(
            """
            <div class="rail-top">
              <div class="rail-avatar">SH</div>
              <div>
                <div class="rail-name">Sahinul Haque</div>
                <div class="rail-role">AI &amp; Agentic AI Developer, Technical Architect</div>
              </div>
            </div>
            <span class="rail-status"><i></i>Available for selected AI builds</span>
            <div class="rail-title">Hire me to build your AI system</div>
            <p class="rail-copy">
              I build practical AI systems for teams that need a workflow, not another generic chatbot:
              agentic automation, decision support and structured LLM applications.
            </p>
            """
            + '<div class="rail-list">'
            + "".join(f"<div><b>✓</b><span>{_text(title)}</span></div>" for title, _ in SERVICES)
            + "</div>"
        )
        st.link_button("Hire me for an AI project", HIRE_ME_URL,
                       type="primary", width="stretch")
        st.link_button("See the source on GitHub", GITHUB_URL, width="stretch")
        _html(
            '<div class="rail-label">Toolkit</div><div class="chips">'
            + "".join(f'<span class="chip">{_text(t)}</span>' for t in TOOLKIT)
            + "</div>"
        )

# -----------------------------------------------------------------------------
# How the workflow runs
# -----------------------------------------------------------------------------
_anchor("workflow")
_html(
    """
    <div class="sec">
      <div class="sec-title">How one pitch becomes a panel decision</div>
      <p class="sec-copy">
        AI-Shark is an orchestrated workflow, not a single prompt with four names attached to it.
        Each stage hands structured data to the next.
      </p>
      <div class="flow">
        <div class="flow-step">
          <div class="flow-num">1</div>
          <div class="flow-name">Submit a pitch</div>
          <div class="flow-text">Problem, solution, market, business model, traction and funding ask.</div>
        </div>
        <div class="flow-step hl">
          <div class="flow-num">2</div>
          <div class="flow-name">Judge alone</div>
          <div class="flow-text">Nikhil, Veena and Sameer each give a verdict on the same pitch without seeing the others.</div>
        </div>
        <div class="flow-step">
          <div class="flow-num">3</div>
          <div class="flow-name">Challenge</div>
          <div class="flow-text">Each investor reads the other two verdicts, reacts where they disagree, and decides again.</div>
        </div>
        <div class="flow-step">
          <div class="flow-num">4</div>
          <div class="flow-name">Host summary</div>
          <div class="flow-text">The host reports how the panel voted, where it split, and one next step for the founder.</div>
        </div>
      </div>
      <div class="eng">
        <div><b>LangGraph orchestration</b><span>Explicit stages and shared state.</span></div>
        <div><b>Structured outputs</b><span>Pydantic models and JSON schema keep results predictable.</span></div>
        <div><b>Guardrails</b><span>Input limits, rate limits, timeouts and safe error messages.</span></div>
        <div><b>Offline tests</b><span>Behaviour is tested without calling a model.</span></div>
      </div>
    </div>
    """
)

# -----------------------------------------------------------------------------
# The panel
# -----------------------------------------------------------------------------
_anchor("panel")
panel_cards = []
for shark in SHARK_DISPLAY_ORDER:
    meta = SHARK_PRESENTATION[shark.name]
    panel_cards.append(
        f"""
        <div class="pcard">
          <div class="pavatar">{_text(shark.name[:1].upper())}</div>
          <div class="pname">{_text(shark.name)}</div>
          <div class="ptitle">{_text(shark.title)}</div>
          <div class="pfocus">{_text(meta["focus"])}</div>
          <p class="pdesc">{_text(meta["description"])}</p>
          <div class="chips">{"".join(f'<span class="chip">{_text(t)}</span>' for t in meta["tags"])}</div>
        </div>
        """
    )
panel_cards.append(
    """
    <div class="pcard host">
      <div class="pavatar">H</div>
      <div class="pname">Host</div>
      <div class="ptitle">Panel moderator</div>
      <div class="pfocus">From debate to decision</div>
      <p class="pdesc">Reads the final positions, points out where the investors agree or clash, and leaves the founder with one actionable takeaway.</p>
      <div class="chips"><span class="chip">Summary</span><span class="chip">Agreement</span><span class="chip">Disagreement</span><span class="chip">Next step</span></div>
    </div>
    """
)
_html(
    f"""
    <div class="sec">
      <div class="sec-title">Four roles, four ways to test an idea</div>
      <p class="sec-copy">
        Each agent has a different job, so the panel can show where it agrees, where it splits,
        and which assumptions in the pitch need attention.
      </p>
      <div class="panel-grid">{"".join(panel_cards)}</div>
    </div>
    """
)

# -----------------------------------------------------------------------------
# Services and process
# -----------------------------------------------------------------------------
_anchor("services")
service_items = "".join(
    f'<div class="svc-item"><div class="svc-name">{_text(title)}</div>'
    f'<div class="svc-text">{_text(body)}</div></div>'
    for title, body in SERVICES
)
_html(
    f"""
    <div class="sec">
      <div class="sec-title">What you can hire me for</div>
      <p class="sec-copy">
        AI-Shark is the proof. The offer is AI engineering: turning ambiguous business workflows
        into structured, testable and usable AI software.
      </p>
      <div class="svc">{service_items}</div>
      <div class="steps">
        <div class="step"><div class="step-num">1</div><div><b>Clarify</b><span>Define the user, workflow, data, AI boundary and success criteria.</span></div></div>
        <div class="step"><div class="step-num">2</div><div><b>Build</b><span>Prototype the smallest credible system around the real use case.</span></div></div>
        <div class="step"><div class="step-num">3</div><div><b>Harden</b><span>Add validation, tests, guardrails and a clean deployment or hand-off path.</span></div></div>
      </div>
    </div>
    """
)

# -----------------------------------------------------------------------------
# Closing call to action
# -----------------------------------------------------------------------------
with st.container(key="final-cta"):
    _html(
        """
        <div class="cta-title">Have an AI workflow worth turning into software?</div>
        <p class="cta-copy">
          Bring the problem, the current workflow and the outcome you want. The goal is a focused build
          with a clear user loop, not an AI demo that stops at the prompt.
        </p>
        """
    )
    cta_primary, cta_secondary, _spacer = st.columns([1, 1, 1.2], gap="small")
    with cta_primary:
        st.link_button("Hire me for an AI project", HIRE_ME_URL,
                       type="primary", width="stretch")
    with cta_secondary:
        st.link_button("See the source on GitHub", GITHUB_URL, width="stretch")

_html('<div class="footer">AI-Shark, a multi-agent decision-support demo. Built by Sahinul Haque.</div>')
