# Agent Instructions: Jira Translator

This repository contains a service that connects to Jira via its API and automatically monitors specified projects to translate incoming comments, descriptions, and custom fields. It primarily uses local LLM generation (e.g., Ollama) to translate text between English and Chinese, with fallbacks to legacy servers or cloud APIs (Gemini).

## Testing
Test and utility scripts are located in the `utils/` directory:
- `utils/test_setup.sh`: Tests Jira authentication and LLM reachability.
- `utils/test_production.sh`: Runs the script interactively against a single Jira ticket ID, pausing for confirmation before applying changes.
- `utils/test_llm.py`: A utility script to test Ollama connectivity directly.

## Versioning Rules
**CRITICAL**: Upon making any changes or updates to the behavior of the translation script, you MUST bump the `__version__` variable located at the top of `jira_translator.py`.

Use standard Semantic Versioning (Major.Minor.Patch) criteria to determine the bump:
- **Major**: Incompatible API changes or massive architectural overhauls.
- **Minor**: Adding new features in a backward-compatible manner.
- **Patch**: Backward-compatible bug fixes or minor tweaks.

*(Note: Bumping the version ensures that the `TR4NSL4T3D-v...` prefix updates, allowing the bot to identify when a ticket was translated by an older, potentially buggy version of the script).*
