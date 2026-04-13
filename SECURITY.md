# Security Policy

## Handling sensitive data

This application handles credentials for multiple systems:

- **Jamf Pro** (API credentials / OAuth)
- **Snipe-IT** (API token)
- **Azure AD / Microsoft Entra ID** (client secret)
- **Slack** (bot token)
- **HiBob** (service user token)
- **Anthropic** (LLM API key)

### Credential storage

**Local development:** `config/config.yaml` (gitignored, never committed)

**Production (AWS Fargate):** AWS SSM Parameter Store SecureString, all paths under `/jamf-snipeit-suite-prod/*`. Values are:
- Encrypted at rest with AWS KMS
- Decrypted at container start via the ECS execution role
- Never visible in the ECS task definition, AWS Console, or CloudTrail

The ECS execution role has `ssm:GetParameters` scoped **only** to this project's parameter namespace.

### Never commit

- `config/config.yaml`
- `config/*.sb-*` (editor swap files)
- `terraform/environments/*/terraform.tfvars`
- Any file starting with a recognisable credential prefix (`xoxb-`, `sk-ant-`, `eyJ...`)

The `.gitignore` covers all of these. Every commit is scanned before pushing to both GitHub and Bitbucket.

## Network security

### AWS Fargate task

- **Account lock:** `allowed_account_ids = ["<AWS_ACCOUNT_ID>"]` — Terraform refuses to deploy anywhere else
- **Region lock:** `eu-west-1` only (validation block)
- **Security group:** egress-only, no inbound rules
- **VPC:** runs in the default VPC with a public IP (needed for outbound to SaaS APIs)
- **Tags:** every resource tagged `Owner: Davide Caputo - TechOps`

### Logging

CloudWatch logs are sanitised:
- API response bodies truncated to 200 chars
- Bearer tokens masked (`***` after first 8 chars)
- No credentials or secrets ever logged at INFO or WARN
- XML/JSON payloads shown as structural placeholders only

## Identity and access

### IAM least privilege

| Role | Permissions |
|------|-------------|
| ECS Execution Role | `AmazonECSTaskExecutionRolePolicy` + scoped `ssm:GetParameters` for this project only |
| ECS Task Role | `s3:GetObject/PutObject` on the AI cache bucket only |
| EventBridge Role | `ecs:RunTask` on this task definition only + scoped `iam:PassRole` |

### Auto-created user passwords

New Snipe-IT users created from Azure AD receive a cryptographically random 24-character password. Users must reset it via Snipe-IT's password reset flow — the plaintext is never logged or stored.

## Change management

### Secret rotation

```bash
# Rotate any credential via SSM — next Fargate run picks it up automatically
aws ssm put-parameter \
  --name /jamf-snipeit-suite-prod/<name> \
  --value "<new-value>" \
  --type SecureString \
  --overwrite
```

### Removing a credential

1. Delete the SSM parameter (`aws ssm delete-parameter --name ...`)
2. Remove the env var reference from `terraform/modules/jamf-snipeit-suite/ecs.tf`
3. Run `terraform apply` to register the updated task definition

## Reporting a vulnerability

Email the maintainer at **CaputoDav@gmail.com**. Do not open a public GitHub issue.

Expected response:
- Acknowledgement: within 48 hours
- Initial assessment: within 1 week
- Fix timeline depends on severity

## Security checklist (for contributors)

Before opening a PR, verify:

- [ ] No credentials in code or commit messages
- [ ] No real URLs pointing to internal instances
- [ ] No employee names, emails, or IDs in code (only in gitignored config)
- [ ] Example files use placeholder values
- [ ] New logging statements don't leak API responses or tokens
- [ ] Dependencies pinned (no `latest` floating references)

---

**Maintained by:** Davide Caputo (CaputoDav@gmail.com)
