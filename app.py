"""Streamlit UI for Pitch to the AI Sharks.

Run:  streamlit run app.py
Needs GROQ_API_KEY in the environment / .env (free Groq key).
"""
import os

import streamlit as st

from panel import run_panel
from sharks import SHARKS

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "sample_pitch.txt"), "r", encoding="utf-8") as f:
    SAMPLE = f.read()

st.set_page_config(page_title="Pitch to the AI Sharks", page_icon="🦈", layout="centered")
st.title("🦈 Pitch to the AI Sharks")
st.caption("Pitch your startup to a panel of AI investors. They judge it, argue, and make offers.")

with st.sidebar:
    st.subheader("The panel")
    for s in SHARKS:
        st.markdown(f"**{s.name}** — {s.title}")

pitch = st.text_area("Your pitch", value=SAMPLE, height=240)

if st.button("Face the Sharks 🦈", type="primary"):
    with st.spinner("The panel is deliberating…"):
        result = run_panel(pitch)

    st.subheader("The verdicts")
    for v in result["final"]:
        invest = v.stance.upper().startswith("INVEST")
        with st.container(border=True):
            st.markdown(f"### {'💰' if invest else '🚫'} {v.shark} — {'INVEST' if invest else 'PASS'}")
            if v.offer:
                st.markdown(f"**Offer:** {v.offer}")
            st.write(v.reasoning)

    st.divider()
    st.markdown("### 🎤 The Host")
    st.info(result["summary"])
