# DuckCloud — run your ducks in the cloud

Status: designed 2026-09-25 at the owner's request (via the `product`
session), not implemented. Product name for the existing `remote-session`
work plus the setup experience on top of it. Owner has decided **GCP and AWS
both at launch**.

Builds on: [session-sharing-design.md](session-sharing-design.md) (the relay
and live sharing), [accounts-and-handoff-design.md](accounts-and-handoff-design.md)
(identity), and the remote-workspace section of
[architecture.md](architecture.md) (the two-VM constraints already proven).

## What it is, in one line

DuckTerm provisions a VM **in the user's own cloud account**, runs sessions
there, and they keep running when the laptop closes.

Bring-your-own-cloud, not hosted by us: credentials, code, and model auth
stay in the user's account. Hosted-by-us is deferred until individual
developers without a cloud account ask for it — billing, tenant isolation,
and on-call are a different product.

## The honest framing of scope

Most of DuckCloud already exists on the `remote-session` branch: VM
provisioning, the connector VM split, mutual-TLS bridges, "Run on: This Mac
/ Remote", conversation resume on the remote box. **The new work is almost
entirely the setup experience**, and the second cloud.

That matters for sequencing: DuckCloud is not a new build, it is a merge
plus an onboarding flow. The branch is the long pole, and it gets harder to
merge every day main moves (see "Sequencing" below).

## Two clouds at launch — what actually differs

The owner decided both GCP and AWS. The architecture already keeps
cloud-specific surface small, and it must stay small: one provisioning
interface, two implementations, nothing else in the product aware of which
cloud it is.

| Concern | GCP (proven on the branch) | AWS (new) |
| --- | --- | --- |
| Sign-in | `gcloud auth login` (device/browser flow the user already has) | `aws sso login`, or existing credentials in `~/.aws` |
| Compute | Compute Engine instance | EC2 instance |
| Secrets, connector VM | Secret Manager, attached service-account identity | Secrets Manager, attached IAM instance profile |
| Admin access | SSH / IAP | SSH / SSM Session Manager |
| Snapshots | persistent-disk snapshot policy | EBS snapshot lifecycle |

**Use the user's existing CLI login; do not build an OAuth flow.** Every
target user already has `gcloud` or `aws` configured, both CLIs own their
own refresh, and asking them to grant a third party cloud-wide OAuth scope
is a much bigger trust ask than "we ran a command you already run". This is
the same reasoning that keeps connectors riding existing CLI logins rather
than storing tokens, and the same reasoning behind
`duckterm backup --to gs://…` using the user's own gcloud auth.

If neither CLI is installed, say so and link the install page. Do not
install cloud tooling on the user's machine.

## Setup: replace "have a project" with three questions

Today the branch assumes a prepared GCP project. The new flow, one screen:

1. **Which cloud** — detected from what is installed and signed in; if both,
   the user picks. If neither, stop with install instructions.
2. **Which account/project/region** — read from the CLI (`gcloud projects
   list`, `aws configure list-profiles`), defaulted to the active one.
3. **How big** — three named sizes with the hourly price next to each, not
   instance-type strings. "Small — 2 vCPU, 8 GB, ~$0.07/hr".

Then one button, and a progress log of exactly what is being created. Every
created resource is named with a `duckterm-` prefix so the user can find and
delete it in their own console without a teardown tool.

**Show the estimate before creating anything**, and state that it is the
user's bill, on their account, and that DuckTerm cannot cap it. Budget
alerts do not stop spend — the remote work already learned this and it must
not be implied otherwise.

## Cost and idle shutdown

This is the part users get burned by, so it is a v1 requirement, not a
later polish:

- **Running cost, visible in the dashboard** — hours since start × the rate
  quoted at provisioning. An estimate computed locally, clearly labelled as
  such; do not query billing APIs for a number that arrives days late.
- **Idle shutdown, on by default.** Stop the VM after N minutes with no
  session activity (default 30). Stopping a VM keeps the disk — sessions
  and worktrees survive — and stops the expensive part of the bill.
- **But stopping a VM terminates its processes**, exactly like a reboot.
  The remote work established this and the UI must not claim otherwise. So:
  warn before idle-stop, let the user pin a VM to stay up, and be explicit
  that resuming restarts sessions rather than continuing them.
- Disk still bills while stopped. Say so. Offer explicit **Delete** as the
  only way to stop paying entirely.

## Answers to the questions asked

- **GCP-only for v1?** No — owner decided both. The constraint is that the
  cloud-specific code stays behind one interface; if AWS starts leaking
  into the UI or the session model, that is the signal the abstraction is
  wrong.
- **How does sign-in work?** The user's existing CLI login for each cloud.
  No OAuth app, no stored cloud credentials, no service-account keys
  downloaded to the laptop (the branch already refuses this for the
  workspace VM).
- **Cost and idle shutdown?** Above: estimate before creating, running
  estimate in the dashboard, idle-stop on by default with honest warnings
  about process termination and disk billing.
- **Does this reorder B2 connectors?** No, and the two are entangled rather
  than competing: `remote-session` already rewrote `connectors.py` (689
  lines: credential-source selection, identity verification, revocation,
  write scoping). The connector wizard should be designed against that
  shape, not against `main`. Fixing connectors and merging the branch are
  the same piece of work sequenced together, not a choice between them.

## Sequencing

1. **Merge `remote-session`.** Blocked on owner approvals for VM testing.
   This is the long pole and the risk: the branch now carries the remote
   workspace, session migration, and the connector rewrite, while five
   sessions push to main daily. Every day of delay raises the merge cost.
2. **Setup flow + cost/idle controls** on GCP, the proven path.
3. **AWS implementation** behind the same interface.
4. **Collaboration through the relay** — live sharing, which already
   depends on the remote workspace for the reason recorded in the roadmap:
   a sleeping laptop kills a share.

## Explicitly not in v1

Hosted-by-us; multi-VM or autoscaling; a teardown/cost console beyond the
running estimate; billing integration; anything that stores the user's
cloud credentials on our side or on the laptop beyond what their CLI
already keeps.
