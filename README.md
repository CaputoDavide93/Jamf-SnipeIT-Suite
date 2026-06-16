<p align="center">
  <img src="https://img.shields.io/badge/Jamf-Pro-purple?style=for-the-badge&logo=jamf" alt="Jamf Pro"/>
  <img src="https://img.shields.io/badge/Snipe--IT-green?style=for-the-badge" alt="Snipe-IT"/>
  <img src="https://img.shields.io/badge/Azure%20AD-blue?style=for-the-badge&logo=microsoftazure" alt="Azure AD"/>
  <img src="https://img.shields.io/badge/HiBob-orange?style=for-the-badge" alt="HiBob"/>
  <img src="https://img.shields.io/badge/AWS-Fargate-FF9900?style=for-the-badge&logo=amazonaws" alt="AWS Fargate"/>
  <img src="https://img.shields.io/badge/AI-powered-7B42BC?style=for-the-badge" alt="AI"/>
</p>

# Jamf-SnipeIT Suite

> **Unified Asset Management & Synchronisation Platform with AI-powered matching**

Automated weekly synchronisation of devices, users, and accessories between **Jamf Pro**, **Snipe-IT**, **Azure AD / Microsoft Entra ID**, and **HiBob**. Runs as ECS Fargate tasks on AWS, scheduled by four EventBridge rules covering provisioning, sync, health checks and housekeeping. Zero-touch user provisioning.

---

## Table of Contents

- [What it does](#what-it-does)
- [Data flow & sources of truth](#data-flow--sources-of-truth)
- [Modules](#modules)
- [Matching engine](#matching-engine)
- [AI features](#ai-features)
- [Deployment](#deployment)
- [Configuration](#configuration)
- [Operations](#operations)
- [Documentation](#documentation)

---

## What it does

Across the week (Mon/Tue evenings + Sun late, all UTC), the suite:

1. **Provisions new starters** from Azure AD into Snipe-IT
2. **Enriches** existing Snipe-IT profiles with job titles, departments, phone numbers from Azure
3. **Matches** every Jamf computer to its Snipe-IT user based on the actual local account on the machine
4. **Auto-creates** Snipe-IT users for anyone who has a Mac but isn't in Snipe-IT yet (pulled from Azure AD)
5. **Self-heals** wrong assignments by cross-referencing all 3 platforms
6. **Marks pending** assets for leavers (Azure AD) while keeping the assignment for tracking
7. **Syncs HiBob equipment** into Snipe-IT accessories with name normalisation
8. **Posts to Slack** any case a human needs to review

No manual intervention needed for the 95% happy path. Ambiguous cases go to a Slack channel for review.

---

## Data flow & sources of truth

```
 Azure AD                          HiBob
 (leavers, starters,             (equipment,
  job titles, depts)              accessories)
       |                              |
       v                              v
  Jamf Pro  ---------> Snipe-IT <---- HiBob Sync
  (local user           (asset inventory,
   accounts)             user records,
                         accessories)
      |                     |
      |   confirmed          |
      +--- match ----------->+ (checkout asset to user)
      |                      |
      +<-- EA only ----------+ (Snipe-IT asset ID written to Jamf)
      |                      |
      +<-- verified ---------+ (confirmed name/email written back)
           data only
```

| System | Source of truth for |
|--------|---------------------|
| **Jamf Local Accounts** | Who actually uses each machine (username + full name from the Mac) |
| **Azure AD** | Employee lifecycle — disabled/leaver status, job titles, departments |
| **HiBob** | Equipment/accessories assigned to employees |
| **Snipe-IT** | Asset inventory (consumer — receives data, never feeds back to identity fields) |

**The golden rule:** user identity information flows *into* Snipe-IT, never out of Snipe-IT into Jamf or Azure unless verified by a confirmed cross-platform match.

---

## Modules

Four EventBridge rules cover all 14 modules. Each rule fires the same Fargate
task with a `containerOverrides.command` so the entrypoint dispatches to the
right CLI. All times UTC.

### Mon 17:00 UTC — `starters` group
| Module | Description |
|--------|-------------|
| **Azure Starters** | Creates Snipe-IT users for new hires from Azure AD starters group. |
| **User Enrichment** | Pushes Azure AD job titles, departments, phone numbers to Snipe-IT. |
| **Peripherals Sync** | Syncs HiBob equipment to Snipe-IT accessories with name mapping. |

### Tue 17:00 UTC — `sync` (full run-once)
| Module | Description |
|--------|-------------|
| **Model Sync** | Ensures hardware models exist in Snipe-IT, creates missing ones. |
| **Correction** | Validates existing Snipe-IT assignments against Jamf local accounts. Auto-corrects mismatches on exact matches; flags fuzzy/AI mismatches for Slack review. |
| **User Match** | Main provisioning. Matches Jamf computers to Snipe-IT users, creates assets (auto-tagged `CF-####`), checks out. Auto-creates Snipe-IT users from Azure AD if needed. |
| **Snipe-to-Jamf** | Writes Snipe-IT asset ID EA back to Jamf (identity fields are never touched). |
| **Leavers** | Sets Pending status on assets for disabled/leaver Azure AD users. Keeps the assignment for tracking. |

### Mon + Thu 19:00 UTC — `health` group
| Module | Description |
|--------|-------------|
| **Health Check** | Scans for stuck Pending, checked-out-to-disabled, stale pending (>30d), orphan users, invalid states. Posts Slack only if findings. |

### Sun 21:00 UTC — `housekeeping` group
| Module | Description |
|--------|-------------|
| **Cleanup** | Merges duplicate users, removes junk accounts. |
| **Username Standardize** | Strips `@domain` from Snipe-IT usernames for consistency. |
| **AI Audit** | AI-powered cross-platform audit — finds security risks, data inconsistencies, anomalies. Posts structured Slack report. |
| **Reconciliation** | Inventory diff Jamf ↔ Snipe-IT. Identifies devices in one system but not the other. |

### On-demand
| Module | Description |
|--------|-------------|
| **WakeUp** | Sends MDM redeploy commands to unresponsive Jamf devices. CLI / interactive only. |
| **`run-group --modules a,b,c`** | Generic CLI to run any sequence of modules under the shared run mutex. Used by all group EventBridge rules. |

---

## Matching engine

The matching engine identifies which Snipe-IT user owns each Jamf computer. Priority order:

### Priority 0 — Manual overrides
`config/user_overrides.json` holds permanent mappings for edge cases (surname changes, custom local account names, test accounts). Fastest path, zero API calls.

### Priority 1 — Full name
Exact match against Snipe-IT user names. Disambiguates same-name users via email.

### Priority 2 — Email
Uses the original Jamf location email directly (`jane.doe@company.com`) before falling back to reconstructed email.

### Priority 3 — Email prefix
Normalised prefix match (dots/dashes/underscores ignored).

### Priority 4 — Username
Exact username match, case-insensitive.

### Priority 4b — Normalised username
Catches surname changes: Jamf local account `janewinters` still matches Snipe-IT user with username `jane.winters@company.com` even after the Snipe-IT name changed to "Jane Sommers".

### Priority 5 — Fuzzy
LCS + bigram Dice coefficient with surname bonus. Minimum score 14, required margin 20% between top two candidates.

### Priority 6 — AI cross-platform resolver
When fuzzy rejects, the LLM reasons about the local account + all candidates + Azure AD data. Resolves:
- Nicknames: Tom → Thomas, Jonny → Jonathan, Rich → Richard
- Typos: "James Fird" → James Ford
- Surname changes via Azure aliases: `janewinters` → Jane Sommers
- Can decide to keep current assignment if AI concludes the Jamf local account is stale (old surname)

### Priority 7 — Auto-create from Azure
If nothing matches in Snipe-IT but the Jamf local user exists in Azure AD as active, the system creates the Snipe-IT user automatically with their Azure data and assigns the machine.

### Safety rules
- **Jamf local account is source of truth** — if a local account matches a Snipe-IT user (disabled or not), the machine gets reassigned to that user. A machine whose local account belongs to `[Disabled] X` is still X's machine (notice period, returning, etc.)
- **Only auto-correct on exact matches** — fuzzy/AI mismatches go to Slack for human review instead
- **Pending assets untouched** by User Match or Correction
- **Checkout failure rollback** — reverts to original user if new checkout fails
- **Sandbox / test / demo accounts deprioritised** — local accounts whose username or realname contains `sandbox`, `demo`, `service`, `test`, `temp`, or `temporary` get -20 in the primary-picker score, so real users always win when both are present
- **Common non-person accounts skipped** — `admin`, `shared`, `guest`, `createfuture`, `xdesign`, `payables`, `iossandboxaccount`, etc. via `MATCHING_SKIP_USERNAMES` env var / `matching.skip_usernames` yaml key

### Asset identity
- **Asset name** = serial number (e.g. `WWLWWKCWGC`). Auto-renamed on every full-run via Correction / User Match flow.
- **Asset tag** = `CF-####` progressive number (e.g. `CF-0813`). On asset creation, `SnipeITClient.next_cf_tag()` scans the max existing `CF-<n>` and returns `CF-<n+1>`. The mutex in `run_all_modules_startup` prevents two processes racing on the next number.

---

## AI features

### AI Resolver (matching)
- Model: Claude Haiku (fast, cheap)
- Called only when fuzzy matching is ambiguous
- Cross-references Snipe-IT + Azure AD data
- Persistent cache in S3 (30-day TTL) — same query = 0 API calls
- Rate-limit circuit breaker — if the API returns usage-limit errors, AI is disabled for the rest of the run

### AI Audit (weekly)
- Model: Claude Sonnet (deep reasoning)
- Runs every Sunday at 04:00 UTC
- Collects data from Jamf + Snipe-IT + Azure AD
- Identifies: security risks, compliance gaps, users with excessive devices, untracked assets, offboarding process gaps
- Posts structured Slack report with severity ratings and specific recommendations

### Cost
~$0.10–$0.50/month for typical operation (most calls hit the cache).

---

## Deployment

### AWS Fargate (production)

```
terraform/
  modules/jamf-snipeit-suite/   # Reusable module
    ecr.tf           ECR repository + lifecycle policy
    ecs.tf           Cluster, task definition, security group
    iam.tf           Execution role, task role, EventBridge role
    secrets.tf       12 SSM SecureString parameters
    eventbridge.tf   Four scheduled rules (starters / sync / health / housekeeping)
    cloudwatch.tf    Log group (90-day retention)
    s3_cache.tf      AI resolver cache bucket
  environments/prod/
    main.tf          Provider (account-locked), module call
```

**Security hardening:**
- Account lock: `allowed_account_ids = ["<AWS_ACCOUNT_ID>"]`
- Region lock: `eu-west-1` only
- All 12 credentials in SSM SecureString (encrypted at rest, not visible in task definition)
- Egress-only security group (no inbound)
- IAM least privilege (SSM parameters scoped to this project only)
- Sanitised logging (no credentials in CloudWatch)
- Random 24-char passwords for auto-created users

**Cost:** ~$2–5/month (Fargate pay-per-use, ~30min/day + ECR storage + CloudWatch + AI API)

### Deploy

```bash
cd terraform/environments/prod
cp terraform.tfvars.example terraform.tfvars   # fill in secrets
terraform init
terraform plan
terraform apply

# Push Docker image
aws ecr get-login-password --region eu-west-1 | \
  docker login --username AWS --password-stdin <ECR_URL>
docker build --platform linux/amd64 -t <ECR_URL>:latest .
docker push <ECR_URL>:latest
```

Four EventBridge rules trigger the task on the schedule shown in the Modules section above. Each rule overrides the container `command` so the entrypoint dispatches to the right CLI subcommand.

### Local / Docker Compose (development)

```bash
# Dry run all modules
docker compose --profile run-once run --rm -e DRY_RUN=true run-once

# Run scheduler mode (daily cron)
docker compose up -d

# Single module
docker compose run --rm cli user-match --dry-run
```

---

## Configuration

### Two config modes

**1. YAML file** (`config/config.yaml`) — used for local development
```yaml
jamf:
  base_url: "https://your-instance.jamfcloud.com"
  username: "api-user"
  password: "api-password"

snipeit:
  base_url: "https://your-snipeit.example.com"
  api_token: "your-token"

azure:
  tenant_id: "..."
  client_id: "..."
  client_secret: "..."
  leavers_group_id: "..."
  disabled_group_id: "..."
  starters_group_id: "..."

matching:
  email_domain: "company.com"
  skip_usernames:
    - "admin"
    - "shared"
```

**2. Environment variables** — used for Fargate (no config file mounted)

Every YAML key has an env var equivalent: `JAMF_BASE_URL`, `SNIPEIT_API_TOKEN`, `AZURE_CLIENT_SECRET`, etc. The app automatically uses env vars when `config.yaml` is missing.

Additional toggles:
- `LOG_JSON=true` — emit JSON logs
- `HEALTH_AUTH_TOKEN=...` — require bearer token on `/health` endpoints (also binds health server to `127.0.0.1` by default)
- `MUTEX_LOCK_REFRESH_SEC=300` — refresh interval for the SSM mutex TTL
- `MUTEX_LOCK_PARAM=/jamf-snipeit-suite-prod/run-lock` — override lock name per environment

### User overrides (`config/user_overrides.json`)

For edge cases that matching can't solve automatically:

```json
{
  "overrides": {
    "janewinters": {
      "snipe_user_id": 687,
      "snipe_user_name": "Jane Sommers",
      "reason": "Surname change after marriage"
    }
  }
}
```

Baked into the Docker image. Takes precedence over all matching logic.

---

## Operations

- **[OPERATIONS.md](OPERATIONS.md)** — day-to-day tasks (adding overrides, checking logs, investigating alerts)
- **[SECURITY.md](SECURITY.md)** — security policy, credential rotation, reporting vulnerabilities
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — contribution guidelines

---

## Documentation

### Project structure

```
src/
  clients/          API client wrappers (Jamf, Snipe-IT, Azure, HiBob, Slack)
  core/             Config loader, client factory, state management
  infra/            Audit CSV, health server, progress tracker
  matching/         UserMatcher + AI resolver + user overrides
  modules/
    lifecycle/      Azure Starters, User Enrichment, Leavers
    sync/           User Match, Correction, Snipe-to-Jamf, Model Sync, Peripherals
    maintenance/    Cleanup, Reconciliation, Username Standardize, WakeUp, AI Audit
  main.py           CLI entry point
  docker_scheduler.py   Docker mode with scheduler + on-demand menu

terraform/
  modules/jamf-snipeit-suite/   Reusable ECS Fargate module
  environments/prod/            Production deployment

config/
  config.yaml.example           Template (safe to commit)
  equipment_mapping.json        HiBob name → Snipe-IT accessory mapping
  user_overrides.json           Manual matching overrides
```

### License

MIT — see [LICENSE](LICENSE)

### Author

**Davide Caputo** — TechOps
