# Hugging Face connector

In **Connectors**, select **Connect** beside **Hugging Face** to make model,
dataset, and documentation discovery available to new Claude Code and Codex
sessions. Public discovery needs no account. Install Node.js 22+ with npm first;
the first agent connection downloads the pinned `mcp-remote` bridge via `npx`.

For authenticated discovery, select **Add optional token** before connecting,
or set `HF_TOKEN` in the environment inherited by Duckterm and its agent sessions.
`HF_TOKEN` takes precedence over a saved token. Saved tokens use Duckterm's secret
store (macOS Keychain, or a private file on other platforms) and are resolved at
launch, never written into agent configs or command arguments. Disconnecting
removes both agent registrations and the saved token; it leaves `HF_TOKEN` alone.
To change a saved token, disconnect and reconnect with the new token.

The connector uses the official hosted server's search preset:
`https://huggingface.co/mcp?bouquet=search`. It advertises discovery tools without
opting into Spaces execution or compute jobs. Tool selection is a discovery
setting, not a permissions boundary; token permissions still apply.

