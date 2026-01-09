<p align="center">
  <img src="https://img.shields.io/badge/Jamf-Pro-purple?style=for-the-badge&logo=jamf" alt="Jamf Pro"/>
  <img src="https://img.shields.io/badge/Snipe--IT-green?style=for-the-badge" alt="Snipe-IT"/>
  <img src="https://img.shields.io/badge/Azure%20AD-blue?style=for-the-badge&logo=microsoftazure" alt="Azure AD"/>
</p>

# 🔄 Jamf-SnipeIT Suite

> **Unified Asset Management & Synchronization Tool**

A comprehensive automation tool for synchronizing device and user information between **Jamf Pro**, **Snipe-IT**, and **Azure AD/Microsoft Entra ID**.

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+"/>
  <img src="https://img.shields.io/badge/docker-ready-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker"/>
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT License"/>
  <img src="https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square" alt="PRs Welcome"/>
</p>

---

## 📑 Table of Contents

- [✨ Features](#-features)
- [🏗️ Architecture](#️-architecture)
- [🚀 Quick Start](#-quick-start)
- [📦 Installation](#-installation)
- [⚙️ Configuration](#️-configuration)
- [🎮 Usage](#-usage)
- [📚 Modules](#-modules)
- [🐳 Docker](#-docker)
- [⏰ Scheduling](#-scheduling)
- [🔧 Troubleshooting](#-troubleshooting)
- [🤝 Contributing](#-contributing)
- [📄 License](#-license)

---

## ✨ Features

| Module | Description | Use Case |
|:-------|:------------|:---------|
| 🚪 **Leavers** | Auto-detect disabled Azure AD users and update asset status | Employee offboarding |
| 🆕 **Azure Starters** | Create Snipe-IT users from Azure AD starters group | Employee onboarding |
| 🔄 **Snipe-to-Jamf** | Sync user info from Snipe-IT to Jamf Pro | Keep Jamf user data accurate |
| 🔗 **User Match** | Match Jamf computers to Snipe-IT users with fuzzy matching | Device provisioning |
| 📋 **Model Sync** | Sync hardware models between platforms | Asset model management |
| 📡 **WakeUp** | Send MDM redeploy commands to devices | Remote management recovery |
| 📊 **Reconciliation** | Compare inventory and find discrepancies | Audit & compliance |

### 🎯 Key Benefits

- ✅ **Automated Workflows** - Set it and forget it with scheduled jobs
- ✅ **Bi-directional Sync** - Keep all systems in harmony
- ✅ **Smart Matching** - Fuzzy algorithms link devices with users
- ✅ **Dry Run Mode** - Preview changes before applying
- ✅ **Docker Ready** - Deploy anywhere with containers
- ✅ **Audit Trail** - Comprehensive logging and CSV exports

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Jamf-SnipeIT Suite                          │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │   main.py   │  │ scheduler.py│  │  docker_scheduler.py    │  │
│  │    (CLI)    │  │  (APSched)  │  │  (Docker + stdin)       │  │
│  └──────┬──────┘  └──────┬──────┘  └───────────┬─────────────┘  │
│         │                │                     │                │
│         └────────────────┼─────────────────────┘                │
│                          ▼                                      │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                      MODULES                              │  │
│  │  ┌─────────┐ ┌───────────┐ ┌──────────┐ ┌───────────┐     │  │
│  │  │ Leavers │ │ AzStarter │ │SnipeJamf │ │ UserMatch │     │  │
│  │  └────┬────┘ └─────┬─────┘ └────┬─────┘ └─────┬─────┘     │  │
│  │  ┌────┴────┐ ┌─────┴─────┐ ┌────┴─────┐                   │  │
│  │  │ModelSync│ │  WakeUp   │ │Reconcile │                   │  │
│  │  └────┬────┘ └─────┬─────┘ └────┬─────┘                   │  │
│  └───────┼────────────┼──────────────────────────────────────┘  │
│          │            │                                         │
│          ▼            ▼                                         │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                       CORE                                │  │
│  │  ┌──────────────┐ ┌────────────────┐ ┌─────────────────┐  │  │
│  │  │  JamfClient  │ │ SnipeITClient  │ │   AzureClient   │  │  │
│  │  └──────┬───────┘ └───────┬────────┘ └────────┬────────┘  │  │
│  └─────────┼─────────────────┼───────────────────┼───────────┘  │
└────────────┼─────────────────┼───────────────────┼──────────────┘
             ▼                 ▼                   ▼
      ┌──────────┐      ┌───────────┐       ┌───────────┐
      │ Jamf Pro │      │  Snipe-IT │       │ Azure AD  │
      │   API    │      │    API    │       │ Graph API │
      └──────────┘      └───────────┘       └───────────┘
```

---

## 🚀 Quick Start

```bash
# Clone the repository
git clone https://github.com/CaputoDavide93/Jamf-SnipeIT-Suite.git
cd Jamf-SnipeIT-Suite

# Copy and configure
cp config/config.yaml.example config/config.yaml
# Edit config/config.yaml with your credentials

# Run with Docker
docker compose up -d

# Or run locally
pip install -r requirements.txt
python src/main.py --interactive
```

---

## 📦 Installation

### Prerequisites

| Requirement | Version |
|:------------|:--------|
| 🐍 Python | 3.11+ |
| 🐳 Docker | 20.10+ (optional) |
| 📦 Docker Compose | 2.0+ (optional) |

### API Access Required

- 🍎 **Jamf Pro** - API credentials or OAuth2
- 📦 **Snipe-IT** - API token
- 🔵 **Azure AD** - App registration with Graph API access

### Local Installation

```bash
# 1. Clone repository
git clone https://github.com/CaputoDavide93/Jamf-SnipeIT-Suite.git
cd Jamf-SnipeIT-Suite

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure
cp config/config.yaml.example config/config.yaml
nano config/config.yaml  # Add your credentials

# 5. Verify installation
python src/main.py --help
```

### Docker Installation

```bash
# 1. Clone and configure
git clone https://github.com/CaputoDavide93/Jamf-SnipeIT-Suite.git
cd Jamf-SnipeIT-Suite
cp config/config.yaml.example config/config.yaml
nano config/config.yaml

# 2. Build and run
docker compose build
docker compose up -d

# 3. View logs
docker compose logs -f
```

---

## ⚙️ Configuration

### Configuration File

Create `config/config.yaml` from the example template:

```yaml
# Jamf Pro Configuration
jamf:
  base_url: "https://your-instance.jamfcloud.com"
  # Option 1: Basic Auth
  username: "api-user"
  password: "api-password"
  # Option 2: OAuth2 (recommended)
  client_id: "your-client-id"
  client_secret: "your-client-secret"
  ea_snipe_asset_id: "Snipe-IT Asset ID"

# Snipe-IT Configuration
snipeit:
  base_url: "https://your-snipeit.example.com"
  api_token: "your-api-token"
  status_deployed_id: 2
  status_pending_id: 3

# Azure AD Configuration
azure:
  tenant_id: "your-tenant-id"
  client_id: "your-client-id"
  client_secret: "your-client-secret"
  leavers_group_id: "group-guid"
  starters_group_id: "group-guid"
```

> ⚠️ **Security Note:** Never commit `config/config.yaml` to version control! It's gitignored by default.

### API Credentials Setup

<details>
<summary>🍎 <b>Jamf Pro Setup</b></summary>

#### OAuth2 (Recommended)

1. Go to **Settings > System > API Roles and Clients**
2. Create an API Role with permissions:
   - `Read Computers`
   - `Update Computers`
   - `Read Smart Computer Groups`
   - `Send Computer Remote Command to Redeploy Management Framework`
3. Create an API Client and note the `client_id` and `client_secret`

#### Basic Auth (Legacy)

1. Create a Jamf Pro user account
2. Assign API access privileges

</details>

<details>
<summary>📦 <b>Snipe-IT Setup</b></summary>

1. Go to **Admin > User Management > Your User**
2. Navigate to **API Keys** tab
3. Create a new API key and copy the token

</details>

<details>
<summary>🔵 <b>Azure AD Setup</b></summary>

1. Go to **Azure Portal > Microsoft Entra ID > App registrations**
2. Create a new registration
3. Add API permissions:
   - `Microsoft Graph > Application > User.Read.All`
   - `Microsoft Graph > Application > Group.Read.All`
   - `Microsoft Graph > Application > GroupMember.Read.All`
4. Grant admin consent
5. Create a client secret under **Certificates & secrets**
6. Note the Application ID, Tenant ID, and secret

</details>

---

## 🎮 Usage

### CLI Commands

```bash
# Show help
python src/main.py --help

# Run in interactive mode
python src/main.py --interactive

# Run specific modules
python src/main.py leavers [--dry-run]
python src/main.py azure-starters [--dry-run]
python src/main.py snipe-to-jamf [--dry-run]
python src/main.py user-match [--dry-run]
python src/main.py model-sync [--dry-run] [--check-only]
python src/main.py reconcile [--export-csv]

# Run all modules
python src/main.py all [--dry-run]

# WakeUp module
python src/main.py wakeup --group <ID>
python src/main.py wakeup --serial <SERIAL>
python src/main.py wakeup --file serials.txt
```

### Interactive Menu

```
╔═══════════════════════════════════════════════════════════╗
║                   Jamf-SnipeIT Suite                      ║
║     Unified Asset Management & Synchronization Tool       ║
╚═══════════════════════════════════════════════════════════╝

  Available Modules:
  1. Leavers - Mark assets of disabled Azure users
  2. Azure Starters - Create Snipe-IT users from Azure AD
  3. Snipe-to-Jamf - Sync user info from Snipe-IT to Jamf
  4. User Match - Match Jamf computers to Snipe-IT users
  5. Model Sync - Sync hardware models between platforms
  6. WakeUp - Send MDM redeploy commands
  7. Reconciliation - Find inventory discrepancies
  8. Run All (except WakeUp)
  0. Exit
```

### Dry Run Mode

All modules support `--dry-run` to preview changes without applying them:

```bash
python src/main.py leavers --dry-run
```

```
2026-01-02 10:15:32 - INFO - [DRY-RUN] Would mark asset MacBook-001 as pending
2026-01-02 10:15:33 - INFO - [DRY-RUN] Would mark asset MacBook-002 as pending
```

---

## 📚 Modules

### 🚪 Leavers Module

Automatically detect users disabled in Azure AD and update their asset status in Snipe-IT.

```bash
python src/main.py leavers --dry-run
```

**Workflow:**

1. Query Azure AD leavers group for disabled users
2. Find corresponding Snipe-IT user records
3. Update assigned assets to "Pending" status

---

### 🆕 Azure Starters Module

Create Snipe-IT users for new employees from Azure AD starters group.

```bash
python src/main.py azure-starters --dry-run
```

**Workflow:**

1. Query Azure AD starters group
2. Check if users exist in Snipe-IT
3. Create missing users with details from Azure AD

---

### 🔄 Snipe-to-Jamf Module

Sync user information from Snipe-IT to Jamf Pro computer records.

```bash
python src/main.py snipe-to-jamf --dry-run
```

**Fields Synced:**

| Snipe-IT | Jamf Pro |
|:---------|:---------|
| User Name | Real Name |
| Email | Email Address |
| Job Title | Position |
| Department | Department |

---

### 🔗 User Match Module

Match Jamf computers to Snipe-IT users using fuzzy matching algorithms.

```bash
python src/main.py user-match --dry-run
```

**Matching Strategy:**

1. Email exact match (highest confidence)
2. Username match
3. Fuzzy name matching with configurable threshold

---

### 📋 Model Sync Module

Ensure hardware models exist in Snipe-IT before assets can be created.

```bash
python src/main.py model-sync --check-only  # Preview only
python src/main.py model-sync --dry-run     # Preview changes
python src/main.py model-sync               # Create missing models
```

---

### 📡 WakeUp Module

Send MDM redeploy commands to unresponsive devices.

```bash
python src/main.py wakeup --group 123        # By Smart Group ID
python src/main.py wakeup --serial C02XYZ    # By serial number
python src/main.py wakeup --file serials.txt # From file
```

---

### 📊 Reconciliation Module

Compare inventory between Jamf and Snipe-IT to identify discrepancies.

```bash
python src/main.py reconcile --export-csv --output-dir ./reports
```

**Output:**

```
🔍 Inventory Reconciliation Results
═══════════════════════════════════════════
Jamf Pro devices:    1,234
Snipe-IT assets:     1,198

Only in Jamf:           45 devices
Only in Snipe-IT:        9 assets
Matched:             1,189 devices
═══════════════════════════════════════════
```

---

## 🐳 Docker

### Default Mode (Scheduler)

```bash
# Start scheduler with all jobs
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

### CLI Mode

```bash
# Run specific module
docker compose run --rm cli leavers --dry-run

# Interactive mode
docker compose run --rm cli --interactive
```

### Run Once

```bash
# Execute all modules once and exit
docker compose --profile run-once run --rm run-once
```

### Scheduler Commands

When the scheduler is running, attach and send commands:

```bash
docker attach jamf-snipeit
# Type: NOW     - Run all modules immediately
# Type: STATUS  - Show scheduler status
# Ctrl+C        - Graceful shutdown
```

---

## ⏰ Scheduling

Configure automated schedules in `config.yaml`:

```yaml
scheduler:
  enabled: true
  timezone: "Europe/London"
  run_on_startup: true
  jobs:
    azure_starters:
      cron: "0 6 * * 1"    # Monday 6am
      enabled: true
    model_sync:
      cron: "0 1 * * 0"    # Sunday 1am
      enabled: true
    user_match:
      cron: "30 6 * * *"   # Daily 6:30am
      enabled: true
    snipe_to_jamf:
      cron: "0 7 * * *"    # Daily 7am
      enabled: true
    leavers:
      cron: "30 7 * * *"   # Daily 7:30am
      enabled: true
```

### Cron Expression Reference

```
┌───────────── minute (0-59)
│ ┌───────────── hour (0-23)
│ │ ┌───────────── day of month (1-31)
│ │ │ ┌───────────── month (1-12)
│ │ │ │ ┌───────────── day of week (0-6, Sun=0)
│ │ │ │ │
* * * * *
```

| Expression | Description |
|:-----------|:------------|
| `0 6 * * *` | Daily at 6:00 AM |
| `0 6 * * 1` | Monday at 6:00 AM |
| `0 */4 * * *` | Every 4 hours |
| `0 8 * * 1-5` | Weekdays at 8:00 AM |

---

## 🔧 Troubleshooting

<details>
<summary>❌ <b>Jamf Pro Authentication Error</b></summary>

```
Error: Failed to obtain Jamf access token
```

**Solutions:**

- Verify `client_id` and `client_secret` are correct
- Check API client has not expired
- Ensure API client has required privileges

</details>

<details>
<summary>❌ <b>Snipe-IT 401 Unauthorized</b></summary>

```
Error: 401 Unauthorized
```

**Solutions:**

- Verify API token is valid and not expired
- Check token has full access permissions

</details>

<details>
<summary>❌ <b>Azure AD Invalid Client Secret</b></summary>

```
Error: AADSTS7000215: Invalid client secret
```

**Solutions:**

- Client secret may have expired
- Create a new secret in Azure Portal

</details>

<details>
<summary>❌ <b>Model Not Found</b></summary>

```
Error: Model not found for asset creation
```

**Solutions:**

1. Run `python src/main.py model-sync --check-only`
2. Run `python src/main.py model-sync` to create missing models
3. Then run your original command

</details>

### Debug Mode

Enable verbose logging:

```bash
python src/main.py leavers --dry-run --verbose
```

---

## 📁 Project Structure

```
Jamf-SnipeIT-Suite/
├── 📁 config/
│   ├── config.yaml.example    # Template (safe to commit)
│   └── config.yaml            # Your config (gitignored)
├── 📁 src/
│   ├── 📁 core/               # API clients
│   │   ├── jamf_client.py
│   │   ├── snipe_client.py
│   │   ├── azure_client.py
│   │   └── config.py
│   ├── 📁 modules/            # Feature modules
│   │   ├── leavers.py
│   │   ├── azure_starters.py
│   │   ├── snipe_to_jamf.py
│   │   ├── user_match.py
│   │   ├── model_sync.py
│   │   ├── wakeup.py
│   │   └── reconciliation.py
│   ├── 📁 utils/              # Shared utilities
│   ├── main.py                # CLI entry point
│   ├── scheduler.py           # APScheduler
│   └── docker_scheduler.py    # Docker scheduler
├── 📁 logs/                   # Log files (gitignored)
├── 📁 output/                 # Audit CSVs (gitignored)
├── 🐳 Dockerfile
├── 🐳 docker-compose.yml
├── 📋 requirements.txt
├── 📜 LICENSE
├── 🔐 SECURITY.md
├── 🤝 CONTRIBUTING.md
└── 📖 README.md
```

---

## 🤝 Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests and linting
5. Submit a pull request

---

## 🔐 Security

See [SECURITY.md](SECURITY.md) for security policy and guidelines.

**Important:** Never commit credentials or secrets!

---

## 📄 License

This project is licensed under the MIT License - see [LICENSE](LICENSE) for details.

---

## 👤 Author

**Davide Caputo**

- 📧 Email: CaputoDav@gmail.com

---

<p align="center">
  Made with ❤️ for IT Asset Management
</p>
