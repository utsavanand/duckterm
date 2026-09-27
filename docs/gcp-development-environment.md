# Duckterm GCP development environment

Provisioned September 20, 2026 (Pacific), in project `duckterm-20260920`
(number `540731020186`), billed to My Billing Account 1 ending `FFA08E`.
Implementation remains in the `remote-session` worktree; it is not merged into main.

## Inventory

| Machine | Configuration | Purpose |
|---|---|---|
| `duckterm-dev-1` | Ubuntu 24.04, e2-medium, 4 GiB RAM, 30 GiB pd-standard | Persistent agents and Duckterm on loopback port 4300 |
| `duckterm-connectors-dev-1` | Ubuntu 24.04, e2-micro, 1 GiB RAM, 10 GiB pd-standard | Connector service on private port 8443 |

Both machines use `us-central1-a`, network `duckterm-dev`, and subnet
`duckterm-dev-us-central1`. SSH accepts only IAP traffic with OS Login and
verified host keys. Ephemeral external IPv4 addresses provide outbound access;
the dashboard and connector port are not publicly exposed.
Secure Boot, vTPM, and integrity monitoring are enabled.

Agents run as the unprivileged `duckterm` Unix user. The workspace VM has no
attached cloud service account. The connector VM uses
`duckterm-broker@duckterm-20260920.iam.gserviceaccount.com`, with secretAccessor
on only `duckterm-github`, `duckterm-railway`, and `duckterm-porkbun`.
No downloaded service-account key exists. Mutual TLS authenticates the workspace
and broker; the signing key stays off both VMs. All workspace agents share
execution access to enabled integrations.

GitHub is enabled for `utsavanand`, using the user's explicitly selected local
GitHub CLI authorization copied directly into Secret Manager version 2.
Synthetic QA version 1 was destroyed. Railway and Porkbun remain disconnected.
No provider token was copied onto the agent VM. Model provider login caches
remain on the workspace, separate from connector credentials.

Both Codex 0.155.1 and Claude 2.1.267 have completed remote authorization.
The broker has GitHub MCP 1.12.2, Railway 5.58.0, and Porkbun MCP 1.1.2.
Porkbun uses Python 3.13.15 installed by uv 0.12.17.

Configuration: [development.json](../infra/gcp/development.json).
Operations: [remote-workspace-operations.md](remote-workspace-operations.md).

## Cost

Estimated baseline using 730 compute/IP hours per month, before credits:

| Resource | USD/month |
|---|---:|
| Workspace compute, disk, IPv4 | 29.31 |
| Connector compute, disk, IPv4 | 10.17 |
| Total | 39.48 |

Allow roughly $40–50/month for light development use, excluding model-provider
charges, taxes, and unusual network traffic. The $50 monthly project budget
alerts at 50%, 80%, and 100%; it does not cap spending or stop machines.
Budget ID: `801e1257-191c-446a-ae58-19c3186e6389`.

## Connect

Use your authenticated Google Cloud CLI:

```sh
gcloud compute ssh duckterm-dev-1 \
  --project=duckterm-20260920 --zone=us-central1-a \
  --tunnel-through-iap --ssh-key-file="$HOME/.ssh/duckterm_gcp" \
  --strict-host-key-checking=yes
```

Google retrieved the VM's public host keys into
`~/.ssh/google_compute_known_hosts`. The ED25519 fingerprint was also compared
against the authenticated serial-console output:
`SHA256:i+rOFkKBrch1Bp0vdnG9RkkUn7HWAbyAqhA8n+wpGcg`.

The administrative OS Login account can switch to the agent account:

```sh
sudo -iu duckterm
```

Do not run coding agents under the administrative OS Login account.

## Stop and restart

Stopping interrupts any running agents. Use only when their work can stop:

```sh
gcloud compute instances stop duckterm-dev-1 \
  --project=duckterm-20260920 --zone=us-central1-a
gcloud compute instances start duckterm-dev-1 \
  --project=duckterm-20260920 --zone=us-central1-a
```

Stopping ends VM compute charges; the disk remains billable. The external IP is
ephemeral and can change after stop/start; IAP connects by instance name.
Deleting the VM alone leaves its boot disk, deliberately. Any full teardown
must separately account for the disk and stored credentials.

## Validation status

Passed: full application gate, native build and SSH configuration tests,
real Keychain create/read/update/delete, official model login and text requests,
two persistent terminals surviving SSH disconnection and workspace service
restart, browser terminal replay, mutual TLS status, and broker Secret Manager
access. The agent metadata token endpoint returned 404.

Also passed: concurrent Claude and Codex coding sessions launched through
Duckterm, with three real Python tests each; native automatic SSH reconnect
after killing only its own tunnel; GitHub get_me through the broker; live
disable closing an active GitHub stream and rejecting reconnect, followed by
successful re-enable. The final gate passed 401 Python, 51 frontend, and 32
Playwright tests.

Pending: native interactive host switching, reboot recovery, extended connector
failure/rotation coverage, and the scheduled eight-hour persistence check.
Railway and Porkbun live tests are deferred; the user selected GitHub first. A text request alone does not
validate coding tools. A service restart preserves running tmux processes;
a VM reboot terminates them and must recover history/state accurately.

The overnight check is scheduled for September 21 at 13:11 UTC; inspect its
journal before recording a pass. Preserve the active probes until it runs.
Main has overlapping uncommitted changes that must be reconciled before merge.

## Pricing references

- https://cloud.google.com/products/compute/pricing/general-purpose
- https://cloud.google.com/products/block-storage
- https://cloud.google.com/vpc/pricing
- https://cloud.google.com/secret-manager/pricing
