# Operations Guide

Day-to-day tasks for running the Jamf-SnipeIT Suite in production.

## Daily checks

The system runs automatically at 06:00 UTC. You should only need to check:

1. **Slack channel** (`C0AGENA7P43`) — only posts when there's something to action
2. **CloudWatch logs** — `/ecs/jamf-snipeit-suite-prod` if you want to see full run details

### What Slack alerts mean

| Alert | What to do |
|-------|------------|
| `Correction — Assignment Mismatches` | A mismatch was found AND auto-correction failed. Investigate the specific asset in Snipe-IT. |
| `User Match — Unmatched Devices` | A machine has a local account we can't match to anyone. Usually means a new hire not yet in Azure AD, or an ex-employee whose data is gone. |
| `Duplicate Users in Snipe-IT` | Two Snipe-IT users share the same display name. Merge them manually. |
| `AI Cross-Platform Audit` | Weekly audit findings. Review severity and take action on critical/high items. |
| `Module Failure` | A module crashed. Check CloudWatch logs for stack trace. |

Successful auto-corrections **do not** generate Slack alerts — silent operation means it worked.

## Common tasks

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
2. Rebuild the Docker image and push to ECR:
   ```bash
   docker build --platform linux/amd64 -t <ECR_URL>:latest .
   docker push <ECR_URL>:latest
   ```
3. Commit the change to git.
4. The next 06:00 run will use the new override immediately.

**Normalisation rules:** keys are matched case-insensitive and dot/dash/underscore-insensitive. `matt-personal` and `matt.personal` and `MATTPERSONAL` all match the same override key.

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

All times UTC.

| Time | Module | Frequency |
|------|--------|-----------|
| 01:00 | Model Sync | Sunday |
| 02:30 | Username Standardize | Sunday |
| 03:00 | Cleanup | Sunday |
| 04:00 | AI Audit | Sunday |
| 05:00 | Reconciliation | Sunday |
| 06:00 | Azure Starters | Monday |
| 06:15 | Correction | Daily |
| 06:30 | User Match | Daily |
| 06:30 | User Enrichment | Monday |
| 07:00 | Snipe-to-Jamf | Daily |
| 07:30 | Leavers | Daily |
| 08:00 | Peripherals Sync | Monday |

## Emergency procedures

### Stop all scheduled runs

Disable the EventBridge rule:
```bash
aws events disable-rule --name jamf-snipeit-suite-prod-scheduled-run
```

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
