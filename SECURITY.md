# Security policy

## Supported versions

PetroGIS is pre-1.0. Only the latest commit on `master` receives security fixes. A supported-versions
table will replace this note at 1.0 (see the Production stage in
[docs/product-plan.md](docs/product-plan.md)).

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's
[private vulnerability reporting](https://github.com/a1rzayev/PetroGIS/security/advisories/new)
for this repository. Don't open a public issue.

Include:
- the affected version or commit;
- the input that triggers the problem, such as a crafted project, LAS, CSV or GeoTIFF file;
- what happens, and what you expected to happen.

You'll get an acknowledgement within 7 days. Once a fix is released, the issue is published as a
GitHub security advisory, crediting you unless you'd rather not be named.

## Threat model

PetroGIS is a desktop application. It has no server component and it opens no network ports.
The main risk is **untrusted files**: projects, styles and data that people email to each other
or download.

| Untrusted input | Examples | What PetroGIS guarantees |
|---|---|---|
| Project files (`.pgis`) | Shared field-study projects | Treated as data only, never executed. Malformed or hostile structures are rejected with a message or skipped with a warning. The app does not crash on them. Source paths are resolved and checked, never interpreted. |
| Style files (Phase 2) | Shared symbology | Data only, never code. Values are type-checked. |
| Data files | Shapefile, GeoJSON, GeoPackage, KML, GeoTIFF, LAS, CSV, Excel | Readers fail with a user-facing error instead of crashing. Text from files (well names, file names, CRS names, band descriptions) is never interpreted as HTML or markup. |
| Network responses (Phase 2 basemaps and services) | Tiles, capabilities documents | Only fetched when the user asks. HTTPS with certificate verification, timeouts and response-size limits. |
| Plugins (after 1.0) | Third-party extensions | Only run after explicit user trust. Never loaded from project files. |

Out of scope: an attacker who can already run code as the user or modify the installed application.

## Design rules

These rules are enforced by tests and CI, not just stated:

1. **No code execution from files.** Projects and styles have no macros, expressions are
   evaluated by a whitelisted parser (never Python `eval`/`exec`), and plugins can't be embedded.
2. **No markup injection.** Data-derived text is escaped or shown in plain-text widgets.
   `tests/ui/test_text_safety.py` pushes a markup payload through every such path.
3. **Malformed input fails cleanly.** Readers raise `DataReadError` and nothing else.
   Property-based tests in `tests/io/test_malformed_inputs.py` feed random and structured
   garbage to the LAS, well-header and project readers.
4. **No secrets in files.** Credentials (Phase 2 services) live only in the operating system's
   keychain. Project files store an opaque ID.
5. **Crash safety.** Saves are atomic (temporary file, then rename), the previous project is kept as
   `<name>.pgis.bak`, and unsaved work is autosaved and offered for recovery after a crash.
6. **Supply chain.** Dependencies are locked with hashes in `uv.lock` and audited for known
   vulnerabilities in CI. CI actions are pinned to commit SHAs, use a read-only token and never run
   untrusted fork code with secrets.
7. **Static checks.** ruff's bandit (`S`) rules run on every change.

## Background

These rules come from problems QGIS has shipped: project-embedded Python macros, plain-text
database passwords in project files, unescaped attribute values in HTML output, and a CI workflow
vulnerability. The details are in [docs/phase2-plan.md](docs/phase2-plan.md#security-baseline).
