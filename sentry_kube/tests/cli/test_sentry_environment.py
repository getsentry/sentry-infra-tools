import pytest

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
    ],
)
def test_resolve_sentry_environment(hostname, environ, expected):
    assert resolve_sentry_environment(hostname=hostname, environ=environ) == expected
