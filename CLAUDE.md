# CLAUDE.md

This file provides guidance for AI assistants working with the sentry-infra-tools repository.

## Project Overview

sentry-infra-tools is an infrastructure management suite for Kubernetes deployments at Sentry. It provides the `sentry-kube` CLI and supporting tools for managing multi-region, multi-tenant Kubernetes clusters.

### Console Entry Points

- `sentry-kube` - Main CLI for cluster operations (`sentry_kube/cli:main`)
- `materialize-config` - Jsonnet/YAML config materialization (`config_builder/materialize_all:main`)
- `pr-docs` - PR documentation generator (`assistant/prdocs:main`)
- `pr-approver` - Automated PR approval (`pr_approver/approver:main`)

## Build & Development Commands

```bash
make develop                    # Full setup: deps + pre-commit + brew
make install-all-dependencies   # Install all Python dependencies
make install-dev-dependencies   # Install dev dependencies only
make tools-test                 # Run all tests (pytest -vv .)
make cli-typecheck              # Strict mypy on config_builder
```

### Running a Single Test

```bash
pytest -vv path/to/test_file.py                  # Run one test file
pytest -vv path/to/test_file.py::test_function   # Run one test function
```

### Linting & Formatting

Pre-commit hooks handle linting automatically. To run manually:

```bash
pre-commit run --all-files      # Run all hooks
pre-commit run ruff --all-files # Ruff linter + formatter only
pre-commit run mypy --all-files # mypy only
```

## Repository Structure

```
libsentrykube/       Core library - Kubernetes utilities, config management, rendering
  tests/             Tests for libsentrykube (co-located)
sentry_kube/         Main CLI application
  cli/               Click command modules (one file per command, auto-discovered)
  tests/             CLI tests
config_builder/      Jsonnet/YAML materialization and config generation
  merger/            YAML/JSON/Jsonnet merging logic
assistant/           PR documentation generation tool
pr_approver/         Automated PR approval tool
cli_config/          CLI configuration files
scripts/             Utility scripts (version bumping)
```

## Architecture & Key Patterns

### Plugin System

- CLI commands are auto-discovered via `pkgutil.walk_packages` in `sentry_kube/cli/__init__.py`
- Each command module in `sentry_kube/cli/` exports commands via `__all__`
- Jinja2/Jsonnet macros are registered as `libsentrykube.macros` entry points in `setup.py`

### Configuration Hierarchy

Configuration merges in order: global defaults -> group overrides -> region/customer overrides -> cluster-specific overrides. Override files live in `region_overrides/` directories within each service.

### Key Abstractions

- `CliContext` (frozen dataclass) - Immutable context passed through the Click command chain
- `Config` - Global configuration management (`libsentrykube/config.py`)
- `Cluster` - Cluster configuration and service discovery (`libsentrykube/cluster.py`)
- `MergeConfig` enum - Controls merge behavior: REJECT (default), OVERWRITE, APPEND
- Jinja2 `StrictUndefined` - Catches template variable typos at render time

### Templating

The rendering pipeline uses Jinja2 templates with Jsonnet support. Custom Jinja2 Extensions provide macros for generating Kubernetes manifests (sidecars, services, volumes, etc.).

## Code Style & Conventions

- **Python version**: 3.11+ (runtime), py38 target for Black formatting
- **Line length**: 90 characters (Black, isort, flake8, ruff)
- **Formatter**: Ruff (replaces Black), configured via pre-commit
- **Linter**: Ruff (replaces flake8), with `--fix` auto-corrections
- **Import sorting**: isort with Black profile
- **Type checking**: mypy with strict mode on `config_builder/` package
- **Frozen dataclasses**: Used for configuration objects to ensure immutability

### Naming Conventions

- `_values.yaml` - Service/chart value override files
- `_helm.yaml` - Helm chart configuration files
- `test_*.py` - Test modules
- `k8s_*_ops.py`, `regionsilo_*.py` - Auto-generated files (excluded from linting)

### Files Excluded from Pre-commit

Ruff linter and formatter exclude files matching `k8s_*_ops.py` and `regionsilo_*.py`.

## Testing

- **Framework**: pytest
- **Test location**: Tests are co-located with source code (`libsentrykube/tests/`, `config_builder/test_*.py`, `pr_approver/test_*.py`, `sentry_kube/tests/`)
- **Fixtures**: Defined in `libsentrykube/tests/conftest.py` - use `tempfile.TemporaryDirectory` for isolation
- **Autouse fixture**: `set_workspaceroot()` automatically sets workspace root for all libsentrykube tests
- **CI requires**: kube-linter (Go binary) for integration tests

## CI/CD

- **Tests**: GitHub Actions runs `make tools-test` on push to main and PRs (Python 3.11, Go 1.22 for kube-linter)
- **Type checking**: GitHub Actions runs `make cli-typecheck` on PRs
- **Docker builds**: Google Cloud Build (`cloudbuild.yaml`) builds images with multiple kubectl versions
- **Releases**: Manual trigger via GitHub Actions `Release` workflow using Craft

## Environment Variables

- `SENTRY_KUBE_CONFIG_FILE` - Path to configuration YAML (default: `[workspace_root]/cli_config/configuration.yaml`)
- `SENTRY_KUBE_ROOT` - Workspace root override (default: git root)
- `SENTRY_KUBE_KUBECTL_DIFF_CONCURRENCY` - Parallel kubectl diff workers
- `SENTRY_KUBE_KUBECTL_VERSION` - kubectl version to use
- `SENTRY_KUBE_NO_CONTEXT` - Skip kube context validation
- `KUBERNETES_OFFLINE` - Set automatically for offline commands (render, lint, validate)

## Dependencies

Key runtime dependencies: Click (CLI), kubernetes (K8s client), Jinja2 (templates), PyYAML, sentry_jsonnet (Jsonnet), google-cloud-secret-manager, httpx, paramiko (SSH), GitPython.

Key dev dependencies: ruff, mypy, pytest, pre-commit.
