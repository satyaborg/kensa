# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in kensa, please report it responsibly.

**Do not open a public GitHub issue.**

Instead, email **satya.borg@gmail.com** with:

- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

You should receive an acknowledgment within 48 hours. We will work with you to understand the issue and coordinate a fix before any public disclosure.

## Scope

kensa runs agent code in subprocesses and executes LLM-generated SQL/commands in example agents. Security-relevant areas include:

- **Subprocess execution** (`runner.py`): command injection via scenario inputs
- **OTel span parsing** (`translate.py`, `exporter.py`): malformed trace data
- **YAML scenario loading**: we use `yaml.safe_load` exclusively
- **API key handling**: keys are read from environment variables, never logged or persisted

## Out of Scope

- Vulnerabilities in upstream dependencies (report to those projects directly)
- Security of user-written agent code evaluated by kensa
- Issues requiring physical access to the machine running kensa
