# 🔐 Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x.x   | :white_check_mark: |

## ⚠️ Important Security Notice

This application handles sensitive credentials for multiple enterprise systems:
- **Jamf Pro** API credentials
- **Snipe-IT** API tokens
- **Azure AD / Microsoft Entra ID** client secrets

### 🚨 Never Commit Secrets

The `config/config.yaml` file contains sensitive credentials and is **gitignored by default**. 

**NEVER:**
- Commit `config/config.yaml` to version control
- Share your configuration file publicly
- Store credentials in code or environment files that are tracked

**ALWAYS:**
- Use `config/config.yaml.example` as a template
- Keep credentials in the gitignored `config/config.yaml`
- Rotate credentials if you suspect they've been exposed
- Use environment variables in production when possible

## 🔒 Security Best Practices

### API Credentials

1. **Jamf Pro**: Use OAuth2 client credentials instead of username/password when possible
2. **Snipe-IT**: Create dedicated API tokens with appropriate scope
3. **Azure AD**: Use app registrations with minimal required permissions

### Minimal Permissions

Configure API credentials with the minimum required permissions:

#### Jamf Pro Permissions
- Read Computers
- Update Computers (Location only)
- Read Smart Computer Groups
- Send Remote Commands (for WakeUp module only)

#### Snipe-IT Permissions
- Full API access (create/read/update assets, users)

#### Azure AD Permissions
- `User.Read.All` (Application)
- `Group.Read.All` (Application)
- `GroupMember.Read.All` (Application)

### Docker Security

When running in Docker:
- Mount `config.yaml` as read-only: `-v ./config:/app/config:ro`
- Run as non-root user (default in Dockerfile)
- Don't expose unnecessary ports

## 🐛 Reporting a Vulnerability

If you discover a security vulnerability, please:

1. **DO NOT** open a public GitHub issue
2. Email the maintainer directly at: **CaputoDav@gmail.com**
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

### Response Timeline

- **Acknowledgment**: Within 48 hours
- **Initial Assessment**: Within 1 week
- **Fix Timeline**: Depends on severity
  - Critical: 24-48 hours
  - High: 1 week
  - Medium: 2-4 weeks
  - Low: Next release

## 🔍 Security Checklist for Contributors

Before submitting a PR, ensure:

- [ ] No credentials, tokens, or secrets in code
- [ ] No hardcoded URLs pointing to real instances
- [ ] No company-specific or personally identifiable information
- [ ] Example configurations use placeholder values
- [ ] Sensitive operations are logged without exposing credentials

## 📋 Credential Rotation

If you suspect credentials have been compromised:

### Jamf Pro
1. Go to Settings > System > API Roles and Clients
2. Revoke the compromised client
3. Create a new API client
4. Update `config.yaml`

### Snipe-IT
1. Go to Admin > User Management > Your User > API Keys
2. Delete the compromised token
3. Create a new API key
4. Update `config.yaml`

### Azure AD
1. Go to Azure Portal > App Registrations > Your App > Certificates & Secrets
2. Delete the compromised secret
3. Create a new client secret
4. Update `config.yaml`

---

**Maintained by:** Davide Caputo (CaputoDav@gmail.com)
