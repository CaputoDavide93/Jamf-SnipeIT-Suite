# Jamf-SnipeIT Suite

Weekly sync of devices, users, and accessories between Jamf Pro, Snipe-IT, Azure AD, and HiBob. Zero-touch provisioning via AWS ECS Fargate.

## Stack
- Python 3.12
- Jamf Pro API / Snipe-IT API / HiBob API / Microsoft Graph API
- AWS ECS Fargate + EventBridge (4 scheduled tasks)
- Docker / Terraform

## Quick Start

```bash
cp .env.example .env
# fill credentials
docker compose up
```

## Key Config

| Variable | Purpose |
|----------|---------|
| `JAMF_URL` + `JAMF_TOKEN` | Jamf Pro connection |
| `SNIPEIT_URL` + `SNIPEIT_TOKEN` | Snipe-IT API |
| `HIBOB_TOKEN` | HiBob service token |
| `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` | Azure AD read access |

---

Full docs -> [Jamf-SnipeIT Suite on Confluence](https://xsolutions.atlassian.net/wiki/pages/viewpage.action?pageId=4493508620)
