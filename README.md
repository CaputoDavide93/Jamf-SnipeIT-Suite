<p align="center">
  <img src="https://img.shields.io/badge/Jamf-Pro-purple?style=for-the-badge&logo=jamf" alt="Jamf Pro"/>
  <img src="https://img.shields.io/badge/Snipe--IT-green?style=for-the-badge" alt="Snipe-IT"/>
  <img src="https://img.shields.io/badge/Azure%20AD-blue?style=for-the-badge&logo=microsoftazure" alt="Azure AD"/>
  <img src="https://img.shields.io/badge/HiBob-orange?style=for-the-badge" alt="HiBob"/>
  <img src="https://img.shields.io/badge/AWS-Fargate-FF9900?style=for-the-badge&logo=amazonaws" alt="AWS Fargate"/>
</p>

# Jamf-SnipeIT Suite

> **Unified Asset Management & Synchronization Platform**

Automated synchronization of devices, users, and accessories between **Jamf Pro**, **Snipe-IT**, **Azure AD/Microsoft Entra ID**, and **HiBob**. Runs as a scheduled ECS Fargate task on AWS.

<p align="center">
  <img src="https://img.shields.io/badge/python-3.14-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.14"/>
  <img src="https://img.shields.io/badge/docker-ready-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker"/>
  <img src="https://img.shields.io/badge/terraform-IaC-7B42BC?style=flat-square&logo=terraform" alt="Terraform"/>
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT License"/>
</p>

---

## Data Flow & Sources of Truth

```
                  Azure AD                          HiBob
                (disabled/leavers,              (equipment/
                 job titles, depts)              accessories)
                      |                              |
                      v                              v
  Jamf Pro  -----> Snipe-IT <----- HiBob Sync
  (local user      (asset inventory,
   accounts)        user records)
      |                |
      |   confirmed    |
      +--- match ----->+ (checkout asset to matched user)
      |                |
      +<-- EA only ----+ (Snipe-IT asset ID written to Jamf)
      |                |
      +<-- verified ---+ (confirmed name/email written to Jamf location)
          data only
```

| System | Source of truth for |
|--------|-------------------|
| **Jamf Local Accounts** | Who uses each machine (username, full name) |
| **Azure AD** | Disabled/leaver status, job titles, departments |
| **HiBob** | Equipment/accessories assigned to employees |
| **Snipe-IT** | Asset inventory (consumer - receives data from all sources) |

---

## Modules

| Module | Schedule | Description |
|--------|----------|-------------|
| **Azure Starters** | Mon 6am | Create Snipe-IT users from Azure AD starters group |
| **User Enrichment** | Mon 6:30am | Push Azure AD fields (job title, dept) to Snipe-IT |
| **Model Sync** | Sun 1am | Ensure hardware models exist in Snipe-IT |
| **Correction** | Daily 6:15am | Validate assignments, fix mismatches, rollback on failure |
| **User Match** | Daily 6:30am | Match Jamf computers to Snipe-IT users, checkout assets |
| **Snipe-to-Jamf** | Daily 7am | Write asset ID EA to Jamf (identity fields untouched) |
| **Leavers** | Daily 7:30am | Set Pending status for disabled users (keep assigned) |
| **Peripherals Sync** | Mon 8am | Sync HiBob equipment to Snipe-IT accessories |
| **Cleanup** | Sun 3am | Merge duplicate users, remove junk accounts |
| **Reconciliation** | On-demand | Cross-platform inventory diff with CSV export |
| **Username Standardize** | On-demand | Strip @domain from Snipe-IT usernames |
| **WakeUp** | On-demand | Send MDM redeploy commands |

---

## User Matching

The matching engine identifies which Snipe-IT user owns each Jamf computer by inspecting the **local user accounts** on the machine (not the Jamf location fields, which may be stale).

### Priority order

1. **Full name** - exact match against Snipe-IT user names
2. **Email** - original Jamf location email (if available) for direct lookup
3. **Email prefix** - normalised username as email prefix (dot/dash insensitive)
4. **Username** - exact username match
5. **Fuzzy** - LCS + bigram Dice coefficient + surname bonus (min score 14, 20% margin)
6. **AI Resolver** - when fuzzy is ambiguous, an LLM reasons about nicknames, typos, and disabled users

### AI Resolver

When the fuzzy matcher can't decide (margin < 20% between top candidates), an LLM evaluates all context:

- Resolves nicknames: Tom -> Thomas, Jonny -> Jonathan, Rich -> Richard
- Detects typos: "James Fird" -> James Ford
- Prefers active users over disabled ones
- Returns `null` when genuinely uncertain (sent to Slack for manual review)

### Safety rules

- **Never reassign from active user to disabled user** (e.g. Kerensa Martin keeps the machine even though Chris Martin's old account is still on it)
- **Checkout failure rollback** - if checkout fails after check-in, re-assigns to original user
- **Pending assets untouched** - neither User Match nor Correction modify Pending assets

---

## Leavers Handling

When a user appears in the Azure AD leavers/disabled group:

1. Asset status set to **Pending** (protects from re-provisioning)
2. Asset **stays assigned** to the leaver (for tracking who had it)
3. User name prefixed with `[Disabled]` (disabled group only)
4. Machine awaits manual collection and reassignment

---

## AWS Infrastructure (Terraform)

Deployed to **ECS Fargate** with all resources managed by Terraform.

```
terraform/
  modules/jamf-snipeit-suite/   # Reusable module
    ecr.tf           ECR repository + lifecycle policy
    ecs.tf           Cluster, task definition, security group
    iam.tf           Execution role, task role, EventBridge role
    secrets.tf       12 SSM SecureString parameters
    eventbridge.tf   Daily 6am UTC scheduled trigger
    cloudwatch.tf    Log group (90-day retention) + optional alarm
  environments/prod/            # Production root
    main.tf          Provider (account-locked), module call
```

### Security

| Measure | Detail |
|---------|--------|
| Account lock | `allowed_account_ids = ["<AWS_ACCOUNT_ID>"]` |
| Region lock | Validation: `eu-west-1` only |
| Secrets | All 12 credentials in SSM SecureString (encrypted at rest) |
| Network | Egress-only security group, no inbound |
| Logging | Debug output sanitised, no credentials in logs |
| Passwords | Random 24-char per new user (no static default) |
| Tags | All resources: `Owner: Davide Caputo - TechOps` |

### Estimated cost

~$2-5/month (Fargate pay-per-use, ~30min/day + ECR storage + CloudWatch)

### Deploy

```bash
cd terraform/environments/prod
cp terraform.tfvars.example terraform.tfvars  # fill in secrets
terraform init
terraform plan
terraform apply

# Push Docker image
aws ecr get-login-password --region eu-west-1 | docker login --username AWS --password-stdin <ECR_URL>
docker build --platform linux/amd64 -t <ECR_URL>:latest .
docker push <ECR_URL>:latest
```

---

## Docker (Local)

### Scheduler mode (default)

```bash
docker compose up -d          # Start with daily schedule
docker attach jamf-snipeit    # Type NOW for on-demand menu
docker compose logs -f        # View logs
```

### Run once

```bash
docker compose --profile run-once run --rm run-once
```

### CLI mode

```bash
docker compose run --rm cli leavers --dry-run
docker compose run --rm cli user-match --dry-run
```

### Dry run (no changes)

```bash
docker compose --profile run-once run --rm -e DRY_RUN=true run-once
```

---

## Configuration

### Config file (local/Docker Compose)

```yaml
# config/config.yaml (never commit - gitignored)
jamf:
  base_url: "https://your-instance.jamfcloud.com"
  username: "api-user"
  password: "api-password"

snipeit:
  base_url: "https://your-snipeit.example.com"
  api_token: "your-token"

azure:
  tenant_id: "your-tenant-id"
  client_id: "your-client-id"
  client_secret: "your-secret"
  leavers_group_id: "group-guid"
  disabled_group_id: "group-guid"
  starters_group_id: "group-guid"

matching:
  email_domain: "company.com"
  skip_usernames:
    - "admin"
    - "shared"
    - "guest"
```

### Environment variables (Fargate/serverless)

When no config.yaml is present, all settings are read from environment variables:

| Variable | Description |
|----------|-------------|
| `JAMF_BASE_URL` | Jamf Pro URL |
| `JAMF_USERNAME` / `JAMF_PASSWORD` | Jamf credentials |
| `SNIPEIT_BASE_URL` / `SNIPEIT_API_TOKEN` | Snipe-IT credentials |
| `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` | Azure AD |
| `AZURE_LEAVERS_GROUP_ID` / `AZURE_DISABLED_GROUP_ID` / `AZURE_STARTERS_GROUP_ID` | Azure groups |
| `SLACK_BOT_TOKEN` / `SLACK_CHANNEL_ID` | Slack notifications |
| `HIBOB_SERVICE_USER_ID` / `HIBOB_SERVICE_USER_TOKEN` | HiBob API |
| `AI_API_KEY` | LLM API key for AI resolver |
| `MATCHING_EMAIL_DOMAIN` | Email domain for matching |
| `MATCHING_SKIP_USERNAMES` | Comma-separated skip list |

---

## Slack Notifications

Sent to the configured channel for:

- **Correction mismatches** - assets assigned to wrong user, needs investigation
- **Unmatched devices** - local account couldn't be matched to any Snipe-IT user (AI also couldn't resolve)
- **Ambiguous name matches** - duplicate users in Snipe-IT need merging
- **Module failures** - error details with stack trace
- **Run summary** - only on errors (no news is good news)

---

## Project Structure

```
src/
  clients/          API client wrappers (Jamf, Snipe-IT, Azure, HiBob, Slack)
  core/             Config loader, client factory, state management
  infra/            Audit CSV, health server, progress tracker, helpers
  matching/         User matcher (fuzzy + AI resolver)
  modules/
    lifecycle/      Azure Starters, User Enrichment, Leavers
    sync/           User Match, Correction, Snipe-to-Jamf, Model Sync, Peripherals
    maintenance/    Cleanup, Reconciliation, Username Standardize, WakeUp
  main.py           CLI entry point
  docker_scheduler.py   Docker mode with scheduler + on-demand menu

terraform/
  modules/jamf-snipeit-suite/   Reusable ECS Fargate module
  environments/prod/            Production deployment

config/
  config.yaml.example           Template (safe to commit)
  equipment_mapping.json        HiBob name -> Snipe-IT accessory mapping
```

---

## License

MIT License - see [LICENSE](LICENSE)

## Author

**Davide Caputo** - TechOps
