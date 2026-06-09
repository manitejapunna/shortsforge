# Security Policy

## Threat Model

ShortsForge processes untrusted media files and user-provided text through AI models, publishes to external APIs, and runs locally with access to the filesystem. The primary threats are:

1. **Prompt injection** via transcript content or knowledge base documents
2. **Path traversal** via filenames in media metadata or API responses
3. **SSRF** via URL-based knowledge base ingestion
4. **Credential exposure** via log leakage or over-privileged token storage
5. **Unintended publishing** via automated tool chaining without user consent

## Mitigations by Category

### SI — Input Sanitization
- `security/prompt_guard.py`: All transcript text and KB content wrapped in XML delimiters with guard preambles before LLM submission
- Known jailbreak patterns (ignore previous, system:, im_start, etc.) are detected and neutralized

### SA — Access Control
- `security/paths.py`: All file paths validated against allow-lists; symlinks, UNC paths, and traversal rejected
- YouTube credentials encrypted with Fernet; key stored in OS keyring (not env)
- Token file chmod 600

### SP — Privacy
- No customer data processed; all examples use demo/CC0 media only
- Audit log stores clip SHA256 (not content), title prefix only
- structlog redaction processor removes known secret patterns from logs

### SV — Validation
- All MCP tool arguments validated with Pydantic v2 strict models
- Enum literals used for mode/preset/visibility parameters (no free-form)
- clip_id validated against ULID regex before use in file operations

### SO — Operations
- `security/rate_limit.py`: Token bucket for LLM calls (60/min) and YouTube uploads (6/hr, 20/day)
- `security/disk.py`: Output directory capped at 5 GB; oldest files rotated
- Append-only audit log for all publish attempts (including refused ones)

### SS — Supply Chain
- Dependencies pinned in `pyproject.toml`
- CI runs `pip-audit` (fail on High/Critical CVEs)
- SBOM generated via `cyclonedx-py` on every release

### SL — Logging
- All logging to stderr via structlog; never stdout
- Secret patterns redacted before any log output
- Moderated content is never logged (only flagged/pass result)

### SC — Content Safety
- `security/moderation.py`: OpenAI omni-moderation on all text and generated images
- Fail-closed: network errors after 2 retries result in content rejection
- B-roll image generation re-tries with safety-prepended prompt on moderation failure

## Responsible Disclosure

To report a vulnerability, contact: **placeholder@example.com** (replace before submission).

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested mitigation (if known)

We aim to respond within 48 hours and remediate within 14 days.

## Release Checklist

- [ ] `detect-secrets scan --baseline .secrets.baseline` passes
- [ ] `pip-audit` shows no High/Critical CVEs
- [ ] `bandit -r src/` shows no High severity findings
- [ ] All model IDs in `providers/*.py` are pinned (no "latest")
- [ ] SBOM artifact generated and attached to release
- [ ] `.env` is in `.gitignore` and not in commit history
