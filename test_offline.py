"""Offline tests — no API key needed.

Covers the deterministic pieces (personas, schema, formatting) so we know they're
sound before spending Groq calls on the full panel. Run: python test_offline.py
"""
import os

from schema import Verdict
from sharks import SHARKS, HOST
from panel import _format

HERE = os.path.dirname(os.path.abspath(__file__))


def check(name, cond):
    print(f"  {'✅' if cond else '❌'} {name}")
    assert cond, f"FAILED: {name}"


def main():
    print("Offline tests — personas, schema, formatting")

    check("three sharks on the panel", len(SHARKS) == 3)
    check("sharks have distinct names", len({s.name for s in SHARKS}) == 3)
    check("each shark has a system prompt", all(len(s.system) > 40 for s in SHARKS))
    check("host prompt exists", len(HOST) > 40)

    v = Verdict(shark="Nikhil", stance="PASS", reasoning="The margins are too thin.")
    check("Verdict validates without an offer", v.offer is None)
    v2 = Verdict(shark="Veena", stance="INVEST", offer="Rs 4cr for 10%", reasoning="Big market.")
    check("Verdict validates with an offer", v2.offer == "Rs 4cr for 10%")

    text = _format([v, v2])
    check("_format lists every shark", "Nikhil" in text and "Veena" in text)
    check("_format shows offers", "Rs 4cr for 10%" in text)

    with open(os.path.join(HERE, "sample_pitch.txt"), encoding="utf-8") as f:
        pitch = f.read()
    check("sample pitch loads and is substantial", len(pitch) > 300 and "TiffinAI" in pitch)

    print("\n✅ All offline tests passed.")


if __name__ == "__main__":
    main()
