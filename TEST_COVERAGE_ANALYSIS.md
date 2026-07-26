# Test Coverage Analysis

**Date:** 2026-02-15
**Overall Coverage:** 58% (2937 of 7043 statements missed)
**Test Results:** 218 passed, 1 failed (test_lint — missing `kube-linter` binary in CI)

---

## Coverage by Package

| Package | Coverage | Notes |
|---------|----------|-------|
| libsentrykube (library) | ~60% | Core library, best-tested package |
| config_builder | ~75% | Well-tested materialization and validation |
| pr_approver | ~55% | Rules tested, GitHub integration untested |
| assistant | 100% | Small module, fully tested |
| sentry_kube (CLI) | ~30% | Largest gap — most CLI commands barely tested |

---

## Modules With Zero Test Coverage

These files have **0%** coverage and no corresponding tests at all:

| File | Lines | Purpose |
|------|-------|---------|
| `sentry_kube/ext.py` | 83 | Jinja2 extensions for K8s YAML generation (IAPService, PGBouncerSidecar, XDSConfigMapFrom) |
| `sentry_kube/render_services.py` | 88 | CLI to render affected K8s services based on file changes (includes multi-threaded path) |
| `sentry_kube/render_helm_services.py` | 51 | CLI to render affected Helm services based on file changes |
| `sentry_kube/validate_services.py` | 58 | CLI to validate rendered services via linting and conftest policies |
| `libsentrykube/ssh.py` | ~20 | SSH command construction for GCP IAP tunnels |

---

## Critically Under-Tested Modules (<30% Coverage)

| File | Coverage | Missed Lines | Purpose |
|------|----------|-------------|---------|
| `sentry_kube/cli/run_job.py` | 12% | 110/125 | Run K8s Job with log streaming and state monitoring |
| `sentry_kube/cli/scale.py` | 12% | 106/121 | Scale deployments with session-based rollback |
| `sentry_kube/cli/kubectl.py` | 20% | 39/49 | kubectl wrapper with dangerous-command detection |
| `sentry_kube/cli/run_pod.py` | 20% | 131/163 | Run one-off pods from deployment templates |
| `sentry_kube/cli/debug.py` | 22% | 52/67 | kubectl debug with volume mount inheritance |
| `libsentrykube/kube.py` | 24% | 273/358 | Core K8s resource rendering, diffing, and applying |
| `sentry_kube/cli/cluster.py` | 28% | 43/60 | Cluster context operations |
| `sentry_kube/cli/audit.py` | 29% | 90/126 | Audit local vs. cluster resources for drift |
| `pr_approver/gh.py` | 15% | 29/34 | GitHub API calls for PR approval/dismissal |

---

## Moderately Under-Tested Modules (30–55%)

| File | Coverage | Purpose |
|------|----------|---------|
| `sentry_kube/cli/apply.py` | 35% | Apply manifests with canary deployment and monitors |
| `sentry_kube/cli/detect_drift.py` | 35% | Detect drift between local and cluster state |
| `sentry_kube/cli/diff.py` | 36% | Show kubectl diff with filtering and coloring |
| `sentry_kube/cli/quickpatch.py` | 36% | Quick-patch operations |
| `sentry_kube/cli/validate.py` | 37% | Validate rendered manifests |
| `sentry_kube/cli/util.py` | 38% | CLI helper utilities |
| `libsentrykube/helm.py` | 39% | Helm chart rendering, diffing, apply, blue-green |
| `sentry_kube/cli/tunnel.py` | 40% | SSH tunnel management |
| `libsentrykube/utils.py` | 41% | Shared utilities (namespace extraction, kubectl, gcloud) |
| `sentry_kube/cli/helm.py` | 42% | Helm CLI subcommands |
| `libsentrykube/loader.py` | 48% | Macro extension loading via entry points |
| `pr_approver/approver.py` | 50% | PR approval orchestration and CLI entry point |
| `libsentrykube/service.py` | 53% | Service path management and value merging |

---

## Recommended Improvements (Prioritized)

### Priority 1: High-Impact Pure Logic Tests (Easy Wins)

These functions contain testable pure logic with no I/O, making them straightforward to unit test with high coverage gain.

**`libsentrykube/kube.py`** — currently 24% coverage

- `_get_nested_key()` — nested dict key extraction by dot-path
- `_match_filters()` — filter matching with `=` and `!=` operators
- `_sort_important_files_first()` — sorting priority (ConfigMap/ServiceAccount first)
- `count_important_diffs()` — diff counting and categorization
- `camel_to_snake()` — regex CamelCase to snake_case conversion
- `KubeClient.is_api_class()` — static class name pattern check

**`libsentrykube/utils.py`** — currently 41% coverage

- `kube_extract_namespace()` — splits "namespace/name" strings
- `kube_convert_kind_to_func()` — converts CamelCase K8s kind to API function name
- `chunked()` — chunks an iterable into fixed-size groups
- `deep_copy_without_refs()` — recursive copy breaking YAML anchors

**`libsentrykube/helm.py`** — currently 39% coverage

- `HelmChart.is_local` / `is_oci` — chart source detection properties
- `HelmChart.cmd_target()` / `local_path()` — command/path construction
- `HelmReleaseStrategy.from_spec()` — parse strategy spec strings/dicts
- `_get_path()` — nested path extraction helper

**`libsentrykube/service.py`** — currently 53% coverage

- `MergeConfig.__init__()` — enum-based merge strategy initialization
- `merge_values_files_no_conflict()` — merge with REJECT/OVERWRITE/APPEND strategies
- `build_materialized_path()` / `build_helm_materialized_path()` — path construction

**`libsentrykube/ssh.py`** — currently 0% coverage

- `build_ssh_command()` — pure string construction, trivial to test with parameter variations

**`sentry_kube/cli/audit.py`** — currently 29% coverage

- `to_api_name()` — converts K8s API version string to Python client class name
- `get_service_selector()` — builds label selector string

**`sentry_kube/cli/diff.py`** — currently 36% coverage

- `should_skip_line()` — diff line filtering logic
- `print_diff_string()` — diff line coloring logic

**Estimated effort:** Low — these are all pure functions with clear inputs/outputs. Adding ~40 test cases here would meaningfully boost overall coverage.

---

### Priority 2: Mocked Tests for Core Infrastructure Operations

These are the most critical code paths in the project. They require mocking external dependencies (Kubernetes API, subprocess, filesystem) but cover high-risk operations.

**`libsentrykube/kube.py`** — K8s resource operations

- `collect_kube_resources()` — resource deserialization from rendered YAML
- `collect_diffs()` — diffing local vs. remote resources
- `apply()` — create/patch resources via K8s API
- `render_templates()` — Jinja2 rendering with service paths, skip_kinds, and filters
- `__filter_metrics()` — HPA metric deduplication

**`libsentrykube/helm.py`** — Helm operations

- `_run_helm()` — subprocess helm command execution
- `render_values()` / `materialize_values()` — Jinja2 value rendering + file writing
- `render()`, `diff()`, `apply()`, `rollback()` — helm lifecycle commands
- Blue-green deployment: `get_remote_bg_active()`, `set_bg_active()`

**`pr_approver/gh.py`** — GitHub API

- `accept_pr()` — HTTP POST to approve a PR
- `dismiss_acceptance()` — HTTP GET + PUT for review dismissal
- Error handling for 401, 404, 500 responses

**Estimated effort:** Medium — requires mock setup for Kubernetes client, subprocess, and HTTP calls.

---

### Priority 3: CLI Command Integration Tests

The `sentry_kube/cli/` package has 20+ commands with most at 12–45% coverage. The existing tests for `get_regions` and `secrets` use Click's `CliRunner`, which is the right pattern.

**Highest-value CLI commands to test (by risk and usage):**

| Command | Coverage | Why Test It |
|---------|----------|-------------|
| `apply.py` | 35% | Deploys to production clusters — canary logic, monitor checks, soak time |
| `diff.py` | 36% | Pre-deployment verification — YAML filtering, concurrent kubectl diffs |
| `scale.py` | 12% | Scaling with rollback — session file management, HPA patching |
| `run_job.py` | 12% | Job execution — pod state machine, log streaming, cleanup |
| `kubectl.py` | 20% | Safety wrapper — dangerous command detection, context injection |
| `audit.py` | 29% | Drift detection — resource comparison, namespace scanning |

**Recommended approach:**
- Use `click.testing.CliRunner` to invoke commands
- Mock `libsentrykube.*` functions at the import boundary
- Focus on argument validation, error handling, and decision logic rather than end-to-end execution

---

### Priority 4: Zero-Coverage Module Tests

**`sentry_kube/ext.py`** (0%, 83 lines)

Contains Jinja2 extensions generating K8s YAML for IAP, PGBouncer, and XDS configs. These produce structured output that can be snapshot-tested:
- `IAPService` — generates Service + BackendConfig + ManagedCertificate YAML
- `PGBouncerSidecar` — generates container spec JSON with pgbouncer.ini
- `XDSConfigMapFrom` — generates ConfigMap from merged Envoy XDS files

**`sentry_kube/render_services.py`** and **`render_helm_services.py`** (0%)

CLI commands for CI rendering pipelines. Testing the file-to-resource mapping logic and rendering dispatch would catch regressions in the build pipeline.

**`sentry_kube/validate_services.py`** (0%, 58 lines)

Validation pipeline combining linting + conftest. Testing the region filtering and error accumulation logic is worthwhile.

---

### Priority 5: Edge Cases in Existing Tests

Some well-tested modules have specific gaps worth filling:

- **`libsentrykube/tests/test_service.py`** (98% on test file, but `service.py` is 53%) — the test file itself runs well, but doesn't exercise all service.py paths. Add tests for `get_service_ctx_overrides()`, `get_hierarchical_value_overrides()`, and `write_managed_values_overrides()`.
- **`libsentrykube/tests/test_kube.py`** — covers normalization and consolidation but not rendering or API operations. Add tests for `render_templates()` with various skip_kinds/filter combinations.
- **`libsentrykube/loader.py`** (48%) — add tests for `load_macros()` caching behavior and empty entry points.

---

## Summary

| Priority | Area | Est. New Tests | Coverage Impact |
|----------|------|---------------|-----------------|
| P1 | Pure logic unit tests | ~40 tests | +8-10% overall |
| P2 | Mocked core operations | ~30 tests | +10-12% overall |
| P3 | CLI integration tests | ~25 tests | +8-10% overall |
| P4 | Zero-coverage modules | ~15 tests | +3-4% overall |
| P5 | Edge cases in existing | ~10 tests | +2-3% overall |
| **Total** | | **~120 tests** | **58% → ~85%** |

The biggest return on investment is **Priority 1** (pure logic tests) — they require no mocking infrastructure, are fast to write, and cover utility functions used throughout the codebase. **Priority 2** (mocked core operations) carries the highest risk reduction, as `kube.py` and `helm.py` contain the deployment logic that affects production clusters.
