# Jamf-SnipeIT Suite

A comprehensive tool for managing asset synchronization between **Jamf Pro**, **Snipe-IT**, and **Azure AD/Microsoft Entra ID**.

![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Installation](#installation)
  - [Prerequisites](#prerequisites)
  - [Local Installation](#local-installation)
  - [Docker Installation](#docker-installation)
- [Configuration](#configuration)
  - [Configuration File](#configuration-file)
  - [API Credentials Setup](#api-credentials-setup)
  - [Environment Variables](#environment-variables)
- [Usage](#usage)
  - [CLI Commands](#cli-commands)
  - [Interactive Mode](#interactive-mode)
  - [Docker Usage](#docker-usage)
- [Modules](#modules)
  - [Leavers Module](#leavers-module)
  - [Azure Starters Module](#azure-starters-module)
  - [Snipe-to-Jamf Module](#snipe-to-jamf-module)
  - [User Match Module](#user-match-module)
  - [Model Sync Module](#model-sync-module)
  - [WakeUp Module](#wakeup-module)
  - [Reconciliation Module](#reconciliation-module)
- [Dry Run Mode](#dry-run-mode)
- [Scheduling](#scheduling)
- [Logging and Auditing](#logging-and-auditing)
- [Troubleshooting](#troubleshooting)
- [API Reference](#api-reference)
- [Development](#development)
- [License](#license)

---

## Overview

The Jamf-SnipeIT Suite consolidates multiple asset management workflows into a single, configurable application. It automates the synchronization of device and user information between:

- **Jamf Pro** - Apple device management (MDM)
- **Snipe-IT** - IT asset management system
- **Azure AD / Microsoft Entra ID** - Identity and access management

### Key Benefits

- **Automated User Offboarding**: Automatically detect disabled Azure AD accounts and mark their assets as pending in Snipe-IT
- **Bi-directional Sync**: Keep user information synchronized between Snipe-IT and Jamf Pro
- **Smart Matching**: Fuzzy matching algorithms to link devices with users across systems
- **Model Management**: Auto-create and sync hardware models between platforms
- **Audit Trail**: Comprehensive logging and CSV exports for all operations

---

## Features

| Module | Description | Use Case |
|--------|-------------|----------|
| **Leavers** | Marks Snipe-IT assets as pending when users are disabled in Azure AD | Employee offboarding |
| **Azure Starters** | Creates Snipe-IT users from Azure AD starters group | Employee onboarding |
| **Snipe-to-Jamf** | Syncs user information from Snipe-IT assets to Jamf Pro computer records | Keep Jamf user data accurate |
| **User Match** | Matches Jamf computers to Snipe-IT users using fuzzy matching | New device provisioning |
| **Model Sync** | Synchronizes hardware models between Jamf Pro and Snipe-IT | Ensure models exist before asset creation |
| **WakeUp** | Sends MDM redeploy commands to devices | Remote management recovery |
| **Reconciliation** | Compares inventory between Jamf and Snipe-IT | Audit and discrepancy detection |

---

## Architecture

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

## Installation

### Prerequisites

- **Python 3.11+** (for local execution)
- **Docker & Docker Compose** (for containerized execution)
- API access to:
  - Jamf Pro (with API credentials or OAuth2)
  - Snipe-IT (with API token)
  - Azure AD / Microsoft Entra ID (with app registration)

### Local Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/CaputoDavide93/Jamf-SnipeIT-Suite.git
   cd Jamf-SnipeIT-Suite
   ```

2. **Create a virtual environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure the application**:
   ```bash
   cp config/config.yaml.example config/config.yaml
   # Edit config/config.yaml with your credentials
   ```

5. **Test the installation**:
   ```bash
   python src/main.py --help
   ```

### Docker Installation

1. **Clone and configure**:
   ```bash
   git clone https://github.com/CaputoDavide93/Jamf-SnipeIT-Suite.git
   cd Jamf-SnipeIT-Suite
   cp config/config.yaml.example config/config.yaml
   # Edit config/config.yaml
   ```

2. **Build the image**:
   ```bash
   docker compose build
   ```

3. **Test the installation**:
   ```bash
   docker compose --profile cli run --rm jamf-snipeit-cli --help
   ```

---

## Configuration

### Configuration File

The application uses a YAML configuration file located at `config/config.yaml`. A template is provided at `config/config.yaml.example`.

```yaml
# Jamf Pro Configuration
jamf:
  base_url: "https://your-jamf-instance.jamfcloud.com"
  # Option 1: Basic Auth (legacy)
  username: "api-user"
  password: "api-password"
  # Option 2: OAuth2 (recommended)
  client_id: "your-client-id"
  client_secret: "your-client-secret"
  
  # Extension Attribute name for Snipe-IT Asset ID
  ea_snipe_asset_id: "Snipe-IT Asset ID"

# Snipe-IT Configuration
snipeit:
  base_url: "https://your-snipeit-instance.example.com"
  api_token: "your-api-token"
  
  # Status IDs (find these in Snipe-IT admin)
  status_deployed_id: 2
  status_pending_id: 3
  
  # Default values for new assets
  company_id: 1
  location_id: 1
  
  # Category ID for computer assets
  category_id: 2

# Azure AD / Microsoft Entra ID Configuration
azure:
  tenant_id: "your-tenant-id"
  client_id: "your-app-client-id"
  client_secret: "your-app-client-secret"
  
  # Azure AD groups to monitor
  leavers_group_id: "group-guid-for-leavers"
  starters_group_id: "group-guid-for-starters"

# Module-specific settings
user_match:
  smart_group_id: 123                    # Jamf Smart Group to process
  allow_reassignment: false              # Allow reassigning assets to different users
  fuzzy_match_threshold: 80              # Minimum match score (0-100)

# Azure Starters module settings
modules:
  azure_starters:
    update_job_titles: true              # Update job titles for existing users
    default_password: "YourSecurePassword123!"  # Password for new users (10+ chars)

# Scheduler Configuration (for automated runs)
scheduler:
  jobs:
    leavers:
      enabled: true
      cron: "0 6 * * *"                  # Daily at 6 AM
    snipe_to_jamf:
      enabled: true
      cron: "0 7 * * *"                  # Daily at 7 AM
    user_match:
      enabled: true
      cron: "0 8 * * *"                  # Daily at 8 AM
    model_sync:
      enabled: true
      cron: "0 0 * * 0"                  # Weekly on Sunday at midnight

# Logging
logging:
  level: "INFO"                          # DEBUG, INFO, WARNING, ERROR
  file: "logs/jamf-snipeit.log"
```

### API Credentials Setup

#### Jamf Pro

**Option 1: OAuth2 (Recommended)**
1. Go to **Settings > System > API Roles and Clients**
2. Create a new API Role with required permissions:
   - `Read Computers`
   - `Update Computers`
   - `Read Smart Computer Groups`
   - `Send Computer Remote Command to Redeploy Management Framework`
3. Create an API Client with the role and note the `client_id` and `client_secret`

**Option 2: Basic Auth (Legacy)**
1. Create a Jamf Pro user account
2. Assign the user to a group with API access privileges

#### Snipe-IT

1. Go to **Admin > User Management** and click on your user
2. Navigate to **API Keys** tab
3. Create a new API key and copy the token

#### Azure AD / Microsoft Entra ID

1. Go to **Azure Portal > Microsoft Entra ID > App registrations**
2. Create a new registration
3. Add API permissions:
   - `Microsoft Graph > Application permissions > User.Read.All`
   - `Microsoft Graph > Application permissions > Group.Read.All`
   - `Microsoft Graph > Application permissions > GroupMember.Read.All`
4. Grant admin consent
5. Create a client secret under **Certificates & secrets**
6. Note the `Application (client) ID`, `Directory (tenant) ID`, and client secret

### Environment Variables

As an alternative to the config file, you can use environment variables:

```bash
export JAMF_BASE_URL="https://your-jamf.jamfcloud.com"
export JAMF_CLIENT_ID="your-client-id"
export JAMF_CLIENT_SECRET="your-client-secret"
export SNIPEIT_BASE_URL="https://your-snipeit.example.com"
export SNIPEIT_API_TOKEN="your-token"
export AZURE_TENANT_ID="your-tenant-id"
export AZURE_CLIENT_ID="your-client-id"
export AZURE_CLIENT_SECRET="your-secret"
```

---

## Usage

### CLI Commands

#### Global Options

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to config file (default: `config/config.yaml`) |
| `--verbose` | `-v` | Enable debug logging |
| `--log-file` | `-l` | Write logs to specified file |
| `--interactive` | `-i` | Run in interactive menu mode |

#### Available Commands

```bash
# View all commands
python src/main.py --help

# Run specific module
python src/main.py leavers [--dry-run]
python src/main.py snipe-to-jamf [--dry-run]
python src/main.py user-match [--dry-run]
python src/main.py model-sync [--dry-run] [--check-only]
python src/main.py wakeup --group <ID> [--dry-run]
python src/main.py wakeup --serial <SERIAL> [--dry-run]
python src/main.py wakeup --file <PATH> [--dry-run]
python src/main.py reconcile [--export-csv] [--output-dir ./output]

# Run all modules (except WakeUp)
python src/main.py all [--dry-run]
```

### Interactive Mode

Launch the interactive menu for guided execution:

```bash
python src/main.py --interactive
```

```
╔═══════════════════════════════════════════════════════════╗
║                   Jamf-SnipeIT Suite                      ║
║     Unified Asset Management & Synchronization Tool       ║
╚═══════════════════════════════════════════════════════════╝

  Available Modules:
  1. Leavers - Mark assets of disabled Azure users
  2. Snipe-to-Jamf - Sync user info from Snipe-IT to Jamf
  3. User Match - Match Jamf computers to Snipe-IT users
  4. Model Sync - Sync hardware models between platforms
  5. WakeUp - Send MDM redeploy commands
  6. Reconciliation - Find inventory discrepancies
  7. Run All (except WakeUp)
  8. Run All (DRY RUN)
  0. Exit

  Enter your choice:
```

### Docker Usage

#### One-time Commands

```bash
# Run with dry-run
docker compose --profile cli run --rm jamf-snipeit-cli leavers --dry-run

# Run all modules
docker compose --profile run-all up

# Run specific module with verbose logging
docker compose --profile cli run --rm jamf-snipeit-cli snipe-to-jamf -v
```

#### Scheduler (Background Service)

```bash
# Start the scheduler
docker compose --profile scheduler up -d

# View logs
docker compose --profile scheduler logs -f

# Stop the scheduler
docker compose --profile scheduler down
```

#### Docker Scheduler Commands

When the scheduler is running, you can send commands via stdin:

- Type `NOW` and press Enter to run all modules immediately
- Type `STATUS` to see scheduler status and next run times
- Press `Ctrl+C` to gracefully shutdown

---

## Modules

### Leavers Module

**Purpose**: Automatically detect users who have been disabled in Azure AD and mark their assigned assets in Snipe-IT as "Pending" status.

**Workflow**:
1. Query Azure AD for members of the configured leavers group (disabled users)
2. For each disabled user, find their Snipe-IT user record
3. Find all assets assigned to that user
4. Update asset status to "Pending"
5. Optionally prefix user's name with `[Disabled]`

**Usage**:
```bash
python src/main.py leavers --dry-run    # Preview changes
python src/main.py leavers              # Execute changes
```

**Configuration**:
```yaml
azure:
  leavers_group_id: "your-group-guid"   # Azure AD group containing disabled users

snipeit:
  status_pending_id: 3                  # Snipe-IT status ID for "Pending"
```

---

### Azure Starters Module

**Purpose**: Automatically create Snipe-IT users for new employees added to an Azure AD starters group. This ensures new starters have user records ready in Snipe-IT before they receive their equipment.

**Workflow**:
1. Query Azure AD for members of the configured starters group
2. For each Azure AD user, check if they already exist in Snipe-IT (by email)
3. If user doesn't exist, create a new Snipe-IT user with:
   - First name and last name from Azure AD
   - Email address
   - Username (derived from email)
   - Job title (from Azure AD)
   - Default password (configurable)
4. If user exists but job title differs, optionally update the job title

**Usage**:
```bash
python src/main.py azure-starters --dry-run    # Preview changes
python src/main.py azure-starters              # Execute changes
```

**Configuration**:
```yaml
azure:
  starters_group_id: "your-starters-group-guid"   # Azure AD group for new starters

modules:
  azure_starters:
    update_job_titles: true                       # Update job titles for existing users
    default_password: "YourSecurePassword123!"    # Default password (10+ chars required)
```

**Example Output**:
```
Azure Starters Module - Syncing to Snipe-IT
============================================================
Found 472 users in Azure starters group
Found 471 users already in Snipe-IT
To create: 1 new users

Processing: Chris Abbott-Hauxwell (chris.abbott@company.com)
  ✓ Created new Snipe-IT user ID: 580
```

---

### Snipe-to-Jamf Module

**Purpose**: Synchronize user information from Snipe-IT asset assignments to Jamf Pro computer records.

**Workflow**:
1. Get all deployed assets from Snipe-IT that have an assigned user
2. For each asset, get the assigned user's details (name, email, department)
3. Find the corresponding computer in Jamf Pro by serial number
4. Update the Jamf computer's Location fields with the Snipe-IT user data

**Usage**:
```bash
python src/main.py snipe-to-jamf --dry-run
python src/main.py snipe-to-jamf
```

**Fields Synced**:
| Snipe-IT Field | Jamf Pro Location Field |
|----------------|------------------------|
| User Name | Real Name |
| Username/Email | Username |
| Email | Email Address |
| Job Title | Position |
| Department | Department |

---

### User Match Module

**Purpose**: Match computers in Jamf Pro to users in Snipe-IT and manage asset checkout/provisioning.

**Workflow**:
1. Get computers from specified Jamf Smart Group
2. For each computer, extract user info (username, email from location data)
3. Use fuzzy matching to find the corresponding Snipe-IT user
4. Check if asset exists in Snipe-IT (by serial number)
5. Create asset if missing, update if exists
6. Checkout asset to matched user (or reassign if configured)
7. Update Jamf Extension Attribute with Snipe-IT Asset ID

**Usage**:
```bash
python src/main.py user-match --dry-run
python src/main.py user-match
```

**Configuration**:
```yaml
user_match:
  smart_group_id: 123                   # Jamf Smart Group ID
  allow_reassignment: false             # Allow reassigning to different user
  fuzzy_match_threshold: 80             # Match confidence threshold

jamf:
  ea_snipe_asset_id: "Snipe-IT Asset ID"  # EA name for linking
```

**Matching Algorithm**:
The module uses multiple strategies to match users:
1. **Email exact match** - Highest confidence
2. **Username match** - Check both short and full usernames
3. **Fuzzy name match** - Compare full names with configurable threshold

---

### Model Sync Module

**Purpose**: Ensure hardware models exist in Snipe-IT before assets can be created.

**Workflow**:
1. Fetch all computer models from Jamf Pro
2. Fetch all models from Snipe-IT
3. Identify models missing in Snipe-IT
4. Create missing models with detected manufacturer and category

**Usage**:
```bash
# Check what models are missing (no changes)
python src/main.py model-sync --check-only

# Preview model creation
python src/main.py model-sync --dry-run

# Create missing models
python src/main.py model-sync
```

**Manufacturer Detection**:
The module automatically detects manufacturers from model names:
- Apple (MacBook, iMac, Mac Pro, etc.)
- Dell
- Lenovo (ThinkPad, ThinkCentre, etc.)
- HP
- Microsoft (Surface)
- And more...

---

### WakeUp Module

**Purpose**: Send MDM redeploy commands to devices that may be unresponsive or need management framework refresh.

**Workflow**:
1. Identify target computers (by group, serial, or file)
2. Send Jamf Pro `RedeployManagementFramework` command
3. Report success/failure for each device

**Usage**:
```bash
# Wake all devices in a Smart Group
python src/main.py wakeup --group 123 --dry-run
python src/main.py wakeup --group 123

# Wake single device by serial
python src/main.py wakeup --serial "C02XYZ123456"

# Wake devices from file (one serial per line)
python src/main.py wakeup --file serials.txt
```

---

### Reconciliation Module

**Purpose**: Compare inventory between Jamf Pro and Snipe-IT to identify discrepancies.

**Workflow**:
1. Fetch all devices from Jamf Pro
2. Fetch all assets from Snipe-IT
3. Compare by serial number
4. Identify:
   - Devices only in Jamf (not in Snipe-IT)
   - Assets only in Snipe-IT (not in Jamf)
   - Duplicate serial numbers in either system
   - Data mismatches (different hostnames, users, etc.)

**Usage**:
```bash
# Run reconciliation
python src/main.py reconcile

# Export to CSV
python src/main.py reconcile --export-csv --output-dir ./reports

# Skip specific checks
python src/main.py reconcile --no-duplicates
python src/main.py reconcile --no-mismatches
```

**Output**:
```
🔍 Inventory Reconciliation Results
═══════════════════════════════════════════
Jamf Pro devices:    1,234
Snipe-IT assets:     1,198

Only in Jamf:           45 devices
Only in Snipe-IT:        9 assets
Matched:             1,189 devices

Jamf duplicates:         2 serials
Snipe-IT duplicates:     1 serial
═══════════════════════════════════════════
```

---

## Dry Run Mode

**All modules support `--dry-run` mode.** This is crucial for safely testing changes before applying them.

### What Dry Run Does

| Action | Normal Mode | Dry Run Mode |
|--------|-------------|--------------|
| Read data | ✅ Yes | ✅ Yes |
| Log actions | ✅ Yes | ✅ Yes (with `[DRY-RUN]` prefix) |
| Create assets | ✅ Yes | ❌ No |
| Update records | ✅ Yes | ❌ No |
| Checkout assets | ✅ Yes | ❌ No |
| Send MDM commands | ✅ Yes | ❌ No |

### Dry Run Output Example

```
2026-01-02 10:15:32 - INFO - Starting Leavers module: dry_run=True
2026-01-02 10:15:33 - INFO - Found 5 disabled users in Azure AD
2026-01-02 10:15:34 - INFO - [DRY-RUN] Would mark asset MacBook-001 as pending
2026-01-02 10:15:34 - INFO - [DRY-RUN] Would mark asset MacBook-002 as pending
2026-01-02 10:15:35 - INFO - [DRY-RUN] Would rename user 123: John Doe → [Disabled] John Doe

============================================================
LEAVERS MODULE - DRY RUN COMPLETE
============================================================
Users processed:      5
Assets updated:       2
User names updated:   1
Errors:               0
============================================================
```

### Best Practices

1. **Always run with `--dry-run` first** to preview changes
2. **Review the output** carefully before running without dry-run
3. **Use verbose mode** (`-v`) for detailed debugging
4. **Test with a small subset** if possible (e.g., single serial with wakeup)

---

## Scheduling

### Built-in Scheduler

The application includes an APScheduler-based scheduler for automated execution:

```bash
# Start the scheduler
python src/scheduler.py

# Run once and exit (useful for cron jobs)
python src/scheduler.py --run-once
```

### Schedule Configuration

Configure schedules using cron expressions in `config.yaml`:

```yaml
scheduler:
  jobs:
    leavers:
      enabled: true
      cron: "0 6 * * *"         # Every day at 6:00 AM
    snipe_to_jamf:
      enabled: true
      cron: "0 7 * * *"         # Every day at 7:00 AM
    user_match:
      enabled: true
      cron: "0 8 * * *"         # Every day at 8:00 AM
    model_sync:
      enabled: true
      cron: "0 0 * * 0"         # Every Sunday at midnight
```

### Cron Expression Reference

```
┌───────────── minute (0 - 59)
│ ┌───────────── hour (0 - 23)
│ │ ┌───────────── day of month (1 - 31)
│ │ │ ┌───────────── month (1 - 12)
│ │ │ │ ┌───────────── day of week (0 - 6) (Sunday = 0)
│ │ │ │ │
* * * * *
```

**Examples**:
- `0 6 * * *` - Daily at 6:00 AM
- `0 */4 * * *` - Every 4 hours
- `0 8 * * 1-5` - Weekdays at 8:00 AM
- `0 0 1 * *` - First day of every month at midnight

### Docker Scheduler

For production deployments, use the Docker scheduler:

```bash
# Start scheduler in detached mode
docker compose --profile scheduler up -d

# View logs
docker compose --profile scheduler logs -f

# Trigger immediate run
docker exec -it <container_id> sh -c "echo NOW"

# Stop scheduler
docker compose --profile scheduler down
```

---

## Logging and Auditing

### Log Levels

| Level | Description |
|-------|-------------|
| `DEBUG` | Detailed diagnostic information |
| `INFO` | General operational messages |
| `WARNING` | Warning messages for potential issues |
| `ERROR` | Error messages for failures |

### Log Output

Logs include:
- Timestamp
- Log level
- Module name
- Message

```
2026-01-02 10:15:32 - INFO - Starting Leavers module: dry_run=False
2026-01-02 10:15:33 - INFO - Found 5 disabled users in Azure AD
2026-01-02 10:15:34 - INFO - Processing user: john.doe@example.com
2026-01-02 10:15:35 - INFO - Updated asset MacBook-001 status to pending
```

### Audit CSV Files

The User Match module generates audit CSV files in the `output/` directory:

```
output/
├── user_match_20260102_081532.csv
├── user_match_20260102_121532.csv
└── user_match_20260103_081532.csv
```

**CSV Columns**:
- `timestamp` - When the action occurred
- `jamf_id` - Jamf computer ID
- `serial` - Device serial number
- `hostname` - Computer hostname
- `primary_username` - Username from Jamf
- `snipe_user_id` - Matched Snipe-IT user ID
- `snipe_user_email` - Matched user's email
- `asset_id` - Snipe-IT asset ID
- `action` - Action taken (create, update, checkout, reassign)
- `result` - Success or failure
- `notes` - Additional details

---

## Troubleshooting

### Common Issues

#### Authentication Errors

**Jamf Pro OAuth2 Error**:
```
Error: Failed to obtain Jamf access token
```
- Verify `client_id` and `client_secret` are correct
- Check API client has not expired
- Ensure API client has required privileges

**Snipe-IT API Error**:
```
Error: 401 Unauthorized
```
- Verify API token is valid and not expired
- Check token has full access permissions

**Azure AD Error**:
```
Error: AADSTS7000215: Invalid client secret
```
- Client secret may have expired
- Create a new secret in Azure portal

#### Rate Limiting

If you encounter rate limit errors:
1. Add delays between API calls in config
2. Reduce batch sizes
3. Schedule runs during off-peak hours

#### Missing Models

If User Match fails with "Model not found":
1. Run `model-sync --check-only` to identify missing models
2. Run `model-sync` to create them
3. Then run `user-match`

### Debug Mode

Enable verbose logging for detailed diagnostics:

```bash
python src/main.py leavers --dry-run --verbose
```

### Health Check

The Docker container includes a health check endpoint:

```bash
# Check container health
docker inspect --format='{{.State.Health.Status}}' <container_name>

# Manual health check
curl http://localhost:8080/health
```

---

## API Reference

### Core Clients

#### JamfClient

```python
from core import JamfClient

client = JamfClient(
    base_url="https://your-jamf.jamfcloud.com",
    client_id="your-id",
    client_secret="your-secret"
)

# Get computer by serial
computer = client.get_computer_by_serial("C02XYZ123456")

# Update location
client.update_computer_location(
    computer_id=123,
    username="jdoe",
    realname="John Doe",
    email="jdoe@example.com"
)
```

#### SnipeITClient

```python
from core import SnipeITClient

client = SnipeITClient(
    base_url="https://your-snipeit.example.com",
    api_token="your-token"
)

# Get asset by serial
asset = client.get_asset_by_serial("C02XYZ123456")

# Create asset
new_asset = client.create_asset(
    name="MacBook Pro",
    serial="C02XYZ123456",
    model_id=5,
    status_id=2
)

# Checkout to user
client.checkout_asset(asset_id=123, user_id=456)
```

#### AzureClient

```python
from core import AzureClient

client = AzureClient(
    tenant_id="your-tenant",
    client_id="your-client",
    client_secret="your-secret"
)

# Get group members
members = client.get_group_members("group-guid")

# Check if user is disabled
is_disabled = client.is_user_disabled("user-guid")
```

---

## Development

### Project Structure

```
Jamf-SnipeIT-Suite/
├── config/
│   ├── config.yaml.example      # Template configuration
│   └── config.yaml              # Your configuration (gitignored)
├── src/
│   ├── core/
│   │   ├── __init__.py          # Core module exports
│   │   ├── config.py            # Configuration management
│   │   ├── jamf_client.py       # Jamf Pro API client
│   │   ├── snipe_client.py      # Snipe-IT API client
│   │   ├── azure_client.py      # Azure AD client
│   │   ├── health.py            # Health check server
│   │   └── async_clients.py     # Optional async clients
│   ├── modules/
│   │   ├── __init__.py          # Module exports
│   │   ├── leavers.py           # Leavers module
│   │   ├── snipe_to_jamf.py     # Snipe→Jamf sync
│   │   ├── user_match.py        # User matching
│   │   ├── model_sync.py        # Model sync
│   │   ├── wakeup.py            # Wake-up commands
│   │   └── reconciliation.py    # Inventory reconciliation
│   ├── utils/
│   │   └── __init__.py          # Shared utilities
│   ├── main.py                  # CLI entry point
│   ├── scheduler.py             # APScheduler entry point
│   └── docker_scheduler.py      # Docker scheduler entry point
├── logs/                        # Log files (gitignored)
├── output/                      # Audit CSVs (gitignored)
├── tests/                       # Unit tests
├── Dockerfile                   # Container definition
├── docker-compose.yml           # Docker Compose config
├── docker-entrypoint.sh         # Container entrypoint
├── requirements.txt             # Python dependencies
├── .gitignore                   # Git ignore rules
└── README.md                    # This file
```

### Setting Up Development Environment

```bash
# Clone repository
git clone https://github.com/CaputoDavide93/Jamf-SnipeIT-Suite.git
cd Jamf-SnipeIT-Suite

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install development dependencies
pip install pytest black flake8 mypy

# Run linting
flake8 src/
black --check src/
mypy src/

# Run tests
pytest tests/ -v
```

### Adding a New Module

1. Create module file in `src/modules/`:
   ```python
   # src/modules/my_module.py
   from typing import Dict, Any
   from core.config import Config
   
   class MyModule:
       def __init__(self, config: Config):
           self.config = config
       
       def run(self, dry_run: bool = False) -> Dict[str, Any]:
           results = {"processed": 0, "errors": 0}
           # Implementation
           return results
       
       def close(self):
           pass
   ```

2. Export from `src/modules/__init__.py`

3. Add CLI command in `src/main.py`

4. Add scheduler job if needed

---

## License

MIT License - See [LICENSE](LICENSE) file for details.

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests and linting
5. Submit a pull request

---

## Support

For issues and feature requests, please use the [GitHub Issues](https://github.com/CaputoDavide93/Jamf-SnipeIT-Suite/issues) page.
