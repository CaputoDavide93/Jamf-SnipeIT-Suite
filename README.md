# 🔄 Jamf-SnipeIT Suite

**Enterprise asset-lifecycle automation** — continuous, unattended synchronisation of devices, users, and accessories between **Jamf Pro**, **Snipe-IT**, **Azure AD / Microsoft Entra ID**, and **HiBob**, running serverless on AWS Fargate.

![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg?logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/docker-ready-2496ED.svg?logo=docker&logoColor=white)
![AWS Fargate](https://img.shields.io/badge/AWS-Fargate-FF9900.svg?logo=amazonwebservices&logoColor=white)
![Tests](https://img.shields.io/badge/tests-39%20passing-brightgreen.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platform](https://img.shields.io/badge/platform-linux%2Famd64-lightgrey.svg)

---

## 📋 Table of Contents

- [Overview](#-overview)
- [Features](#-features)
- [Architecture](#-architecture)
- [Modules](#-modules)
  - [Lifecycle](#lifecycle)
  - [Sync](#sync)
  - [Maintenance](#maintenance)
- [The User Lifecycle Model](#-the-user-lifecycle-model)
- [Schedule](#-schedule)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Usage](#-usage)
- [Production Deployment (AWS)](#-production-deployment-aws)
- [Safety Model](#-safety-model)
- [Testing](#-testing)
- [Documentation](#-documentation)

---

## 🎯 Overview

Keeping an asset register truthful across four systems of record is a losing manual battle: people join, leave, change surname, convert from contract to permanent, and sometimes come back a day after being offboarded. Each of those events touches Jamf (device management), Snipe-IT (asset register), Azure AD (identity), and HiBob (HR truth) — and any drift between them means lost laptops, ghost users, and wrong audit answers.

This suite closes the loop automatically:

| System | Role |
|--------|------|
| 🖥️ **Jamf Pro** | Device management — serials, local accounts, hardware models |
| 📦 **Snipe-IT** | Asset register — ownership, checkout state, accessories |
| 🔑 **Azure AD / Entra ID** | Identity — account status, group membership, job titles |
| 👥 **HiBob** | **HR source of truth** — employment status, equipment entitlements *(read-only, never written to)* |

## ✨ Features

- 🤝 **Zero-touch user provisioning** — new starters (including contractors) appear in Snipe-IT before their first Monday
- 🔁 **Re-hire detection** — employees who leave and return (even next-day) are automatically un-ghosted and get their machine assignment restored
- 🧠 **Multi-strategy user matching** — exact / email / normalised-username / fuzzy scoring, with an AI resolver for genuinely ambiguous cases and manual overrides as the final word
- 🚪 **Leaver processing** — departed users' assets flip to *Pending*, accounts are tagged `[Disabled]`, nothing is deleted
- 🧾 **Accessory sync from HR** — HiBob equipment entitlements become Snipe-IT accessory checkouts
- 🛠️ **Self-healing** — a correction module continuously repairs wrong assignments; a health-check scans for stuck states twice a week
- 🔒 **Concurrency-safe** — every scheduled job serialises on a distributed mutex (SSM-backed); overlapping runs are skipped, never interleaved
- 🧪 **Dry-run everywhere** — every mutating module supports `--dry-run`; the newest modules default to it via a config safety latch
- 📣 **Slack reporting** — run summaries, error alerts, and human-decision queues (ambiguous re-hires) delivered to a channel

## 🏗 Architecture

```text
                    ┌──────────────────────────────────────────────┐
                    │                AWS (eu-west-1)               │
                    │                                              │
   EventBridge ──▶  │  ECS Fargate task (linux/amd64)              │
   4 cron rules     │  ┌────────────────────────────────────────┐  │
                    │  │            docker_scheduler            │  │
                    │  │  RunMutex (SSM) ─ serialised modules   │  │
                    │  └──────┬─────────────────┬───────────────┘  │
                    │         │                 │                  │
                    │   SSM Parameter Store   ECR image            │
                    │   (secrets)             (:latest)            │
                    └─────────┼─────────────────┼──────────────────┘
                              ▼                 ▼
      ┌──────────┐   ┌──────────────┐   ┌──────────────┐   ┌───────────┐
      │ Jamf Pro │◀─▶│   Snipe-IT   │◀─▶│  Azure AD /  │   │   HiBob   │
      │  (MDM)   │   │ (asset reg.) │   │   Entra ID   │   │ (HR, R/O) │
      └──────────┘   └──────────────┘   └──────────────┘   └───────────┘
```

**Layered codebase:**

```text
src/
├── clients/      # Thin, retrying API clients (jamf, snipeit, azure, hibob, slack)
├── core/         # Config schema, client factory, run context, sync state
├── matching/     # UserMatcher scoring engine + AI resolver
├── infra/        # RunMutex, health server, shared helpers
├── modules/
│   ├── lifecycle/     # azure_starters, user_enrichment, rehire_detection, leavers
│   ├── sync/          # user_match, snipe_to_jamf, model_sync, correction, peripherals_sync
│   └── maintenance/   # cleanup, reconciliation, username_standardize, ai_audit, health_check
├── main.py            # CLI entry point (interactive menu + subcommands)
└── docker_scheduler.py# Container entry point (APScheduler + health endpoint)
```

## 🧩 Modules

### Lifecycle

| Module | Icon | What it does |
|--------|------|--------------|
| `azure-starters` | 🌱 | Creates Snipe-IT users for every member of the starters AAD group. Skips former employees (passed leave date) and aborts if the Snipe-IT fetch looks broken. Unique random password per user. |
| `user-enrichment` | 🏷️ | Enriches Snipe-IT users with AAD job titles and departments; persists an append-only **Contractor** marker so contractors are identifiable in the register. |
| `rehire-detection` | ♻️ | Reverses the leaver flow for returning employees — see [lifecycle model](#-the-user-lifecycle-model). Runs **before** every other lifecycle/sync module. |
| `leavers` | 🚪 | Tags departed users `[Disabled]`, flips their assets to *Pending*. Uses live AAD group membership + leave dates; per-asset re-verification before any change. |

### Sync

| Module | Icon | What it does |
|--------|------|--------------|
| `user-match` | 🧠 | Matches Jamf machines' local accounts to Snipe-IT users (override → exact → email → normalised → fuzzy → AI) and checks assets out to the right person. |
| `snipe-to-jamf` | 🔗 | Pushes the Snipe-IT asset ID back into a Jamf extension attribute for cross-linking. |
| `model-sync` | 💻 | Ensures every Jamf hardware model exists in Snipe-IT before assets need it. |
| `correction` | 🛠️ | Self-healing: detects and repairs wrong asset assignments using the machine's local account as ground truth. |
| `peripherals-sync` | 🎧 | Reads HiBob equipment entitlements (read-only) and mirrors them as Snipe-IT accessory checkouts. |

### Maintenance

| Module | Icon | What it does |
|--------|------|--------------|
| `cleanup` | 🧹 | De-duplicates user records and removes junk accounts. |
| `reconciliation` | ⚖️ | Full inventory diff across systems; reports drift. |
| `username-standardize` | ✂️ | Normalises usernames to `first.last` (email prefix), honouring configured exceptions. |
| `ai-audit` | 🔍 | Weekly AI-assisted cross-platform audit of suspicious assignments. |
| `health-check` | 🩺 | Scans for stuck states (assets pending too long, users disabled with assets, orphan checkouts) and alerts. |
| `wakeup` | ⏰ | Manual-only: sends wake commands to a Jamf smart group before big syncs. |

## 🔄 The User Lifecycle Model

The suite models the full employee journey, including the awkward paths most tooling ignores:

```text
                   ┌────────────┐
      new hire ──▶ │   ACTIVE   │ ◀────────────────────┐
                   └─────┬──────┘                      │
                         │ leaves (AAD leavers group)  │ re-hired / contract→perm
                         ▼                             │
                   ┌────────────┐   4-signal check   ┌─┴──────────────┐
                   │ [Disabled] │ ─────────────────▶ │ REHIRE-RESTORE │
                   │ assets ⏸   │                    │ name un-tagged │
                   └─────┬──────┘                    │ assets ▶ live  │
                         │ stays gone                └────────────────┘
                         ▼
                   (kept for audit — never deleted)
```

A `[Disabled]` user is **automatically restored** only when **four independent signals agree**:

1. ✅ Azure AD account is **enabled**
2. ✅ **Not** a member of the leavers or disabled AAD groups
3. ✅ No passed `employeeLeaveDateTime`
4. ✅ **HiBob lists them as an active employee** (HR source of truth, read-only)

Anything less (e.g. AAD enabled but still in the leavers group) is classified **ambiguous** and reported to Slack for a human decision — the suite never guesses on people.

## ⏰ Schedule

All jobs run in `Europe/London`, serialised under the run mutex. Local scheduler crons:

| Day | Time | Job | Purpose |
|-----|------|-----|---------|
| Mon | 17:00 | 🌱 `azure_starters` | Provision the week's new starters |
| Mon | 18:00 | 🏷️ `user_enrichment` | Titles, departments, contractor markers |
| Mon | 18:10 | 🎧 `peripherals_sync` | HiBob equipment → accessories |
| Mon+Thu | 19:00 | 🩺 `health_check` | Drift / stuck-state scan |
| Tue | 17:30 | ♻️ `rehire_detection` | Un-ghost returners **before** the sync chain |
| Tue | 18:00 | 🛠️ `correction` | Fix wrong assignments |
| Tue | 18:30 | 🧠 `user_match` | Match & check out machines |
| Tue | 19:00 | 🔗 `snipe_to_jamf` | Push asset IDs to Jamf |
| Tue | 19:30 | 🚪 `leavers` | Process departures **last** |
| Sun | 22:00–23:00 | 💻🧹✂️🔍⚖️ housekeeping | model_sync, cleanup, username_standardize, ai_audit, reconciliation |

> 🔐 **Why 30-minute slots?** A slow module can never overlap the next one — and even if it did, the per-job mutex skips (never interleaves) the collision.

## 📥 Installation

### Prerequisites

- Python **3.12+** (or Docker)
- API access to Jamf Pro, Snipe-IT, Azure AD (Graph), HiBob (read-only service user), Slack (bot token)

### Local

```bash
git clone <repo-url> && cd Jamf-SnipeIT-Suite
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp config/config.yaml.example config/config.yaml   # fill in credentials
python src/main.py                                  # interactive menu
```

### Docker

```bash
cp config/config.yaml.example config/config.yaml
docker compose up          # scheduler mode with health endpoint
```

## ⚙️ Configuration

Configuration is layered — **YAML for local runs, environment variables for containers** (env always wins):

| Source | Used by | Notes |
|--------|---------|-------|
| `config/config.yaml` | Local CLI & docker-compose | Gitignored — never committed |
| Task-definition env vars | AWS Fargate | Non-secret settings (group IDs, matching rules) |
| SSM Parameter Store | AWS Fargate | Secrets (`/jamf-snipeit-suite-prod/*`), read at container start |

Key settings:

| Setting | Purpose |
|---------|---------|
| `azure.starters_group_id` | AAD group of active staff to provision (active-users-only group) |
| `azure.leavers_group_id` / `disabled_group_id` | Leaver signals for lifecycle modules |
| `modules.rehire_detection.dry_run` | 🔒 Safety latch — config can only force dry-run **on**, never off |
| `modules.rehire_detection.hibob_confirmation` | Require HiBob (read-only) to confirm re-hires |
| `modules.user_enrichment.mark_contractors` | Persist AAD `Contractor` department as a Snipe-IT marker |
| `matching.skip_usernames` | Shared/test local accounts to ignore |
| `config/user_overrides.json` | 🚫 Local-only (gitignored, PII) — manual match overrides, baked into the image at build time |

## 🚀 Usage

```bash
# Interactive menu (all modules, guided)
python src/main.py

# Individual modules
python src/main.py rehire-detection --dry-run
python src/main.py leavers --dry-run
python src/main.py user-match
python src/main.py health-check

# Everything in dependency order
python src/main.py all

# Container: scheduler with NOW menu + /health endpoint
RUN_MODE=scheduler python src/docker_scheduler.py --config config/config.yaml
```

Every mutating command accepts `--dry-run` / `-n` and prints exactly what it *would* change.

## ☁️ Production Deployment (AWS)

Production runs as **four EventBridge rules → one Fargate task definition** (`RUN_MODE` decides the module set). The task reads secrets from SSM at start; the image ships from ECR.

```bash
# 1. ECR login (12h token)
aws ecr get-login-password --region eu-west-1 \
  | docker login --username AWS --password-stdin <AWS_ACCOUNT_ID>.dkr.ecr.eu-west-1.amazonaws.com

# 2. Build — MUST be linux/amd64 (ARM64 silently fails on Fargate)
docker build --platform linux/amd64 \
  -t <AWS_ACCOUNT_ID>.dkr.ecr.eu-west-1.amazonaws.com/jamf-snipeit-suite-prod:latest .

# 3. Push — next EventBridge trigger picks it up automatically
docker push <AWS_ACCOUNT_ID>.dkr.ecr.eu-west-1.amazonaws.com/jamf-snipeit-suite-prod:latest
```

> ⚠️ **Config changes ≠ code changes.** Fargate never reads `config.yaml` — non-secret settings live in the **task-definition environment**. To change one: register a new task-def revision, then repoint all four EventBridge rule targets to it. See `OPERATIONS.md`.

## 🛡 Safety Model

| Guard | Protects against |
|-------|------------------|
| 🔒 Distributed `RunMutex` (SSM) on **every** job | Overlapping runs reverting each other's work |
| 🧪 Dry-run safety latch on new modules | A new module going live before its output is reviewed |
| 🤝 4-signal re-hire confirmation incl. HiBob | Un-ghosting someone who is actually leaving |
| 🙋 Ambiguous-case human queue (Slack) | Automated guesses on people's employment state |
| 🧯 Fetch-integrity aborts | An empty API response triggering mass duplicate creation |
| ⏸️ *Pending*, never delete | Any destructive action on leaver data |
| 📴 HiBob strictly read-only | Any write ever reaching the HR source of truth |

## 🧪 Testing

```bash
pytest tests/ -v        # 39 tests: matcher scoring, lifecycle classification,
                        # rehire signals, starters guards, helpers
```

## 📚 Documentation

- 📖 **Full architecture & operations** → [Confluence: Jamf-SnipeIT Suite](https://xsolutions.atlassian.net/wiki/pages/viewpage.action?pageId=4493508620)
- 🔧 **Runbook** (deploys, schedules, secret rotation, mutex) → [`OPERATIONS.md`](OPERATIONS.md)
- 🤝 **Contributing** → [`CONTRIBUTING.md`](CONTRIBUTING.md)

---

<p align="center">Built and maintained by <strong>Davide Caputo</strong> · CreateFuture TechOps</p>
