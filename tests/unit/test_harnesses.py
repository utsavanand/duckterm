from duckterm.harnesses import REGISTRY, installable_agents, runtime_for


def test_opencode_cannot_silently_fall_back_to_generic(monkeypatch) -> None:
    import pytest

    from duckterm.harnesses import infer_runtime
    from duckterm.runtimes.opencode_options import snapshot

    assert infer_runtime("opencode --model 'local/org/model'") == "opencode"
    assert runtime_for("opencode", "opencode").name == "opencode"
    monkeypatch.delitem(REGISTRY, "opencode")
    assert snapshot()["launch"]["available"] is False
    with pytest.raises(ValueError, match="not available"):
        runtime_for("opencode", "opencode")


def test_runtime_for_resolves_each_agent() -> None:
    assert runtime_for("claude-code", "claude").name == "claude-code"
    assert runtime_for("codex", "codex").name == "codex"
    assert runtime_for("copilot", "copilot").name == "copilot"


def test_runtime_for_unknown_falls_back_to_generic() -> None:
    assert runtime_for("nope", "aider").name == "generic"
    assert runtime_for(None, "aider").name == "generic"


def test_installable_agents_excludes_generic() -> None:
    # generic has no hook adapter, so it can't be wired for watched sessions.
    agents = installable_agents()
    assert "generic" not in agents
    assert {"claude-code", "codex", "copilot"} <= set(agents)


def test_every_registry_entry_builds_a_runtime() -> None:
    for name, cls in REGISTRY.items():
        assert cls("x").name == name


def test_installable_agents_carry_a_hook_spec() -> None:
    for name in installable_agents():
        spec = REGISTRY[name].hook_spec
        assert spec is not None
        # build/strip round-trip: stripping a freshly built config removes
        # exactly our entries, preserving the shared format version.
        built = spec.build({}, "/path/to/duckterm-hook.sh", name)
        assert spec.strip(built) == ({"version": 1} if name == "copilot" else {})


def test_native_preparation_arguments_are_not_replayed_as_the_user_command():
    runtime = runtime_for("opencode", "opencode --model fixture/alpha")
    saved = runtime.command_for_history(["opencode", "/project", "--port", "1234"])
    assert saved == "opencode --model fixture/alpha"
    assert runtime_for("opencode", saved).model == "fixture/alpha"
