from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from kubernetes.client.rest import ApiException

from sentry_kube.cli.argocd_sync import argocd_app_name, argocd_sync, sync_patch

HUB = "gke_sentry-eu-west3_europe-west3_primary"


def cluster(name, **services_data):
    return SimpleNamespace(name=name, services_data=services_data)


def app(name, automated=None):
    sync_policy = {} if automated is None else {"automated": automated}
    return {"metadata": {"name": name}, "spec": {"syncPolicy": sync_policy}}


@pytest.mark.parametrize(
    "c, expected",
    [
        (cluster("default", context=HUB), "relay"),
        (cluster("seer-sandbox", context="gke_p_r_seer-sandbox"), "seer-sandbox-relay"),
        (
            cluster("pop-au", context="gke_p_r_x", cluster_name="pop-au-1"),
            "pop-au-1-relay",
        ),
        (cluster("local", context="colima"), "local-relay"),
    ],
)
def test_argocd_app_name(c, expected):
    assert argocd_app_name("relay", c, HUB) == expected


@pytest.mark.parametrize(
    "automated, autosync, self_heal, expected",
    [
        (None, True, None, {"enabled": True}),
        ({"prune": True, "selfHeal": True}, False, None, {"enabled": False}),
        (None, None, True, {"enabled": False, "selfHeal": True}),
        ({}, None, False, {"enabled": True, "selfHeal": False}),
        ({"enabled": False}, True, True, {"enabled": True, "selfHeal": True}),
    ],
)
def test_sync_patch(automated, autosync, self_heal, expected):
    assert sync_patch(automated, autosync, self_heal) == {
        "spec": {"syncPolicy": {"automated": expected}}
    }


@pytest.fixture
def api():
    with (
        patch("sentry_kube.cli.argocd_sync.kube_get_client"),
        patch("sentry_kube.cli.argocd_sync.client.CustomObjectsApi") as mock,
    ):
        yield mock.return_value


def invoke(*args):
    obj = SimpleNamespace(cluster=cluster("default", context=HUB), context_name=HUB)
    return CliRunner().invoke(argocd_sync, ["relay", *args], obj=obj)


def test_status_does_not_patch(api):
    api.get_namespaced_custom_object.return_value = app(
        "relay", {"prune": True, "selfHeal": True}
    )

    result = invoke()

    assert result.exit_code == 0
    assert result.output == "relay: autosync=on self-heal=on\n"
    api.patch_namespaced_custom_object.assert_not_called()


def test_toggle_patches_and_prints_result(api):
    api.get_namespaced_custom_object.return_value = app("relay", {"prune": True})
    api.patch_namespaced_custom_object.return_value = app(
        "relay", {"enabled": False, "prune": True}
    )

    result = invoke("--autosync", "off")

    assert result.exit_code == 0
    assert result.output == "relay: autosync=off self-heal=off\n"
    assert api.patch_namespaced_custom_object.call_args.kwargs["body"] == {
        "spec": {"syncPolicy": {"automated": {"enabled": False}}}
    }


def test_missing_app(api):
    api.get_namespaced_custom_object.side_effect = ApiException(status=404)

    result = invoke()

    assert result.exit_code != 0
    assert "ArgoCD Application 'relay' not found." in result.output


def test_rejects_invalid_toggle(api):
    result = invoke("--self-heal", "maybe")

    assert result.exit_code == 2
    api.get_namespaced_custom_object.assert_not_called()
