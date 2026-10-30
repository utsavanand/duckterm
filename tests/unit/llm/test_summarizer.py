import os
import subprocess
import urllib.error
from collections.abc import Iterator

import pytest

from duckterm.llm.summarizer import (
    build_prompt,
    mechanical_summary,
    summarize,
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # No env backends AND no auto-detectable agent on PATH, so summarize() takes
    # the true no-backend path.
    for k in ("DUCKTERM_SUMMARIZER_CMD", "DUCKTERM_SUMMARIZER_URL", "DUCKTERM_SUMMARIZER"):
        monkeypatch.delenv(k, raising=False)
    import duckterm.llm.summarizer as s

    monkeypatch.setattr(s.shutil, "which", lambda _b: None)
    yield


def test_no_backend_returns_empty_none(clean_env: None) -> None:
    result = summarize("anything", tier="fast")
    assert result.backend == "none"
    assert result.text == ""
    assert result.failure_reason == "no_provider"


def test_cli_backend_runs_command(clean_env: None) -> None:
    # A trivial "summarizer" that echoes a fixed line.
    os.environ["DUCKTERM_SUMMARIZER_CMD"] = "printf 'did the thing'"
    result = summarize("the prompt", tier="fast")
    assert result.backend == "cli"
    assert result.text == "did the thing"


def test_cli_backend_failure_falls_back_to_none(clean_env: None) -> None:
    os.environ["DUCKTERM_SUMMARIZER_CMD"] = "false"
    result = summarize("x", tier="fast")
    assert result.backend == "none"
    assert result.failure_reason == "provider_failed"


def test_cli_subprocess_marks_itself_internal(clean_env: None) -> None:
    # The summarizer runs an agent (`claude -p`) which inherits Duckterm's
    # hooks. Without DUCKTERM_INTERNAL the subprocess would report a phantom
    # session back into Duckterm on every checkpoint. Prove the env reaches it.
    os.environ["DUCKTERM_SUMMARIZER_CMD"] = 'printf "%s" "$DUCKTERM_INTERNAL"'
    result = summarize("x", tier="fast")
    assert result.backend == "cli"
    assert result.text == "1"


def test_mechanical_summary_states_intent_and_activity() -> None:
    s = mechanical_summary("add login", "5 events; tools used: Edit, Bash.")
    assert "add login" in s
    assert "5 events" in s


def test_mechanical_summary_handles_missing_intent() -> None:
    s = mechanical_summary("", "3 events.")
    assert "no stated intent" in s


def test_build_prompt_includes_all_sections() -> None:
    p = build_prompt("ship feature", "user: hi", "10 events.")
    assert "ship feature" in p
    assert "10 events." in p
    assert "user: hi" in p


def test_auto_detects_installed_harness(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from duckterm.llm import backend

    # Pretend only codex is installed.
    monkeypatch.setattr(
        backend.shutil, "which", lambda b: "/usr/bin/codex" if b == "codex" else None
    )
    assert backend.harness() == "codex"
    assert backend.harness("claude-code") is None


def test_summarizer_off_disables_autodetect_only(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import duckterm.llm.summarizer as s

    # off disables the auto-detect fallback...
    monkeypatch.delenv("DUCKTERM_SUMMARIZER_CMD", raising=False)
    monkeypatch.delenv("DUCKTERM_SUMMARIZER_URL", raising=False)
    monkeypatch.setenv("DUCKTERM_SUMMARIZER", "off")
    monkeypatch.setattr(s.shutil, "which", lambda _b: "/usr/bin/claude")
    assert s.summarize("x", tier="fast").backend == "none"
    assert s.summarize("x", tier="fast").failure_reason == "disabled"

    # ...but an explicitly-set backend still wins.
    monkeypatch.setenv("DUCKTERM_SUMMARIZER_CMD", "printf done")
    assert s.summarize("x", tier="fast").text == "done"


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (subprocess.TimeoutExpired("test", 60), "provider_timeout"),
        (OSError("private detail"), "provider_failed"),
    ],
)
def test_cli_failure_category_excludes_raw_output(clean_env, monkeypatch, failure, reason):
    import duckterm.llm.summarizer as s

    monkeypatch.setenv("DUCKTERM_SUMMARIZER_CMD", "test-provider")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(s.subprocess, "run", fail)
    result = s.summarize("x", tier="fast")
    assert result.failure_reason == reason
    assert result.text == ""


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (TimeoutError("private detail"), "provider_timeout"),
        (urllib.error.URLError(TimeoutError("private detail")), "provider_timeout"),
        (urllib.error.URLError("private detail"), "provider_failed"),
    ],
)
def test_http_failure_category_excludes_raw_output(clean_env, monkeypatch, failure, reason):
    import duckterm.llm.summarizer as s

    monkeypatch.setenv("DUCKTERM_SUMMARIZER_URL", "https://example.invalid/test")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(s.urllib.request, "urlopen", fail)
    result = s.summarize("x", tier="fast")
    assert result.failure_reason == reason
    assert result.text == ""


@pytest.mark.parametrize(
    ("binary", "harness", "tier", "model", "effort"),
    [
        ("claude", "claude-code", "fast", "haiku", "low"),
        ("claude", "claude-code", "deep", "sonnet", "medium"),
        ("codex", "codex", "fast", "", "low"),
    ],
)
def test_auto_summary_uses_bounded_isolated_runner(
    clean_env, monkeypatch, binary, harness, tier, model, effort
):
    from duckterm import memory_provider
    from duckterm.llm import summarizer

    monkeypatch.setattr(
        summarizer.shutil, "which", lambda b: "/fixture/" + b if b == binary else None
    )

    def legacy(*args):
        raise AssertionError("Unisolated CLI runner must not be used")

    monkeypatch.setattr(summarizer, "_cli_summary", legacy)
    calls = []

    async def generate(selected, chosen, prompt, schema, **options):
        calls.append((selected, chosen, prompt, schema, options))
        return "Prepared summary"

    monkeypatch.setattr(memory_provider, "generate", generate)
    result = summarizer.summarize("Original evidence", tier=tier)
    assert result.text == "Prepared summary" and result.backend == "cli"
    assert calls == [
        (harness, model, "Original evidence", None, {"user_settings": True, "effort": effort})
    ]


def test_pinned_claude_model_wins_over_tier_model(clean_env, monkeypatch):
    from duckterm import memory_provider
    from duckterm.llm import summarizer

    monkeypatch.setattr(summarizer.shutil, "which", lambda b: "/fixture/claude")
    models = []

    async def generate(selected, chosen, prompt, schema, **options):
        models.append(chosen)
        return "ok"

    monkeypatch.setattr(memory_provider, "generate", generate)
    summarizer.summarize("x", tier="deep", claude_model="opus")
    assert models == ["opus"]


@pytest.mark.parametrize("timed_out", [True, False])
def test_auto_summary_runner_failure_stays_explicit(clean_env, monkeypatch, timed_out):
    from duckterm import memory_provider
    from duckterm.core.session_api import APIError
    from duckterm.llm import summarizer

    monkeypatch.setattr(
        summarizer.shutil, "which", lambda b: "/fixture/claude" if b == "claude" else None
    )

    def legacy(*args):
        raise AssertionError("Unisolated CLI runner must not be used")

    monkeypatch.setattr(summarizer, "_cli_summary", legacy)

    async def generate(*args, **kwargs):
        raise (
            memory_provider.BatchTimeout() if timed_out else APIError(503, "private provider data")
        )

    monkeypatch.setattr(memory_provider, "generate", generate)
    result = summarizer.summarize("Original evidence", tier="deep")
    assert result.text == "" and result.backend == "none"
    assert result.failure_reason == ("provider_timeout" if timed_out else "provider_failed")


def test_copilot_is_the_last_resort_without_claude_or_codex(clean_env, monkeypatch):
    from duckterm.llm import summarizer

    monkeypatch.setattr(
        summarizer.shutil, "which", lambda b: "/fixture/copilot" if b == "copilot" else None
    )
    calls = []

    def cli(command, prompt):
        calls.append((command, prompt))
        return summarizer.Summary("Copilot result", "cli")

    monkeypatch.setattr(summarizer, "_cli_summary", cli)
    assert summarizer.summarize("fixture", tier="fast").text == "Copilot result"
    assert calls == [("copilot -p", "fixture")]
