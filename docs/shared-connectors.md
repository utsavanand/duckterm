# Shared connectors

Configure a provider on the connector host once. Agent machines enroll with a
workspace TLS certificate and relay MCP traffic; they do not receive GitHub
or other provider credentials. Claude Code and Codex are
registered automatically. Other MCP clients can use the same
`duckterm connector-run NAME` command on an enrolled machine.

## Connector host

The existing Google Cloud connector VM is the recommended host: it remains
available when a developer laptop sleeps. Keep its port private. Use the
existing private network for cloud workspaces and an authenticated SSH/IAP
port-forward for a local machine. A workspace certificate is still required
inside the tunnel.

`duckterm connector-serve --config /etc/duckterm/connector-service.json` runs the
mutual-TLS service. Existing deployments may call the equivalent
`connector-service` command from the remote-session build.

The administrator-owned configuration contains `certificate`, `private_key`,
`client_ca`, `bind`, `port`, `project`, a `workspaces` mapping from certificate
common name to allowed connector names, and a `connectors` mapping. Each enabled
hosted connector pins a numeric Secret Manager version in `reference`.
Disabling a connector, changing its version, or withdrawing a workspace grant
closes existing relays. Provider-side actions already accepted are not undone.

Credential payloads (the values are secrets, never command-line arguments):

| Connector | Secret Manager JSON fields |
| --- | --- |
| GitHub / Railway | `token` |
| Porkbun | `token`, `secret` |


The administrator can also run a local credential host with `mode: "local"`.
It resolves the host's existing Duckterm/CLI connections and observes Duckterm's
enable/disable state. A local credential host must not itself be configured as
a relay client. Local same-user filesystem access is not a credential isolation
boundary; use the separate Google Cloud connector VM for remote workspaces.

## Agent enrollment

Issue each workspace a client certificate using the existing connector CA.
Keep the signing key off agent machines. A client JSON file contains:

```json
{
  "host": "127.0.0.1",
  "port": 8443,
  "server_name": "CONNECTOR_CERTIFICATE_DNS_NAME",
  "server_ca": "/path/to/ca.crt",
  "certificate": "/path/to/workspace.crt",
  "private_key": "/path/to/workspace.key"
}
```

The localhost address above assumes an SSH/IAP port forward to the connector VM.
Remote workspaces can use its private address instead. Then run:

```sh
duckterm connector-attach --config /path/to/client.json
```

This validates access, saves only the relay settings and certificate paths, and
registers permitted connectors for Claude Code and Codex. Restart agent sessions
to load changed MCP registrations. New workspaces still need enrollment; users
never repeat provider sign-in for each agent. Explicitly configured relay clients
fail closed when the host is unreachable; they never fall back to local credentials.
A new connector grant added later requires rerunning enrollment to register its
name; provider authentication remains centralized.

## Validation and QA

Test with fake credentials and isolated homes first. The TLS integration test
checks missing-certificate rejection and live relay revocation. Provider tests
check local login detection and no provider credential resolution on clients.
