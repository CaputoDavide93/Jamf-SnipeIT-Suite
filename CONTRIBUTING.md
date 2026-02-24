# 🤝 Contributing to Jamf-SnipeIT Suite

First off, thank you for considering contributing! 🎉

## 📋 Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [How to Contribute](#how-to-contribute)
- [Pull Request Process](#pull-request-process)
- [Style Guidelines](#style-guidelines)
- [Security](#security)

## 📜 Code of Conduct

This project adheres to a Code of Conduct. By participating, you are expected to uphold this code:

- Be respectful and inclusive
- Welcome newcomers and help them get started
- Focus on what is best for the community
- Show empathy towards other community members

## 🚀 Getting Started

### Prerequisites

- Python 3.11+
- Docker & Docker Compose (optional, for containerized development)
- Access to test instances of Jamf Pro, Snipe-IT, and Azure AD (or mock them)

### Development Setup

1. **Fork and clone the repository**
   ```bash
   git clone https://github.com/YOUR_USERNAME/Jamf-SnipeIT-Suite.git
   cd Jamf-SnipeIT-Suite
   ```

2. **Create a virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   pip install pytest pytest-cov black flake8 mypy  # Dev dependencies
   ```

4. **Set up configuration**
   ```bash
   cp config/config.yaml.example config/config.yaml
   # Edit with your test credentials
   ```

5. **Run tests**
   ```bash
   pytest tests/ -v
   ```

## 💡 How to Contribute

### 🐛 Reporting Bugs

Before creating a bug report:
- Check existing issues to avoid duplicates
- Collect relevant information (logs, config, steps to reproduce)

Include in your bug report:
- Clear, descriptive title
- Steps to reproduce
- Expected vs actual behavior
- Environment details (OS, Python version, Docker version)
- Relevant logs (sanitize any credentials!)

### 💡 Suggesting Features

Feature requests are welcome! Please include:
- Clear description of the feature
- Use case / problem it solves
- Potential implementation approach (optional)

### 🔧 Code Contributions

1. **Pick an issue** or create one for discussion
2. **Comment** that you're working on it
3. **Fork** and create a feature branch
4. **Implement** your changes
5. **Test** thoroughly
6. **Submit** a pull request

## 📝 Pull Request Process

### Before Submitting

- [ ] Code follows project style guidelines
- [ ] All tests pass (`pytest tests/ -v`)
- [ ] Linting passes (`flake8 src/`)
- [ ] No secrets or credentials in code
- [ ] Documentation updated if needed
- [ ] Commit messages are clear and descriptive

### PR Template

```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
How was this tested?

## Checklist
- [ ] Tests added/updated
- [ ] Documentation updated
- [ ] No secrets in code
```

### Review Process

1. Maintainer reviews the PR
2. Feedback addressed
3. Tests pass in CI
4. PR merged

## 🎨 Style Guidelines

### Python

- Follow PEP 8
- Use type hints where practical
- Maximum line length: 100 characters
- Use meaningful variable names

```python
# Good
def get_asset_by_serial(serial: str) -> Optional[Dict[str, Any]]:
    """Fetch an asset from Snipe-IT by serial number."""
    ...

# Avoid
def get(s):
    ...
```

### Formatting

Use `black` for consistent formatting:
```bash
black src/ --line-length 100
```

### Linting

Use `flake8` to check for issues:
```bash
flake8 src/ --max-line-length 100
```

### Commits

- Use clear, descriptive commit messages
- Start with a verb: "Add", "Fix", "Update", "Remove"
- Reference issues when applicable: "Fix #123: Handle API timeout"

```
Good:  Add dry-run mode to WakeUp module
Good:  Fix rate limiting in Snipe-IT client
Avoid: fixed stuff
Avoid: updates
```

### Documentation

- Update README.md for user-facing changes
- Add docstrings to functions and classes
- Include usage examples for new features

## 🔒 Security

**CRITICAL:** Never commit:
- API tokens or credentials
- Real URLs to production systems
- Personal or company-specific information
- Secrets in any form

See [SECURITY.md](SECURITY.md) for full security guidelines.

## 📁 Project Structure

```
Jamf-SnipeIT-Suite/
├── src/
│   ├── core/           # API clients and config
│   ├── modules/        # Feature modules
│   ├── utils/          # Shared utilities
│   └── main.py         # CLI entry point
├── config/
│   └── config.yaml.example
├── tests/              # Unit tests
├── docs/               # Additional documentation
└── README.md
```

## 🏷️ Issue Labels

| Label | Description |
|-------|-------------|
| `bug` | Something isn't working |
| `enhancement` | New feature request |
| `documentation` | Documentation improvements |
| `good first issue` | Good for newcomers |
| `help wanted` | Extra attention needed |
| `security` | Security-related |

## 📞 Getting Help

- Open an issue for questions
- Check existing documentation
- Email: CaputoDav@gmail.com

---

**Thank you for contributing!** 🙏
