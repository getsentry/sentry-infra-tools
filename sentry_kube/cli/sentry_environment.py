from __future__ import annotations

import os
import socket
from typing import Mapping, Optional


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
