#!/usr/bin/env python3

# Based off https://github.com/getsentry/devinfra-deployment-service/blob/main/gocd_agent/scripts/checks/datadog/monitor_status.py

from __future__ import annotations

import json
import urllib.error
import urllib.request
import os
from collections.abc import Sequence
from libsentrykube.events import DATADOG_API_KEY


DD_API_BASE = "https://api.datadoghq.com/api/v1"
DD_APP_BASE = "https://app.datadoghq.com"


class MissingOverallStateException(Exception):
    def __init__(self, message):
        super().__init__(message)


class MissingDataDogAppKeyException(Exception):
    def __init__(self, message):
        super().__init__(message)


class MissingDataDogApiKeyException(Exception):
    def __init__(self, message):
        super().__init__(message)


def check_monitors(
    monitor_ids: Sequence[int],
    dd_app_key: str | None = None,
    failure_states: Sequence[str] | None = None,
) -> bool:
    if failure_states is not None:
        failure_states = ["Alert", "Warn"]

    for mid in monitor_ids:
        if not check_monitor(mid, dd_app_key, failure_states):
            return False

    return True


def check_monitor(
    monitor_id: int,
    dd_app_key: str | None = None,
    failure_states: Sequence[str] | None = None,
) -> bool:
    if dd_app_key is None:
        dd_app_key = os.getenv("DATADOG_APP_KEY") or os.getenv("DD_APP_KEY")

    if dd_app_key is None:
        raise MissingDataDogAppKeyException(
            "DATADOG_APP_KEY must be set to check monitors."
        )

    if DATADOG_API_KEY is None:
        raise MissingDataDogApiKeyException(
            "DATADOG_API_KEY must be set to check monitors."
        )

    if failure_states is None:
        failure_states = ["Alert", "Warn"]

    failure_states = [s.lower() for s in failure_states]  # type: ignore[union-attr]

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "DD-API-KEY": DATADOG_API_KEY,
        "DD-APPLICATION-KEY": dd_app_key,
    }

    req = urllib.request.Request(f"{DD_API_BASE}/monitor/{monitor_id}", headers=headers)
    resp = urllib.request.urlopen(req)

    resp_json = json.load(resp)

    if "overall_state" in resp_json:
        overall_state = resp_json["overall_state"]
    else:
        raise MissingOverallStateException(
            "'overall_state' key missing from the DataDog response."
        )

    return overall_state.lower() not in failure_states
