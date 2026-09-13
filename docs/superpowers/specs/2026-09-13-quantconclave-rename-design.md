# QuantConclave Full-Rename Design

## Goal

Rename the product and repository from CapitalRadar to QuantConclave across the
brand, Python package, CLI, environment variables, user-data directory, Docker
artifacts, documentation, and tests without losing existing user data.

## Repository identity

- Product name: `QuantConclave`
- GitHub repository: `guxuan1210/quant-conclave`
- Visibility: private
- Default branch: `main`
- Python distribution and import package: `quantconclave`
- CLI command: `quantconclave`
- New environment-variable prefix: `QUANTCONCLAVE_`
- New user-data directory: `~/.quantconclave`

The current Windows checkout directory is not renamed during the migration.
Changing the checkout path is an optional final operation after verification.

## Compatibility policy

One release retains compatibility with the old identity:

- `import capitalradar...` resolves through a thin compatibility package that
  re-exports the corresponding `quantconclave` modules and emits a deprecation
  warning.
- `CAPITALRADAR_*` environment variables remain readable when the corresponding
  `QUANTCONCLAVE_*` value is absent. New-prefixed variables always win.
- `~/.capitalradar` is treated as a read-only migration source. The application
  migrates its data to `~/.quantconclave` on first startup and never deletes the
  old directory automatically.
- Compatibility is removed in the next major release after the rename release.

Compatibility covers public imports, configuration, stored data, and the CLI.
It does not preserve CapitalRadar wording in user-facing branding.

## Data migration

Migration is non-destructive and restart-safe:

1. Detect the old directory only when the new migration marker is absent.
2. Copy files into a temporary directory under `~/.quantconclave`.
3. Merge the canonical SQLite workspace without overwriting unrelated rows;
   remap colliding IDs and update dependent references.
4. Verify file sizes, checksums, database integrity, row counts, and references.
5. Atomically promote the temporary data and write a versioned migration marker.
6. On failure, remove only the temporary destination and continue reading the
   old directory through the compatibility path.

The source directory and its databases are never renamed, modified, or deleted.

## Code and packaging migration

The business implementation moves from `capitalradar/` to `quantconclave/`
using Git-aware renames. The old package contains forwarding modules only.
Internal imports, the setuptools package list, console entry point, Docker
commands, module documentation, generated paths, tests, and examples all use
the new identity.

The rename must not change investment logic, HTTP routes, request/response
formats, database schemas, default ports, or analyst behavior. Existing HTTP
paths remain stable because they do not currently contain the product name.

## Repository safety

Before the first commit, ignore secrets and generated state, including `.env`,
logs, databases, caches, backups, local model data, exports, and evaluation run
artifacts. Track `.env.example` files and every Python `__init__.py` required to
install packages.

Create a baseline commit before the rename, then perform the rename on a feature
branch in small commits. Create the private GitHub repository and push only
after the renamed application passes verification.

GitHub CLI authentication must be renewed interactively before remote creation.

## Verification

- Install the renamed distribution in an isolated environment.
- Verify new and compatibility imports.
- Verify the new CLI and the one-release old CLI alias.
- Test environment-variable precedence and deprecation warnings.
- Test clean startup, legacy-directory migration, collision remapping, migration
  idempotence, integrity failure, and rollback.
- Run all non-integration tests and record unrelated baseline failures before the
  rename.
- Import the FastAPI application and compare its route set with the baseline.
- Start the dashboard and chart servers using the existing default ports.
- Scan tracked files for unintended secrets and remaining user-facing
  `CapitalRadar` branding.

## Acceptance criteria

- GitHub contains a private `guxuan1210/quant-conclave` repository on `main`.
- New installations use only `quantconclave`, `QUANTCONCLAVE_*`, and
  `~/.quantconclave`.
- Existing installations start successfully and migrate without data loss.
- Old imports, configuration, and CLI usage work for one release with explicit
  deprecation warnings.
- All expected tests pass; any pre-existing failures are documented from the
  baseline commit.
- No secrets, runtime databases, logs, backups, or generated evaluation outputs
  are tracked.
