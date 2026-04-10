# Contributing to Liferay Docker Manager (LDM)

First off, thank you for considering contributing to LDM! It's people like you that make LDM such a great tool for the Liferay community.

> [!IMPORTANT]
> LDM is **not** an official Liferay product. It is a community-driven project maintained by Peter Richards. While we strive to align with Liferay's engineering values, please do not contact Liferay Support for issues related to this tool.

## 🚀 How Can I Contribute?

### Reporting Bugs

Before creating bug reports, please check our [Issue Tracker](https://github.com/peterrichards-lr/liferay-docker-manager/issues) to see if the problem has already been reported.

### Suggesting Enhancements

We love new ideas! Please use the Feature Request template when opening a new issue.

### Pull Requests

1. **Fork the repo** and create your branch from `master`.
2. If you are working on a stability or hardening task, please align with our **Hardening Workflow**.
3. **Pass the Lint Check**: Before submitting, you MUST run the local linting script:

   ```bash
   ./lint.sh
   ```

   This script runs:
   - **Ruff** (Linting & Formatting)
   - **Bandit** (Security Scanning)
   - **Pytest** (Unit Tests)
   - **Markdownlint** (Docs)
4. **Update Documentation**: If you change or add a command, please update the relevant files in the `docs/` folder.

## 🤖 AI-Assisted Contributions

We encourage the use of AI to help improve LDM.

- **Preferred Tool**: [Gemini](https://gemini.google.com/) is the preferred AI assistant for this project.
- **Guidance**: You are free to use other AI tools (like Copilot or ChatGPT), but you **MUST** ensure that your tool adheres to the architectural and engineering standards defined in [`.gemini/gemini.md`](.gemini/gemini.md).
- **Verification**: AI-generated code must still pass all local linting and unit tests (`./lint.sh`) before being submitted.

## 🛠️ Development Environment

- **Python**: 3.12+ recommended.
- **Docker**: Required for runtime testing.
- **Pre-commit**: We use pre-commit hooks to ensure code quality.

## 📜 Commit Message Conventions

We prefer [Conventional Commits](https://www.conventionalcommits.org/):

- `feat(stack): ...` for new features.
- `fix(infra): ...` for bug fixes.
- `docs: ...` for documentation changes.
- `test: ...` for adding tests.

## ⚖️ License

By contributing, you agree that your contributions will be licensed under its **MIT License**.
