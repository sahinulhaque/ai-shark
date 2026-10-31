"""Offline tests — no API key needed.

Covers the deterministic pieces (personas, schema, formatting) so we know they're
sound before spending Groq calls on the full panel. Run: python test_offline.py
"""
import os
import tempfile
import time
from unittest.mock import patch

import httpx
from groq import APIError, APITimeoutError, RateLimitError
from pydantic import ValidationError

from abuse_controls import (
    GROQ_MAX_OUTPUT_TOKENS,
    GROQ_MAX_RETRIES,
    GROQ_REQUEST_TIMEOUT_SECONDS,
    MAX_CONCURRENT_PANEL_RUNS,
    MAX_PANEL_COMPLETION_TOKENS,
    MAX_PANEL_DURATION_SECONDS,
    MAX_PITCH_CHARS,
    PANEL_PROVIDER_REQUESTS,
    ModelOutputError,
    PanelBusyError,
    PanelDeadlineExceededError,
    PitchFileError,
    PitchTooLongError,
    ProviderBudgetExceededError,
    ProviderConfigurationError,
    ProviderRequestError,
    RateLimitExceededError,
    SlidingWindowRateLimiter,
    SlidingWindowRequestBudget,
    groq_request_timeout_seconds,
    panel_execution_slot,
    panel_time_budget,
    validate_pitch,
    validate_provider_config,
)
from panel import (
    _format,
    run_panel,
)
from run import load_pitch
from schema import Verdict
from sharks import HOST, SHARKS

HERE = os.path.dirname(os.path.abspath(__file__))


def check(name, cond):
    print(f"  {'✅' if cond else '❌'} {name}")
    assert cond, f"FAILED: {name}"


def expect_raises(exception_type, callback):
    try:
        callback()
    except exception_type:
        return True
    return False


def run_with_graph_error(error):
    class FailingGraph:
        def invoke(self, _state):
            raise error

    with patch.dict(os.environ, {"GROQ_API_KEY": "offline-test-key"}), patch(
        "abuse_controls._provider_request_budget",
        SlidingWindowRequestBudget([(1000, 60), (1000, 86400)]),
    ), patch("panel.build_panel", return_value=FailingGraph()):
        return run_panel("A test pitch")


def main():
    print("Offline tests — personas, schema, formatting")

    check("three sharks on the panel", len(SHARKS) == 3)
    check("sharks have distinct names", len({s.name for s in SHARKS}) == 3)
    check("each shark has a system prompt", all(
        len(s.system) > 40 for s in SHARKS))
    check("host prompt exists", len(HOST) > 40)

    v = Verdict(shark="Nikhil", stance="PASS",
                reasoning="The margins are too thin.")
    check("Verdict validates without an offer", v.offer is None)
    v2 = Verdict(shark="Veena", stance="INVEST",
                 offer="Rs 4cr for 10%", reasoning="Big market.")
    check("Verdict validates with an offer", v2.offer == "Rs 4cr for 10%")
    check(
        "Verdict rejects an unknown stance",
        expect_raises(
            ValidationError,
            lambda: Verdict(shark="Nikhil", stance="MAYBE",
                            reasoning="Unclear."),
        ),
    )
    check(
        "Verdict rejects an offer when passing",
        expect_raises(
            ValidationError,
            lambda: Verdict(
                shark="Nikhil", stance="PASS", offer="Rs 10L", reasoning="No."
            ),
        ),
    )
    no_offer_investment = Verdict(
        shark="Veena", stance="INVEST", reasoning="I am interested."
    )
    check(
        "Verdict allows an investment without a stated offer",
        no_offer_investment.offer is None,
    )

    with patch.dict(os.environ, {"GROQ_API_KEY": ""}):
        check(
            "provider config rejects a missing Groq key",
            expect_raises(ProviderConfigurationError,
                          validate_provider_config),
        )
    with patch.dict(os.environ, {"GROQ_API_KEY": "offline-test-key"}):
        validate_provider_config()
    check("provider config accepts a configured key without contacting Groq", True)
    check("automatic SDK retries are disabled", GROQ_MAX_RETRIES == 0)
    check("per-call output is configured to 5,000 tokens",
          GROQ_MAX_OUTPUT_TOKENS == 5000)
    check("panel completion budget is 35,000 tokens",
          MAX_PANEL_COMPLETION_TOKENS == 35000)
    check("panel wall-clock budget is bounded to four minutes",
          MAX_PANEL_DURATION_SECONDS == 240)
    with panel_time_budget(1):
        check("request timeout shortens to the remaining panel budget",
              0 < groq_request_timeout_seconds() <= 1)
    with (
        patch.dict(os.environ, {"GROQ_API_KEY": "offline-test-key"}),
        patch("abuse_controls.GROQ_MODEL", " "),
    ):
        check(
            "provider config rejects an empty model setting",
            expect_raises(ProviderConfigurationError,
                          validate_provider_config),
        )

    timeout_request = httpx.Request(
        "POST", "https://api.groq.com/openai/v1/chat/completions"
    )
    try:
        run_with_graph_error(APITimeoutError(timeout_request))
    except ProviderRequestError as exc:
        timeout_message = str(exc)
    else:
        timeout_message = ""
    check(
        "provider timeout returns a safe retry message",
        timeout_message == "A Groq request timed out. Please try again.",
    )

    api_error = APIError(
        "secret-key and private pitch content",
        httpx.Request(
            "POST", "https://api.groq.com/openai/v1/chat/completions"),
        body={"sensitive": "private pitch content"},
    )
    try:
        run_with_graph_error(api_error)
    except ProviderRequestError as exc:
        safe_message = str(exc)
    else:
        safe_message = ""
    check(
        "provider errors do not expose response details",
        safe_message == "Groq could not complete the request. Check the model setting or try again.",
    )

    try:
        Verdict(shark="Nikhil", stance="MAYBE", reasoning="Invalid.")
    except ValidationError as validation_error:
        try:
            run_with_graph_error(validation_error)
        except ModelOutputError as output_error:
            safe_output_message = str(output_error)
        else:
            safe_output_message = ""
    else:
        safe_output_message = "Unexpectedly accepted an invalid verdict."
    check(
        "invalid model output returns a safe message",
        safe_output_message.startswith(
            "Groq returned a response that did not match"),
    )

    text = _format([v, v2])
    check("_format lists every shark", "Nikhil" in text and "Veena" in text)
    check("_format shows offers", "Rs 4cr for 10%" in text)

    validate_pitch("A concise startup pitch.")
    check("pitch validator accepts non-empty text", True)
    check(
        "pitch validator rejects oversized text",
        expect_raises(
            PitchTooLongError,
            lambda: validate_pitch("x" * (MAX_PITCH_CHARS + 1)),
        ),
    )
    check(
        "run_panel rejects oversized text before model use",
        expect_raises(PitchTooLongError, lambda: run_panel(
            "x" * (MAX_PITCH_CHARS + 1))),
    )
    check(
        "pitch validator rejects blank input",
        expect_raises(ValueError, lambda: validate_pitch(" \n\t ")),
    )
    check(
        "run_panel rejects blank input before provider configuration",
        expect_raises(ValueError, lambda: run_panel("  ")),
    )

    limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
    limiter.check("client-a", now=100)
    limiter.check("client-a", now=101)
    check(
        "rate limiter rejects requests over its window quota",
        expect_raises(RateLimitExceededError,
                      lambda: limiter.check("client-a", now=102)),
    )
    limiter.check("client-b", now=102)
    limiter.check("client-a", now=161)
    check("rate limiter separates clients and expires old requests", True)

    provider_budget = SlidingWindowRequestBudget([(10, 60), (500, 86400)])
    provider_budget.reserve(PANEL_PROVIDER_REQUESTS, now=100)
    check(
        "provider budget accounts for a complete seven-call panel",
        expect_raises(RateLimitExceededError,
                      lambda: provider_budget.reserve(
                          PANEL_PROVIDER_REQUESTS, now=101)),
    )
    provider_budget.reserve(PANEL_PROVIDER_REQUESTS, now=161)
    check("provider minute budget resets after its rolling window", True)

    daily_provider_budget = SlidingWindowRequestBudget(
        [(10_000, 60), (500, 86400)])
    for _ in range(500 // PANEL_PROVIDER_REQUESTS):
        daily_provider_budget.reserve(PANEL_PROVIDER_REQUESTS, now=200)
    check(
        "provider daily budget prevents exceeding the 500-request ceiling",
        expect_raises(RateLimitExceededError,
                      lambda: daily_provider_budget.reserve(
                          PANEL_PROVIDER_REQUESTS, now=201)),
    )
    check(
        "provider budget rejects a window too small for one complete panel",
        expect_raises(
            ValueError,
            lambda: SlidingWindowRequestBudget([(6, 60)]).reserve(
                PANEL_PROVIDER_REQUESTS, now=300),
        ),
    )

    exhausted_budget = SlidingWindowRequestBudget([(10, 60), (500, 86400)])
    exhausted_budget.reserve(10, now=time.monotonic())
    with (
        patch.dict(os.environ, {"GROQ_API_KEY": "offline-test-key"}),
        patch("abuse_controls._provider_request_budget", exhausted_budget),
        patch("panel.build_panel") as build_panel_mock,
    ):
        check(
            "run_panel rejects over-budget work before building the graph",
            expect_raises(ProviderBudgetExceededError,
                          lambda: run_panel("A test pitch"))
            and not build_panel_mock.called,
        )

    from contextlib import ExitStack

    slots = ExitStack()
    for _ in range(MAX_CONCURRENT_PANEL_RUNS):
        slots.enter_context(panel_execution_slot())
    check(
        "panel execution rejects work over its concurrency cap",
        expect_raises(PanelBusyError,
                      lambda: panel_execution_slot().__enter__()),
    )
    slots.close()

    with open(os.path.join(HERE, "sample_pitch.txt"), encoding="utf-8") as f:
        pitch = f.read()
    check("sample pitch loads and is substantial",
          len(pitch) > 300 and "TiffinAI" in pitch)

    with tempfile.TemporaryDirectory() as temp_dir:
        valid_path = os.path.join(temp_dir, "pitch.txt")
        with open(valid_path, "w", encoding="utf-8") as f:
            f.write("A temporary pitch.")
        check("CLI loads a UTF-8 pitch file", load_pitch([valid_path]) ==
              "A temporary pitch.")
        check(
            "CLI reports a missing pitch file safely",
            expect_raises(PitchFileError, lambda: load_pitch([
                os.path.join(temp_dir, "missing.txt")])),
        )
        invalid_path = os.path.join(temp_dir, "invalid.txt")
        with open(invalid_path, "wb") as f:
            f.write(b"\xff")
        check(
            "CLI rejects a non-UTF-8 pitch file safely",
            expect_raises(PitchFileError, lambda: load_pitch([invalid_path])),
        )

    class FakeStructuredModel:
        def __init__(self, shark):
            self.shark = shark

        def invoke(self, prompt):
            workflow_prompts.append(prompt)
            return Verdict(shark="untrusted attribution", stance="PASS",
                           reasoning="The unit economics need proof.")

    class FakeHostModel:
        def invoke(self, prompt):
            workflow_prompts.append(prompt)
            return type("FakeResponse", (), {"content": "Panel summary."})()

    class FakeChatGroq:
        def __init__(self, **_kwargs):
            workflow_client_configs.append(_kwargs)

        def with_structured_output(self, _schema, **_kwargs):
            return FakeStructuredModel(self)

        def invoke(self, _prompt):
            return FakeHostModel().invoke(_prompt)

    workflow_prompts = []
    workflow_client_configs = []
    workflow_pitch = "Offline workflow test pitch."
    with (
        patch.dict(os.environ, {"GROQ_API_KEY": "offline-test-key"}),
        patch("panel.ChatGroq", FakeChatGroq),
    ):
        workflow_result = run_panel(workflow_pitch)
    check("fake-client workflow returns all three final verdicts",
          len(workflow_result["final"]) == 3)
    check("workflow overwrites model-provided shark attribution",
          [v.shark for v in workflow_result["final"]] ==
          [s.name for s in SHARKS])
    check("fake-client workflow returns host summary",
          workflow_result["summary"] == "Panel summary.")
    check("fake-client workflow makes six verdict calls and one host call",
          len(workflow_prompts) == 7)
    check("workflow clients use bounded retries, output, and request timeouts",
          len(workflow_client_configs) == 7 and all(
              config["max_retries"] == 0
              and config["max_tokens"] <= 5000
              and 0 < config["timeout"] <= GROQ_REQUEST_TIMEOUT_SECONDS
              for config in workflow_client_configs))
    check("workflow prompts include the pitch", all(
          workflow_pitch in "\n".join(
              str(message.content) for message in prompt)
          for prompt in workflow_prompts))

    class SlowStructuredModel:
        def invoke(self, _prompt):
            slow_call_count[0] += 1
            time.sleep(0.25)
            return Verdict(shark="model", stance="PASS",
                           reasoning="The test timed out.")

    class SlowChatGroq:
        def __init__(self, **_kwargs):
            pass

        def with_structured_output(self, _schema, **_kwargs):
            return SlowStructuredModel()

    slow_call_count = [0]
    with (
        patch.dict(os.environ, {"GROQ_API_KEY": "offline-test-key"}),
        patch("abuse_controls.MAX_PANEL_DURATION_SECONDS", 0.2),
        patch(
            "abuse_controls._provider_request_budget",
            SlidingWindowRequestBudget([(1000, 60), (1000, 86400)]),
        ),
        patch("panel.ChatGroq", SlowChatGroq),
    ):
        check(
            "overall panel deadline stops later workflow requests",
            expect_raises(
                PanelDeadlineExceededError,
                lambda: run_panel("A test pitch"),
            ) and slow_call_count[0] == 1,
        )

    rate_response = httpx.Response(429, request=timeout_request)
    rate_error = RateLimitError(
        "private provider detail", response=rate_response,
        body={"detail": "sensitive"},
    )
    partial_call_count = [0]

    class PartialFailureModel:
        def invoke(self, _prompt):
            partial_call_count[0] += 1
            if partial_call_count[0] == 3:
                raise rate_error
            return Verdict(shark="model", stance="PASS",
                           reasoning="A test verdict.")

    class PartialFailureChatGroq:
        def __init__(self, **_kwargs):
            pass

        def with_structured_output(self, _schema, **_kwargs):
            return PartialFailureModel()

        def invoke(self, _prompt):
            raise AssertionError("host must not run after a partial failure")

    with patch.dict(os.environ, {"GROQ_API_KEY": "offline-test-key"}), patch(
        "abuse_controls._provider_request_budget",
        SlidingWindowRequestBudget([(1000, 60), (1000, 86400)]),
    ), patch("panel.ChatGroq", PartialFailureChatGroq):
        partial_message = ""
        try:
            run_panel("A test pitch")
        except ProviderRequestError as exc:
            partial_message = str(exc)
    check(
        "partial provider failure stops workflow with a safe rate-limit message",
        partial_call_count[0] == 3
        and partial_message == "Groq's request limit was reached. Please try again later.",
    )

    from streamlit.testing.v1 import AppTest

    with patch.dict(os.environ, {"GROQ_API_KEY": ""}):
        app = AppTest.from_file(os.path.join(HERE, "app.py")).run(timeout=15)
        check("Streamlit UI loads without script exceptions",
              len(app.exception) == 0)
        check("Streamlit UI discloses Groq pitch processing", any(
            "sent to Groq" in block.value for block in app.markdown))
        app.button[0].click()
        app.run()
        check("UI shows a safe missing-key error", len(app.exception) == 0 and any(
            "GROQ_API_KEY is not configured" in error.value for error in app.error))
        app.text_area[0].set_value("x" * (MAX_PITCH_CHARS + 1))
        app.button[0].click()
        app.run()
        check("UI rejects oversized pitches before provider calls",
              len(app.exception) == 0 and any(
                  "Pitch is too long" in error.value for error in app.error))

    print("\n✅ All offline tests passed.")


if __name__ == "__main__":
    main()
