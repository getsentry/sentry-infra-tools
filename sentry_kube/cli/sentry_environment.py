from __future__ import annotations

import os
import socket
import sys
from typing import Mapping, Optional

import sentry_sdk

_FALSY_ENV_VALUES = {"", "0", "false", "no", "off"}

# GoCD injects these on agent jobs. Pipeline is the useful primary identifier.
_GOCD_TAG_ENV_VARS = (
    ("gocd.pipeline", "GO_PIPELINE_NAME"),
    ("gocd.pipeline_counter", "GO_PIPELINE_COUNTER"),
    ("gocd.stage", "GO_STAGE_NAME"),
    ("gocd.job", "GO_JOB_NAME"),
)


def _env_flag(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "").strip().lower() not in _FALSY_ENV_VALUES


def should_enable_sentry(environ: Optional[Mapping[str, str]] = None) -> bool:
    """
    Decide whether sentry-kube should report to Sentry at all.

    Reporting is off in CI (`CI` is set by GitHub Actions and most other CI
    providers), under pytest, and when `SENTRY_KUBE_NO_SENTRY` is set. Errors
    from test runs are noise, not production failures. GoCD does not set `CI`
    by default, so deploy pipelines keep reporting.
    """
    env = os.environ if environ is None else environ
    if _env_flag(env, "SENTRY_KUBE_NO_SENTRY") or _env_flag(env, "CI"):
        return False
    return "pytest" not in sys.modules


def resolve_sentry_environment(
    hostname: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> str:
    """
    Choose a Sentry environment for sentry-kube.

    Local runs keep the machine hostname so developers can map errors back to
    their laptop (e.g. F6HFPVHVKR.local).

    Ephemeral CI/runtime hosts collapse to stable names so they do not create
    unbounded Sentry environments:
    - gocd (GoCD agents / jobs)
    - script-runner (script-runner pods)

    GoCD pipeline/stage/job identity belongs on tags (see
    apply_runtime_sentry_tags), not in the environment name.
    """
    env = os.environ if environ is None else environ
    host = socket.gethostname() if hostname is None else hostname

    explicit = env.get("SENTRY_KUBE_ENVIRONMENT") or env.get("SENTRY_ENVIRONMENT")
    if explicit:
        return explicit

    # GoCD injects GO_* vars on agents; k8s agent pod names also start with gocd-.
    if env.get("GO_PIPELINE_NAME") or host.startswith("gocd-"):
        return "gocd"

    # script-runner workloads use pod names like script-runner-region-....
    if host.startswith("script-runner"):
        return "script-runner"

    return host


def apply_runtime_sentry_tags(
    hostname: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> None:
    """
    Attach stable runtime identity tags for filtering in Sentry.

    Prefer tags over environment cardinality for GoCD pipeline details. Dozens
    of pipelines are fine as tags; stuffing them into environment still makes
    the environments UI noisier than needed.
    """
    env = os.environ if environ is None else environ
    host = socket.gethostname() if hostname is None else hostname
    scope = sentry_sdk.get_global_scope()

    # Always keep hostname available when we collapsed the environment.
    environment = resolve_sentry_environment(hostname=host, environ=env)
    if environment in {"gocd", "script-runner"}:
        scope.set_tag("runtime.hostname", host)

    for tag_name, env_var in _GOCD_TAG_ENV_VARS:
        value = env.get(env_var)
        if value:
            scope.set_tag(tag_name, value)
