# Shared connectors

Configure a provider on the connector host once. Agent machines enroll with a
workspace TLS certificate and relay MCP traffic; they do not receive Gmail,
Google Cloud, GitHub, or other provider credentials. Claude Code and Codex are
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
| Hugging Face | Optional `token`; `anonymous: true` in the connector entry needs no secret |
| Gmail | `oauth_json`, `credentials_json` (each a JSON-encoded string) |
| Google Cloud | `credentials_json` (authorized-user or service-account JSON), optional `project` |

Google credential files are materialized with mode 0600 in a private temporary
broker directory and removed when the relay ends. They never go to clients.
The GCP adapter isolates gcloud's config directory and sets
`CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE` to the selected credential. Assign only
the cloud permissions intended for connected agents. The broker's existing
Secret Manager identity must not be used implicitly as the GCP connector account.

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

## Personal Gmail

This version provides read-only search, messages, threads, and labels through the
pinned local `@artymclabin/gmail-mcp@1.2.3` server. It does not enable sending or
draft writes. OAuth scopes include draft sending along with draft creation, so
that broader permission should be a separate product choice.

1. In Google Cloud Console, enable Gmail API in an owned project.
2. Configure External OAuth consent and add the personal Gmail address as a test
   user. Create a Desktop app OAuth client.
3. Save its downloaded JSON as `~/.gmail-mcp/gcp-oauth.keys.json` on the trusted
   setup machine (or select `GMAIL_OAUTH_PATH`).
4. Run `duckterm connector-auth gmail` and complete Google's consent screen.
   This requests only `gmail.readonly`. An External app left in Testing can
   require renewed authorization under Google's testing-token rules.
5. For a local host, Refresh and Connect Gmail in the dashboard. For the shared
   Google Cloud host, the administrator uploads the two JSON documents to its
   Gmail secret, grants that one secret to the broker service account, selects
   the version with `connector-admin`, and grants the desired workspaces access.

Refresh tokens remain on the connector host after enrollment. Ordinary disconnect
removes client registrations; revoke the grant in Google Account settings when
provider-side revocation is desired.

## Google Cloud

The pinned `@google-cloud/gcloud-mcp@0.5.3` server executes gcloud commands with
the configured account's permissions. Local setup reuses `gcloud auth login` and
the active project. Shared setup uses the explicit credential payload above and
requires gcloud plus Node.js 22+ on the broker. Never download a broad service
account key merely to populate this connector; prefer an explicitly scoped
user authorization or an established keyless identity flow.

## Validation and QA

Test with fake credentials and isolated homes first. The TLS integration test
checks missing-certificate rejection and live relay revocation. Provider tests
check private temporary files, local login detection, and no provider credential
resolution on clients. Live Gmail testing requires the user's own OAuth consent;
no email is sent as part of connector setup or automated QA.
