"""CLI for Pitch to the AI Sharks.

python run.py                 # judge the bundled sample pitch (TiffinAI)
python run.py --demo          # same as above
python run.py mypitch.txt     # judge a pitch from a text file
"""

import os
import sys

from abuse_controls import (
    ModelOutputError,
    PanelBusyError,
    PitchFileError,
    ProviderConfigurationError,
    ProviderRequestError,
)
from panel import run_panel

HERE = os.path.dirname(os.path.abspath(__file__))


def load_pitch(args) -> str:
    files = [a for a in args if not a.startswith("-")]
    path = files[0] if files else os.path.join(HERE, "sample_pitch.txt")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeError):
        raise PitchFileError(
            "Could not read the pitch file. Check that it exists and is UTF-8 encoded."
        ) from None


def main() -> None:
    try:
        pitch = load_pitch(sys.argv[1:])
        result = run_panel(pitch)
    except (
        ProviderConfigurationError,
        ProviderRequestError,
        ModelOutputError,
        PanelBusyError,
        PitchFileError,
        ValueError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None

    print("\n🦈  PITCH TO THE AI SHARKS")
    print("=" * 60)
    print("The founder pitches… the panel deliberates…\n")

    for v in result["final"]:
        badge = "💰 INVEST" if v.stance.upper().startswith("INVEST") else "🚫 PASS"
        print(f"{badge} — {v.shark}")
        if v.offer:
            print(f"   Offer: {v.offer}")
        print(f"   {v.reasoning}\n")

    print("-" * 60)
    print(f"🎤 HOST: {result['summary']}")


if __name__ == "__main__":
    main()
