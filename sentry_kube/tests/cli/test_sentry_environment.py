from unittest.mock import MagicMock, call, patch

import pytest

from sentry_kube.cli.sentry_environment import apply_runtime_sentry_tags
from sentry_kube.cli.sentry_environment import resolve_sentry_environment


@pytest.mark.parametrize(
    ("hostname", "environ", "expected"),
    [
        ("F6HFPVHVKR.local", {}, "F6HFPVHVKR.local"),
        ("mikes-mbp.lan", {}, "mikes-mbp.lan"),
        (
            "gocd-seer-k8s-agent-fdfde667-f203-4715-ab6e-9b166bf17a11",
            {},
            "gocd",
        ),
        (
            "gocd-agent-1",
            {"GO_PIPELINE_NAME": "deploy-edge-relay"},
            "gocd",
        ),
        (
            "some-random-host",
            {"GO_PIPELINE_NAME": "deploy-edge-relay"},
            "gocd",
        ),
        (
            "script-runner-region-86df8ff74d-4xb8x",
            {},
            "script-runner",
        ),
        (
            "script-runner-default-abc123",
            {},
            "script-runner",
        ),
        (
            "gocd-seer-k8s-agent-abc",
            {"SENTRY_KUBE_ENVIRONMENT": "custom"},
            "custom",
        ),
        (
            "script-runner-region-abc",
            {"SENTRY_ENVIRONMENT": "from-sentry-env"},
            "from-sentry-env",
        ),
        (
            "laptop.local",
            {
                "SENTRY_KUBE_ENVIRONMENT": "preferred",
                "SENTRY_ENVIRONMENT": "fallback",
            },
            "preferred",
        ),
        # Pipeline name must not inflate environment cardinality.
        (
            "gocd-agent-1",
            {
                "GO_PIPELINE_NAME": "deploy-edge-relay",
                "GO_STAGE_NAME": "deploy",
                "GO_JOB_NAME": "apply",
            },
            "gocd",
        ),
    ],
)
def test_resolve_sentry_environment(hostname, environ, expected):
    assert resolve_sentry_environment(hostname=hostname, environ=environ) == expected


def test_apply_runtime_sentry_tags_gocd():
    scope = MagicMock()
    environ = {
        "GO_PIPELINE_NAME": "deploy-edge-relay",
        "GO_PIPELINE_COUNTER": "42",
        "GO_STAGE_NAME": "deploy",
        "GO_JOB_NAME": "apply",
    }
    with patch(
        "sentry_kube.cli.sentry_environment.sentry_sdk.get_global_scope",
        return_value=scope,
    ):
        apply_runtime_sentry_tags(hostname="gocd-agent-1", environ=environ)

    assert scope.set_tag.call_args_list == [
        call("runtime.hostname", "gocd-agent-1"),
        call("gocd.pipeline", "deploy-edge-relay"),
        call("gocd.pipeline_counter", "42"),
        call("gocd.stage", "deploy"),
        call("gocd.job", "apply"),
    ]


def test_apply_runtime_sentry_tags_script_runner():
    scope = MagicMock()
    with patch(
        "sentry_kube.cli.sentry_environment.sentry_sdk.get_global_scope",
        return_value=scope,
    ):
        apply_runtime_sentry_tags(
            hostname="script-runner-region-86df8ff74d-4xb8x",
            environ={},
        )

    scope.set_tag.assert_called_once_with(
        "runtime.hostname", "script-runner-region-86df8ff74d-4xb8x"
    )


def test_apply_runtime_sentry_tags_local_no_extra_tags():
    scope = MagicMock()
    with patch(
        "sentry_kube.cli.sentry_environment.sentry_sdk.get_global_scope",
        return_value=scope,
    ):
        apply_runtime_sentry_tags(hostname="F6HFPVHVKR.local", environ={})

    scope.set_tag.assert_not_called()
