# CDE / Remote Agent Sessions — On-Prem Architecture Reference

> Depth for SKILL.md Step 2 ("CDE / Remote Agent Execution") and Step 3
> ("CDE / Remote Agent Session Architecture"). Current as of **Sept 2026**.
> A Cloud Development Environment (CDE) is a remote, centrally-managed developer
> workspace — the developer's laptop becomes a thin client; the code, the toolchain
> and (increasingly) the AI coding agent all run on a server you control.

**Read this before writing the words "the codebase never leaves our network."** For a
CDE that runs AI coding agents, that sentence is usually false (§2). Everything else in
this document is downstream of getting §2 right.

---

## 1. CDE platform landscape

**What changed:** the 2022–2024 CDE field consolidated hard. Three of the five names
people still cite are gone as self-hostable products. **Current recommendation: Coder
(self-hosted) as default; Eclipse Che / OpenShift Dev Spaces as the OpenShift-native
conservative lane.**

| Platform | Verdict | Basis |
|---|---|---|
| **Coder** (self-hosted, Premium tier for governance) | ✅ **Default** | Ships the agent-governance surface the others don't: **Coder Agents** (self-hosted, model-agnostic, GA Sept 2026), **Agent Relay**, **Agent Firewall** (default-deny egress *for agents*), **AI Gateway** (prompt audit trail, per-user token/spend attribution, SIEM export). Terraform-defined workspaces; runs on your k8s/VMs. |
| **Eclipse Che / Red Hat OpenShift Dev Spaces** | ✅ Conservative lane | Fully open source, OpenShift-native, devfile-driven. Correct choice if OpenShift is already the platform and Red Hat support is the procurement requirement. No built-in agent governance — you build §6–§8 yourself. |
| **OpenHands** (MIT) | ✅ Agent-runtime fallback | Open-source agent harness with Docker sandbox isolation + a self-hosted Enterprise tier. Use as the agent runtime when model-vendor egress is disallowed entirely and you must point the harness at a self-hosted model (§2 option b). Not a CDE — pair it with Coder or Che. |
| ~~**Gitpod / Ona**~~ | ❌ **Structurally disqualified** | Self-managed killed Dec 2022; repo archived Apr 2026; rebranded "Ona"; OpenAI announced acquiring Ona June 2026. It is now a managed service owned by a model vendor — the opposite of an air-gap posture. |
| ~~**DevPod**~~ | ❌ Superseded | Stalled — no commits since Nov 2025. Fine as a client-side pilot tool for one developer; do not build a program on it. |
| ~~**Daytona**~~ | ❌ Superseded | Pivoted to AI-code-execution infrastructure and took the core closed-source June 2026. No longer a human CDE. |

**Selection rule:** if the design needs per-developer *agent* governance (egress control,
spend attribution, prompt audit), that is a Coder Premium line item or a
build-it-yourself project. Do not assume a CDE gives you it for free.

---

## 2. The air-gap caveat — the single most common factual error

**What changed:** nothing technical — this was always true and is routinely mis-stated.
**Current recommendation: never let a design claim network containment for a CDE that runs
a hosted-model agent, until the inference path is named and confirmed.**

Claude Code, Codex-style CLIs and every comparable agent are **clients of a hosted model**.
The *runtime* sits on your server; the *inference* does not. Prompt context — which for a
coding agent means source files, diffs, stack traces, config, and whatever the agent read to
answer the question — is transmitted to the model vendor regardless of where the process runs.
Moving the agent from a laptop to an on-prem CDE changes **who controls the disk**, not
**where the code is sent**.

The three honest options, and nothing else:

| Option | What it actually gives you | Cost |
|---|---|---|
| **(a) Enterprise zero-retention agreement + private routing** | Code still leaves, but under a contractual no-training/no-retention term, over a private path (AWS Bedrock / Google Vertex in your own account/VPC, not the public consumer endpoint). | Legal review + enterprise contract; the strongest *practical* answer for most fintechs. |
| **(b) Fully self-hosted model behind an OpenAI-compatible gateway** | Code genuinely does not leave. Point the agent CLI at the internal gateway base URL. | GPU capex/opex + model quality gap + ops (see §9). |
| **(c) No agents on that network segment** | Trivially compliant. | Developers use agents on a lower-classification segment with non-production code, or not at all. |

**Write the chosen option, with the contract reference or the gateway endpoint, into the
design doc's Security Posture Summary.** "We're on-prem so it's fine" is the failure mode
this section exists to kill.

---

## 3. Persistent agent sessions

**What changed:** long agentic runs (hours, not minutes) made session *durability* an
architecture concern rather than a personal-preference one. **Current recommendation:
`mosh → tmux attach` as the standard remote entry; tmux on the host as the durable substrate.**

| Concern | Recommendation | Notes |
|---|---|---|
| Multiplexer on the remote host | **tmux** | Ubiquitous, present in every base image, scriptable. Zellij is a nicer UX but needs manual install; the common 2026 pattern is **Zellij locally / tmux on the remote box**. |
| Transport over a bad link (>80 ms, flaky Wi-Fi, tethering) | **mosh** | UDP, roaming-tolerant, survives suspend/IP change and predicts local echo. Raw SSH drops the session; mosh does not. Standard entry: `mosh dev-host -- tmux attach -t work`. |
| Agent-level resume | Claude Code **`--resume`** (capture the session ID, resume later) | Resumes the *conversation*; does not resume a killed process tree. Complements tmux, does not replace it. |
| Browser / mobile attach | Claude Code **Remote Control** (research preview) | Useful for checking on a run from a phone. **Expires on extended inactivity** — so it is not the durable layer. tmux on the host remains the substrate for multi-hour unattended runs. |

**Design rule:** an unattended agent run must survive the developer closing their laptop.
That means the agent process is a child of a tmux session on the dev host, not a child of
the SSH connection.

### 3a. Runaway agents are a SECURITY control, not a finance one

**What changed:** the "Beyond Max Tokens" attack (Jan 2026) demonstrated a **malicious MCP
server steering agents into repetitive tool-call chains, inflating cost up to 658x**. That
reclassifies budget enforcement from FinOps hygiene to an availability/DoS control.

Documented 2026 incident classes — cite these when someone calls the caps paranoid:

| Incident | Loss | Mechanism |
|---|---|---|
| 2026-04-22 | **$48K of model spend in 14 hours**, one session | Research agent with no wall-clock or token ceiling |
| 2026-06-12 | **$6,531 AWS bill** | Agent re-created CloudFormation stacks on every error — retry loop with no idempotency or attempt cap |
| (4-agent loop) | **$47K over 11 days** | Multi-agent fan-out, no global budget across children |
| 2026-04-28 (Anthropic post-mortem) | — | Hook-recursion bug: a hook triggering itself with **no timeout and no depth limit** |
| Jan 2026 ("Beyond Max Tokens") | up to **658x** cost inflation | Malicious MCP server injecting repetitive tool-call chains |

Mandatory mitigations, all of them:

- **Hard per-session token budget and wall-clock budget** — enforced, not advisory.
- **`--max-turns` / max-turn caps** on every unattended run.
- **Loop detection** — identical tool call with identical arguments N times → abort.
- **Kill switch at the gateway, not at the agent.** An agent that is looping or compromised
  cannot be trusted to honour its own limit. The budget must be enforced by the thing
  holding the credential (Coder AI Gateway, LiteLLM, or your own OpenAI-compatible proxy).
- **Recursion depth limit on hooks and sub-agent spawning**, with a timeout on each.

### 3b. Session hijack — shared tmux socket

A shared `tmux attach` "pair programming" session means **Developer B inherits Developer A's
full agent authority and credentials, with zero attribution** — every action lands in the
audit log as A. In a regulated environment that is an attributable-access control failure,
not a convenience.

| Fix | Detail |
|---|---|
| Per-developer workspaces | One workspace per human. No shared dev host account. |
| Agent never runs as a shared account | The agent session inherits a *named* identity (§4). |
| Pairing through a recording broker | **Teleport session sharing/recording** or **Coder workspace sharing** — both record *both* identities. Never a shared UNIX socket. |

---

## 4. Zero-trust secrets and workload identity

**What changed:** HashiCorp Vault went BUSL and became an IBM product (acquisition closed
Feb 2025); OpenBao matured under Linux Foundation governance; SPIFFE/SPIRE reached **CNCF
Graduated**. **Current recommendation: OpenBao as the default for a greenfield build.**

| Layer | Default | Alternative / when |
|---|---|---|
| **Secrets store** | **OpenBao** (MPL-2.0, Linux Foundation-governed; v2.5.0 Feb 2026, v2.6 Aug 2026 adding per-namespace sealing) | **Vault** only if you need IBM enterprise support or a Vault-Enterprise-only feature. Vault is BUSL-licensed — for a new build that is a deliberate lock-in choice, not the neutral default it used to be. |
| **Workload identity** | **Phase 2 start:** Kubernetes ServiceAccount + OpenBao/Vault JWT auth. **End state:** **SPIFFE/SPIRE** (CNCF Graduated) | This is *sequencing, not either/or*. Vault/OpenBao accepts **SPIFFE JWT-SVIDs via its JWT/OIDC auth method**, so the two compose. Move to SPIRE once workloads exist outside Kubernetes — bare-metal dev hosts, VMs, CI runners — which in a CDE build they **always** do. |
| **Access plane** | **Teleport** | Unifies three things one control plane: SSO-fronted **short-lived SSH/X.509 certs** (12h default, no `authorized_keys`), **Machine ID** (SPIFFE-compatible SVIDs for workload identity, with attestation for Docker/Podman/Kubernetes/SystemD/CI systems), and **session recording**. Honest trade-off: a single high-value control plane is a new operational dependency and a new blast radius. **HashiCorp Boundary** is the alternative if you are already all-in on the Vault ecosystem. |
| **Certificate authority for SSH** | See §5 | — |

### The agent is its own privileged non-human identity

**Rule, no exceptions:** the coding agent gets **its own SPIFFE ID / Machine ID bot, its own
scoped short-lived credentials, and its own audit trail**. It never inherits the developer's
certificate.

Why it is non-negotiable: a prompt-injected agent running on the developer's certificate has
the developer's *full* blast radius — every repo, every environment, every database the human
can reach — and every action it takes is attributed to the human, destroying the forensic
record at exactly the moment you need it. A scoped agent identity bounds the damage and keeps
"what did the agent do" answerable separately from "what did the human do."

---

## 5. Human SSO and short-lived SSH certificates

**What changed:** `authorized_keys` is no longer defensible in a regulated environment.
**Current recommendation: Keycloak as the IdP for a fintech; SSO must terminate in
short-lived SSH certificates, not keys.**

| Choice | Recommendation | Basis |
|---|---|---|
| **IdP** | **Keycloak** | **FAPI 2 Final certification** (the profile financial-grade APIs are audited against), **CNCF Incubating**, Red Hat-backed. The last one is decisive when a regulator asks who stands behind the IdP. |
| | Authentik — only for a small team | Genuinely good app-proxy / remote-access features, but it is a bet on a single small vendor for an identity control. Not a fintech default. |
| **SSH credential** | **Short-lived certificates, never keys** | `authorized_keys` is unrevocable-in-practice distributed state. Retire it entirely — the sshd config trusts the CA, not a key list. |

SSH CA options, in ascending scope:

| Option | When |
|---|---|
| **step-ca** (Smallstep, OIDC provisioner) | Smallest, cleanest, genuinely open source. Default if you only need certs. |
| **Vault / OpenBao SSH secrets engine** | Natural if you are already running the vault — one less system. |
| **Teleport** | When you want recording + RBAC in the same plane as issuance (§4). |

**Certificate lifetime:**

| Lifetime | Use |
|---|---|
| 4–8 h | High-security segments / production access |
| 12–16 h | Sane default — covers a working day, expires overnight |
| **> 24 h** | ❌ **Never.** Beyond a day you have recreated static keys with extra steps. |

**The CA must refuse raw signing requests.** A certificate is issued only from the
authenticated SSO flow; if an engineer can `POST` a public key to the CA and get it signed,
the SSO binding is decorative.

---

## 6. Shared agent memory (MCP + vector DB) — the highest-severity design area

**What changed:** nothing about the appeal of the idea; everything about what we know it
costs. **Current recommendation: treat a shared agent-memory service as a cross-developer
prompt-injection bus unless every mitigation below is in place.**

The naive design — *"agents publish discovery patterns to a central MCP server so other
agents can query them"* — is, as written, exactly that bus. One agent injected once writes a
poisoned "pattern"; every other developer's agent later retrieves it and **trusts it**,
because a retrieved memory looks like institutional knowledge rather than attacker input.

### Threat model

| # | Threat | Mechanism |
|---|---|---|
| 1 | **Tool-description poisoning** | The MCP tool `description` field is an unsanitized instruction channel. It is injected into the model's context and steers behaviour — and the user never sees it. |
| 2 | **Stored / second-order prompt injection via shared memory** | The exact mechanism the naive design proposes. Write once, execute in every consumer's context, indefinitely. |
| 3 | **Cross-tenant leakage** | In a dev org, "tenant" = project/repo. Leakage means a developer on a low-sensitivity repo retrieving fragments of the payments codebase through the shared memory index. |

### Mandatory mitigations

| Control | Requirement |
|---|---|
| **Isolation at the protocol layer, not the app layer** | Scoped context windows and **per-project namespaces enforced by the server**. An app-layer filter the agent could be talked out of is not isolation. |
| **Retrieved memory is untrusted data, never instructions** | Wrap in a delimited block; strip imperative content; **never let a retrieved memory alter tool permissions.** |
| **Human-reviewed write-side promotion gate** | **Nothing auto-publishes to shared memory.** Promotion from a developer's private namespace to the team namespace is a human-reviewed PR. This is exactly how `CLAUDE.md`-in-git already works — make MCP-memory promotion follow the same rule rather than inventing a weaker one. |
| **Pin and verify MCP servers** | Scan configs with **`mcp-scan`**; pin server versions; **treat a description change as requiring re-approval** (rug-pull defense). |
| **Narrow, short-lived, per-user downstream credentials** | Never tenant-wide MCP access. The memory server's credential to the vector DB is scoped to the caller's namespace. |
| **Log every MCP event** | Tool discovery, authorization request, invocation — with user, client, server, tool, and parameters. |

### Vector store

| Choice | When | Why |
|---|---|---|
| **pgvector** (default) | Already on Postgres; under ~10M vectors; want SQL joins | **Row-level security gives you per-project tenant isolation in the database engine itself** — the fintech-relevant argument, and the one that answers threat 3. Also: agent-memory workloads are **write-heavy** (unlike classic read-mostly RAG), so Postgres WAL durability is the right property. |
| **Qdrant** | > 10M vectors, or sub-15 ms p99 filtered search | Purpose-built ANN with strong payload filtering. Tenant isolation becomes your application's job again — budget for it. |

---

## 7. Agent observability and audit trail

**What changed:** Claude Code ships first-party OpenTelemetry; there is no longer an excuse
for an unobservable agent. **Current recommendation: enable OTel *and* the Compliance API —
they cover different events.**

| Source | Enable | Covers |
|---|---|---|
| **Claude Code OpenTelemetry** | `CLAUDE_CODE_ENABLE_TELEMETRY=1` plus `OTEL_TRACES_EXPORTER` / `OTEL_METRICS_EXPORTER` / `OTEL_LOGS_EXPORTER=otlp` | Spans per model request and per tool execution; token / cost / session metrics; structured log events including `tool_decision`, `tool_result`, and permission events. |
| **Enterprise Compliance API** (separate) | Enterprise plan | Login events, permission changes, org-admin events. |

You need **both** — OTel tells you what the agent did, the Compliance API tells you who was
allowed to do it. Sinks in production use: **SigNoz, Elastic, Grafana, CloudWatch, Dash0**.

**Design rule, state it explicitly in the design doc:** agent action and reasoning traces go
to an **append-only store the agent cannot modify**, retained under the **same policy as
other privileged-access logs**. An audit trail writable by the subject of the audit is not an
audit trail.

---

## 8. Egress control, sandboxing, supply chain

### 8a. Egress — default-deny, allowlist, log

| Tier | Control |
|---|---|
| Baseline (copy this) | Claude Code's own reference devcontainer ships **`init-firewall.sh`** — iptables + ipset, container granted `NET_ADMIN`/`NET_RAW`, run from `postStartCommand`, allowlisting the model endpoints, GitHub, npm, PyPI, crates, and distro mirrors. **Copy the pattern, then narrow it:** pin package traffic to an **internal Artifactory/Nexus mirror**, not public registries. |
| Platform tier | **Coder Agent Firewall** (Premium) — default-deny egress scoped to agents. |
| Agent-protocol tier | **Pipelock** (Apache-2.0) — inspects HTTP/MCP/A2A/WebSocket for exfiltration, SSRF and injection, and emits **mediator-signed action receipts** — audit evidence produced from *outside* the agent, which is the property that makes it evidence. |

**Be honest in the design doc:** any allowlist wide enough to be useful — GitHub plus a model
API — is wide enough to exfiltrate through. **Egress control raises the cost of exfiltration
and creates evidence; it does not make exfiltration impossible.** Claiming otherwise is the
same class of error as §2.

### 8b. Sandboxing ladder

| Tier | Isolation | Verdict |
|---|---|---|
| Shared VM | none | ❌ Not acceptable with agents running |
| **Container** | namespace isolation, **shared kernel** | **The floor.** Container-per-developer is the minimum. |
| **gVisor** | syscall interposition | Good middle tier; some syscall-compat friction. |
| **microVM** (Firecracker / Kata) | own kernel | **The defensible answer for untrusted code execution** — i.e. anywhere the agent runs code it wrote. |

Beneath all of these, Claude Code's own `--sandbox` / sandbox-environments layer is the
**per-session** control. It is additive, not a substitute for a kernel boundary.

### 8c. Supply-chain hardening of the dev-host image

| Control | Requirement |
|---|---|
| Base image | **Pinned by digest**, never a floating tag |
| **Agent CLI version** | **Pinned explicitly.** An auto-updating agent CLI in a regulated environment is an **unreviewed change** shipping straight to every developer. |
| SBOM | Generated and scanned at build time |
| Signing | **Sigstore / cosign**, with **verification enforced at admission** |
| Rebuild | On a schedule, from pinned inputs — never `latest` |
| MCP servers | Versions pinned (see §6) |

---

## 9. Sizing and cost

### Hosted-model lane — the constraint is tokens, not CPU

Dev-host CPU/RAM sizing is the easy, familiar part. The variable that actually blows up is
token spend (§3a). Budget **per-developer token ceilings enforced at a gateway** with
per-user attribution: **Coder AI Gateway**, **LiteLLM**, or an equivalent OpenAI-compatible
proxy. Attribution is what makes a ceiling enforceable; a shared API key with no user
dimension cannot be capped per developer.

### Self-hosted-model lane — 2026 reference point

| Dimension | Reference figure |
|---|---|
| Hardware | Single **H200 SXM5 (141 GB)** |
| Model | FP8 coding MoE, e.g. **Qwen3-Coder-Next** — **~92 GB weights**, leaving **~49 GB for KV cache** |
| Serving | **vLLM V1**, **~750 tok/s** |
| Unit cost | **~$1.79 / M tokens** |
| Scale served | A small team |
| Crossover vs per-seat SaaS | Reported around **8+ developers** |

**Size for concurrent sessions, not for weights.** **KV cache — not model weights — is what
limits concurrency**, and agentic sessions carry far larger contexts than chat, so the
per-session KV footprint is the number that matters. A capacity plan that only checks that
the weights fit will under-provision badly.

---

## 10. Data residency and incident response

### Data residency — one workspace per project

**No "mount everything" dev host.** An agent can only exfiltrate what it can read, so the
workspace's mounted scope *is* the blast radius. One workspace per project; the payments repo
is not mounted in the marketing-site workspace. This is the cheapest control in this document
and the one most often skipped for convenience.

### Incident runbook — write it before you need it

The design doc must answer all three, by name and command, not in principle:

| Question | What the runbook must specify |
|---|---|
| **How do we kill a running agent session?** | The exact control-plane action (workspace stop / gateway key disable / tmux session kill) and who is authorized to take it, out of hours. |
| **How do we revoke its credential?** | Revoke the agent's **own** short-lived credential (§4) — SPIFFE SVID / Machine ID bot / Vault-OpenBao lease — without revoking the developer's. This only works if §4's separation was actually implemented. |
| **How do we reconstruct what it did?** | From the **OTel trace** (§7) — spans per model request and tool execution — plus MCP event logs (§6) and, where deployed, Pipelock's signed action receipts (§8a). |

---

## 11. Rollout roadmap (5 phases)

| Phase | Scope | Exit criterion |
|---|---|---|
| **0 — Governance & threat model** | Decide §2's inference path (a/b/c) and get it in writing; write the agent threat model (§3a, §6); define the identity split between human and agent (§4); set the token/wall-clock budget policy. | A signed statement of where prompt context goes. **No pilot starts before this.** |
| **1 — DIY pilot** | One team, one project. Container-per-developer, tmux + mosh, `init-firewall.sh` narrowed to the internal mirror, agent CLI version pinned. No shared memory yet. | A developer can run a multi-hour unattended agent session and reattach the next morning. |
| **2 — Platform & identity** | Coder (or Che) as the platform. Keycloak SSO → short-lived SSH certs, `authorized_keys` retired. OpenBao + K8s-SA JWT auth. Agent gets its own identity. | No static SSH key on any dev host; every agent action carries a non-human identity. |
| **3 — Shared memory / knowledge** | MCP memory server with per-project namespaces, pgvector + RLS, human-reviewed promotion gate, `mcp-scan` in CI, pinned server versions. | Nothing reaches the team namespace without a reviewed PR. |
| **4 — Observability & incident response** | OTel + Compliance API into an append-only store; gateway-enforced budgets with per-user attribution; sandbox ladder raised to gVisor/microVM where untrusted code runs; incident runbook drilled. | A tabletop exercise reconstructs a full agent session from the trace and revokes only the agent's credential. |

Phases 0 and 4 are the two the original three-phase plan omitted, and they are the two that
decide whether this is an auditable platform or a fast way to lose control of source code.
