# Operations Guide

Day-to-day tasks for running the Jamf-SnipeIT Suite in production.

## Placeholders in this guide

This repo is mirrored publicly, so infrastructure identifiers are written as
placeholders. Substitute them before running any command below:

| Placeholder | How to get the real value |
|-------------|---------------------------|
| `<AWS_ACCOUNT_ID>` | `aws sts get-caller-identity --query Account --output text` |
| `<SUBNET_ID>` / `<SECURITY_GROUP_ID>` | `terraform output` in `terraform/environments/prod`, or the ECS service's network config in the console |

`./scripts/deploy.sh` resolves the account ID itself — prefer it over the manual
Docker commands where you can.

## Daily checks

The system runs automatically at 06:00 UTC. You should only need to check:

1. **Slack channel** (`C0AGENA7P43`) — only posts when there's something to action
2. **CloudWatch logs** — `/ecs/jamf-snipeit-suite-prod` if you want to see full run details

### What Slack alerts mean

| Alert | What to do |
|-------|------------|
| `Correction — Assignment Mismatches` | A mismatch was found AND auto-correction failed. Investigate the specific asset in Snipe-IT. |
| `User Match — Unmatched Devices` | A machine has a local account we can't match to anyone. Usually means a new hire not yet in Azure AD, or an ex-employee whose data is gone. |
| `No local user account (only admin/system accounts)` | Shared / test-lab Mac with no real person account on it. Assign the real owner manually in Snipe-IT — the system will not revert it (it never reassigns without a local-account match). |
| `Duplicate Users in Snipe-IT` | Two Snipe-IT users share the same display name. Merge them manually. |
| `AI Cross-Platform Audit` | Weekly audit findings. Review severity and take action on critical/high items. |
| `Module Failure` | A module crashed. Check CloudWatch logs for stack trace. |

Successful auto-corrections **do not** generate Slack alerts — silent operation means it worked.

## Common tasks

### Deploying a new image

Run these three commands in order. Docker Desktop must be running.

```bash
# 1. ECR login (token valid 12h)
aws ecr get-login-password --region eu-west-1 \
  | docker login --username AWS --password-stdin \
    <AWS_ACCOUNT_ID>.dkr.ecr.eu-west-1.amazonaws.com

# 2. Build — MUST be linux/amd64; ARM64 silently fails on Fargate
docker build --platform linux/amd64 \
  -t <AWS_ACCOUNT_ID>.dkr.ecr.eu-west-1.amazonaws.com/jamf-snipeit-suite-prod:latest .

# 3. Push
docker push \
  <AWS_ACCOUNT_ID>.dkr.ecr.eu-west-1.amazonaws.com/jamf-snipeit-suite-prod:latest
```

The ECS task definition already references `:latest` — no task-def update needed.
The next EventBridge trigger picks up the new image automatically.

To verify the push landed:
```bash
aws ecr describe-images \
  --repository-name jamf-snipeit-suite-prod \
  --region eu-west-1 \
  --query 'sort_by(imageDetails,&imagePushedAt)[-1].[imagePushedAt,imageDigest]' \
  --output table
```

To smoke-test without waiting for a schedule:
```bash
aws ecs run-task \
  --cluster jamf-snipeit-suite-prod \
  --task-definition jamf-snipeit-suite-prod \
  --launch-type FARGATE \
  --overrides '{"containerOverrides":[{"name":"app","command":["health-check"]}]}' \
  --network-configuration "awsvpcConfiguration={subnets=[<SUBNET_ID>],securityGroups=[<SECURITY_GROUP_ID>],assignPublicIp=ENABLED}" \
  --region eu-west-1
```

---

### Adding a user override

When matching consistently picks the wrong Snipe-IT user for a given Jamf local account:

1. Edit `config/user_overrides.json`:
   ```json
   {
     "overrides": {
       "jamf_local_username": {
         "snipe_user_id": 1234,
         "snipe_user_name": "Jane Doe",
         "reason": "Explain why this override exists"
       }
     }
   }
   ```
2. Keep the file local; it contains PII and is intentionally gitignored.
3. Rebuild the Docker image and push to ECR. The build includes the override when present, while secret YAML files remain excluded by `.dockerignore`.
4. The next scheduled run will use the override.

**Normalisation rules:** keys are matched case-insensitive and dot/dash/underscore-insensitive. `matt-personal` and `matt.personal` and `MATTPERSONAL` all match the same override key.

### Onboarding a new SSO (SAML) user in Snipe-IT

**Background (2026-08-05):** the Snipe-IT EC2 migration surfaced a real gap — Snipe-IT's SAML login hardcodes matching the IdP's `emailaddress` claim (a full email address) against the local `username` column. Nearly every Snipe-IT user has a short-form `username` (`firstname.lastname`, no `@domain`) because that's what the Jamf-device-matching logic in this suite (`UserMatcher`) is built around. The two are incompatible for any user who actually needs to log into Snipe-IT via SSO.

`username_standardize.py` runs on a schedule and actively converts any `username` containing `@` back to short form — so a manual fix to `username` alone will get silently reverted.

When someone new needs SAML login access to Snipe-IT:

1. Let them get provisioned normally first (via Azure Starters or however their account already exists) — don't change anything yet.
2. In Snipe-IT (Admin → Users → edit user), set their `Username` field to their **full email address**, matching exactly what Azure sends.
3. Add that email (lowercase) to `preserved_usernames` in `config/user_overrides.json`:
   ```json
   "preserved_usernames": [
     "davide.caputo@xdesign.com",
     "daniel.mcmanus@createfuture.com",
     "new.person@createfuture.com"
   ]
   ```
   This is the only thing that stops `username_standardize.py` reverting the change on its next scheduled run.
4. In Snipe-IT, make sure their account is **Activated** — this is the actual login gate (`samlLogin()` requires `activated = 1`; it won't find a deactivated user at all).

**Do not** change `azure_starters.py`/`user_match.py` to make *every* new user's username the full email address — that would break Jamf-device matching for everyone else, since `UserMatcher` is built around short-form usernames matching macOS local account names. The `preserved_usernames` override is the correct, narrow fix for the small number of people who actually need SSO.

### Adding a new equipment mapping (HiBob to Snipe-IT)

Edit `config/equipment_mapping.json`:
```json
{
  "mappings": {
    "New HiBob Equipment Name": "Canonical Snipe-IT Accessory Name"
  }
}
```

Rebuild and push the image. The next Monday 08:00 Peripherals Sync run will use it.

### Triggering an on-demand Fargate run

```bash
aws ecs run-task \
  --cluster jamf-snipeit-suite-prod \
  --task-definition jamf-snipeit-suite-prod \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<SUBNET_ID>],securityGroups=[<SECURITY_GROUP_ID>],assignPublicIp=ENABLED}" \
  --region eu-west-1
```

To run in dry-run mode, add:
```bash
  --overrides '{"containerOverrides":[{"name":"app","environment":[{"name":"DRY_RUN","value":"true"}]}]}'
```

Scheduler dry-run applies to the startup chain, every later APScheduler job,
and EventBridge CLI/`run-group` commands. A module's own
`modules.<name>.dry_run: true` setting can also force dry-run and cannot be
overridden by the caller.

For persistent Fargate controls, set maps in `terraform.tfvars` and apply a new
task-definition revision:

```hcl
module_enabled_overrides = {
  ai_audit = false
}
module_dry_run_overrides = {
  cleanup    = true
  user_match = true
}
```

Canonical names use underscores. Terraform converts these to
`MODULE_<NAME>_ENABLED` and `MODULE_<NAME>_DRY_RUN` task environment variables.

### Viewing logs

CloudWatch Logs: `/ecs/jamf-snipeit-suite-prod`

Filter by task:
```bash
aws logs get-log-events \
  --log-group-name /ecs/jamf-snipeit-suite-prod \
  --log-stream-name "ecs/app/<task-id>" \
  --query 'events[].message' --output text
```

Grep for specific events:
```bash
# Mismatches found
grep "MISMATCH" logs.txt

# AI resolver decisions
grep "AI resolver\|AI cross-platform" logs.txt

# User auto-created from Azure
grep "Auto-created Snipe-IT user" logs.txt
```

### Investigating a specific machine

```python
# In a Python shell with src/ on path
from clients.snipeit import SnipeITClient
from clients.jamf import JamfClient

snipe = SnipeITClient(...)
jamf = JamfClient(...)

# Check Snipe-IT
asset = snipe.get_asset_by_serial("SERIAL")
print(asset["status_label"], asset.get("assigned_to"))

# Check Jamf local accounts
jamf_comp = jamf.get_computer_by_serial("SERIAL")
local_users = jamf_comp["groups_accounts"]["local_accounts"]
for u in local_users:
    print(u.get("name"), u.get("realname"))
```

### Handling a leaver

Automatic — when someone is moved to the Azure AD leavers or disabled group:

1. Daily 07:30 Leavers module detects them
2. Their assets set to `Pending` status
3. Assignment **stays** with the leaver for tracking
4. If their Mac is reassigned to someone else, the `Correction` module fixes it automatically on the next 06:15 run

### Handling a new hire

Automatic — when IT sets up their Mac:

1. They're already in Azure AD (IT provisions in advance)
2. Jamf enrolls the Mac, local account `newhire` is created
3. Daily 06:30 User Match detects this local account
4. Not found in Snipe-IT → auto-creates them from Azure AD data
5. Assigns the Mac to the new user

No manual steps needed.

## AI resolver cache

Location: `s3://jamf-snipeit-suite-prod-ai-cache-<AWS_ACCOUNT_ID>/ai-resolver-cache.json`

The cache stores AI decisions for 30 days. If you need to force AI to re-evaluate:

```bash
aws s3 rm s3://jamf-snipeit-suite-prod-ai-cache-<AWS_ACCOUNT_ID>/ai-resolver-cache.json
```

Next run will rebuild the cache from scratch.

## Credentials

All secrets live in AWS SSM Parameter Store under `/jamf-snipeit-suite-prod/*`. To rotate:

```bash
aws ssm put-parameter \
  --name /jamf-snipeit-suite-prod/jamf-password \
  --value "new-password" \
  --type SecureString \
  --overwrite
```

The ECS task reads SSM parameters at container start — just trigger a new run to pick up the change.

## Schedule overview

All times UTC. Driven by four EventBridge rules, each fires the same Fargate
task with a `containerOverrides.command` so the entrypoint dispatches to the
right CLI subcommand or `run-group`.

**Scheduling safety**
- Each run grabs a mutex in SSM and refreshes it periodically so long runs stay protected. Missing credentials, IAM denial, or SSM failure aborts the run.
- The startup/run-once chain checks Snipe-IT, Jamf, and Azure before constructing modules. Any failed critical probe blocks the chain and returns a failed task status.
- Local development without AWS must opt out explicitly with `MUTEX_DISABLED=true`; never set this in ECS.
- APScheduler jobs have a small random `jitter` (default 180s) to avoid hitting APIs in a single burst when multiple jobs are adjacent.
- `/healthz` is the unauthenticated container liveness probe. Set `HEALTH_AUTH_TOKEN` to protect `/health`, readiness, status, and metrics when exposing them through a sidecar/ALB.
- AI audit identifiers are tokenised by default. Raw transfer requires `AI_AUDIT_ALLOW_EXTERNAL_PII=true` or `modules.ai_audit.allow_external_pii: true` and an approved data-processing policy.

| EventBridge rule | Cron (UTC) | UK time | Modules executed |
|------------------|------------|---------|------------------|
| `…-starters`     | Mon 05:50  | Mon 06:50 BST | azure-starters → user-enrichment → peripherals-sync |
| `…-sync`         | Tue 17:00  | Tue 18:00 BST | run-once: model-sync, correction, user-match, snipe-to-jamf, leavers (+ starters/enrichment/peripherals re-run) |
| `…-health`       | Mon+Thu 19:00 | 20:00 BST | health-check |
| `…-housekeeping` | Sun 21:00  | Sun 22:00 BST | cleanup → pending-reconciliation → jamf-location-cleanup → ai-audit → reconciliation |
| `…-monthly-digest` | First Mon 09:00 | 10:00 BST | monthly-digest |

`wakeup` is intentionally manual — invoke via CLI or interactive menu.

`username-standardize` was removed from the housekeeping chain on 2026-08-03:
it is a completed one-time migration and reported 807/807 usernames already
plain for three consecutive runs, so it only cost a full user fetch to do
nothing. Run it on demand if email-style usernames reappear:

```bash
aws ecs run-task --cluster jamf-snipeit-suite-prod \
  --task-definition jamf-snipeit-suite-prod --launch-type FARGATE \
  --overrides '{"containerOverrides":[{"name":"app","command":["run-group","--modules","username-standardize"],"environment":[{"name":"RUN_MODE","value":"cli"}]}]}' \
  --network-configuration "$NETWORK_CONFIG"
```

Group rules use the new CLI subcommand `run-group --modules a,b,c` which
serialises modules under `RunMutex` so two rules firing close together can't
collide.

## Emergency procedures

### Stop all scheduled runs

Disable every EventBridge rule:
```bash
for r in starters sync health housekeeping monthly-digest; do
  aws events disable-rule --name jamf-snipeit-suite-prod-$r --region eu-west-1
done
```
Re-enable with `enable-rule` instead of `disable-rule`.

### Stop a running task

```bash
aws ecs stop-task \
  --cluster jamf-snipeit-suite-prod \
  --task <task-arn> \
  --reason "Manual stop"
```

### Roll back to a previous image

```bash
# List images
aws ecr describe-images --repository-name jamf-snipeit-suite-prod --region eu-west-1

# Tag older image as latest
aws ecr put-image --repository-name jamf-snipeit-suite-prod \
  --image-tag latest \
  --image-manifest "$(aws ecr batch-get-image --repository-name jamf-snipeit-suite-prod --image-ids imageDigest=<sha> --query 'images[0].imageManifest' --output text)"
```
