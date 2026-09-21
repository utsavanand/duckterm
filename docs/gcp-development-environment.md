# Duckterm GCP development environment

Provisioned September 20, 2026 (Pacific), with the user's approval, in the
separate `remote-session` worktree. The workspace service has been deployed for QA. The native remote-host UI is
implemented in the worktree; the connector service is not deployed yet.

## Inventory

- Project: `duckterm-20260920` (number `540731020186`), display name Duckterm.
- Billing: My Billing Account 1, ending `FFA08E`.
- VM: `duckterm-dev-1`, `us-central1-a`, on-demand `e2-medium` (4 GiB memory).
- OS: Ubuntu 24.04 LTS; 30 GiB standard persistent boot disk. Disk deletion is
  independent of VM deletion, to prevent accidental loss of session files.
- Custom network/subnet: `duckterm-dev` / `duckterm-dev-us-central1`.
- SSH: OS Login over IAP. Only TCP 22 from Google's `35.235.240.0/20` IAP range
  is allowed into this VM. Its ephemeral external IPv4 provides outbound
  internet access; the dashboard is not publicly exposed.
- Secure Boot, vTPM, and integrity monitoring are enabled.
- No service account is attached to the agent VM. The new project's automatic
  Editor grant to its unused default compute account was removed.
- Broker identity: `duckterm-broker@duckterm-20260920.iam.gserviceaccount.com`.
  No service-account key was created. Its secret-access grants are scoped to
  the three connector entries, not the project.
- Empty Secret Manager entries: `duckterm-github`, `duckterm-railway`,
  `duckterm-porkbun`. No credential versions have been added.
- The local dedicated SSH identity is `~/.ssh/duckterm_gcp`; only its public key
  is registered with OS Login. Never commit or copy its private key into an image.

Machine-readable configuration: [development.json](../infra/gcp/development.json).
The provisioning startup script is [bootstrap.sh](../infra/gcp/bootstrap.sh).

The `duckterm` OS account is for agents and has no administrative group
membership. The `duckterm-broker` OS account is reserved for the future service.
Do not attach the broker's cloud identity to the unrestricted agent environment.
Broker deployment must enforce the boundary described in the implementation plan.

## Cost

Approximate baseline before credits, using 730 compute/IP hours per month:

| Resource | USD/month |
|---|---:|
| e2-medium compute | 24.46 |
| 30 GiB standard persistent disk | 1.20 |
| In-use external IPv4 | 3.65 |
| Baseline total | 29.31 |

Allow approximately $29–35/month for this initial lightly used VM, excluding
model-provider charges, tax, and later broker compute. Network traffic is
variable; this range is not a hard cap. No assumption is made about free-trial
eligibility or remaining credits.

A $40 monthly project-filtered budget alert notifies billing recipients at 50%,
80%, and 100%. It measures usage before credits and does not stop resources.
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

## Remaining setup

- Complete live remote-host QA and deploy the separate connector service after the
  user decides on its additional cost.
- Install and authorize agent CLIs through official provider browser flows.
- Add real connector credentials through secure input, not chat or shell argv.
- Complete application, provider, reboot, and overnight QA from the approved plan.

Infrastructure verification passed: bootstrap completed, verified-host-key SSH
worked, the same tmux process survived separate SSH connections, the agent user
could not read the broker home, and no cloud identity token was available.
The disposable process and temporary local tunnel were cleaned up. Results:
[verification.json](../infra/gcp/verification.json). A tmux smoke test does not
substitute for end-to-end Duckterm QA.

## Pricing references

- https://cloud.google.com/products/compute/pricing/general-purpose
- https://cloud.google.com/products/block-storage
- https://cloud.google.com/vpc/pricing
- https://cloud.google.com/secret-manager/pricing

## Implementation progress

The approved worktree now includes the native SSH host picker, reconnect loop,
connector identity selection and disable/forget controls, secure file and Keychain
access, bounded pane logs, and an unprivileged systemd workspace service.

Validation on September 20: full gate passed (391 Python tests, 51 frontend
unit tests, 32 Playwright tests). Native build and two SSH-configuration tests
passed. Additional secret-store reference checks passed separately.

Claude Code 2.1.267 is installed under the remote `duckterm` account and has not
been signed in. No connector credentials or additional broker VM were created.

These checks do not complete acceptance: real model login, live persistence,
connector-service isolation, credential flows, and overnight tests remain.
Nothing has been merged into main.

The user subsequently approved the second VM. `duckterm-connectors-dev-1`
(`e2-micro`, 10 GiB disk, private address `10.80.0.3`) now runs the connector
service. TCP 8443 is restricted to the workspace tag and requires a trusted
client certificate. The project budget alert was updated to $50. Allow roughly
$40–50/month for both lightly used VMs before model usage and tax.

Live checks passed: the two Duckterm sessions kept their PIDs across separate
SSH connections and a systemd restart; the browser replayed their terminal
screen; workspace-to-broker mutual TLS returned status; the agent metadata token
endpoint returned 404. The broker successfully read a synthetic Secret Manager
value using its attached identity. That test version (`duckterm-github`, version
1) was then destroyed. No real connector credentials have been added.

Codex 0.155.1 and Claude 2.1.267 are installed on the workspace. GitHub MCP 1.12.2,
Railway 5.58.0, and Porkbun MCP 1.1.2 are installed on the broker. Porkbun requires
Python 3.13; uv 0.12.17 installed Python 3.13.15 into `/opt/duckterm/python`.
