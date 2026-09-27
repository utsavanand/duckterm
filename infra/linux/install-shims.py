"""Run as the workspace user after deploying the execution-only TLS client."""

from duckterm.agents import mcp_install

for name in ("github", "railway", "porkbun"):
    for install in (mcp_install.claude_install, mcp_install.codex_install):
        install(
            name,
            "/opt/duckterm/venv/bin/duckterm",
            ["connector-run", name],
            env={"DUCKTERM_HOSTED": "1"},
        )
print("Installed hosted connector launchers without provider credentials")
