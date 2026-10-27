# Pitch to the AI Sharks 🦈

The Week 3 project for **Agentic AI for Developers** — a **multi-agent** system. Paste a
startup pitch and a panel of AI investors judges it, reacts to each other, and makes offers.

Built with **LangGraph** + **Groq** (free tier). The panel is orchestrated as a graph:
`panel → debate → host`.

## What it demonstrates (Week 3 skills)
- **Multi-agent systems** — three distinct investor personas (each an "agent" with its own lens)
- **Coordination** — a graph runs them, lets them **react to each other** (the debate round), then a **host** synthesises
- **Structured output** — each shark returns a typed `Verdict` (stance / offer / reasoning) the UI renders as a card

## The panel
| Shark | Lens |
|------|------|
| **Nikhil** — the Numbers Shark | unit economics, margins, CAC/LTV, valuation |
| **Veena** — the Visionary | market size, mission, 10x potential |
| **Sameer** — the Skeptic | risks, competition, why it fails |
| **Host** | summarises the verdicts and gives the founder a takeaway |

## Setup
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # paste your free Groq key from https://console.groq.com/keys
```

## Run
```bash
python test_offline.py    # personas/schema/formatting (no key)
python run.py             # judge the bundled sample pitch (TiffinAI)
python run.py mypitch.txt # judge your own pitch file
streamlit run app.py      # the web UI — paste any pitch, face the sharks
```

## Files
| File | Role |
|------|------|
| `sharks.py` | The three investor personas + the host |
| `schema.py` | The `Verdict` structured-output schema |
| `panel.py` | The LangGraph graph: `panel → debate → host` (`run_panel`) |
| `sample_pitch.txt` | A sample startup pitch (TiffinAI) |
| `run.py` | CLI runner |
| `app.py` | Streamlit UI |
| `test_offline.py` | Offline tests |
| `sample_pitches/` | Three pitches that exercise different panel outcomes — see its README |

## Notes
- Sharks use **structured output** via Groq's native mode (`method="json_schema", strict=True`), so every
  verdict is guaranteed to match the `Verdict` schema — strict mode is constrained decoding, so the
  model cannot emit off-schema text. Without those arguments LangChain falls back to
  function-calling, which fails often enough on Groq to break a run: the panel makes 6 structured
  calls, so at ~92% per call roughly one run in three would fail. How you *ask* for structure matters
  as much as which model you ask.
