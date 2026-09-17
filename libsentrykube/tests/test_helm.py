import os
import shutil
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest
from yaml import safe_dump, safe_load

from libsentrykube.cluster import load_cluster_configuration
from libsentrykube.context import init_cluster_context
from libsentrykube.helm import (
    HelmChart,
    HelmException,
    HelmRelease,
    HelmReleaseStrategy,
    _split_release_manifests,
    fetch_chart,
    materialize_manifests,
)
from libsentrykube.utils import set_workspace_root_start, workspace_root

CLUSTER = {
    "id": "cluster1",
    "services": [],
    "helm": {"services": ["k8s/helm_services/my-helm-service"]},
}

CONFIGURATION = {
    "silo_regions": {
        "customer1": {
            "k8s": {
                "root": "k8s",
                "cluster_def_root": "clusters",
                "materialized_manifests": "materialized_manifests",
            },
        },
    },
}

HELM_CONFIG = {"chart": "chart"}

SERVICE_VALUES = {"replicas": 3, "nodepool": "relay-pop"}

# Jinja template merged by sentry-kube into the helm values of the release.
VALUES_TEMPLATE = """\
replicaCount: {{ values.replicas }}
nodeSelector:
  nodepool: {{ values.nodepool }}
"""

CHART_YAML = {"apiVersion": "v2", "name": "test-chart", "version": "0.1.0"}

CHART_VALUES = {"replicaCount": 1, "nodeSelector": {}}

DEPLOYMENT_TEMPLATE = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}
  namespace: {{ .Release.Namespace }}
spec:
  replicas: {{ .Values.replicaCount }}
  template:
    spec:
      nodeSelector:
        {{- toYaml .Values.nodeSelector | nindent 8 }}
"""

HELM_TEMPLATE_OUTPUT = """\
---
# Source: chart/templates/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-helm-service
spec:
  replicas: 3
---
# Source: chart/templates/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: my-helm-service
  namespace: other
"""


@pytest.fixture
def helm_workspace(tmp_path: Path) -> Iterator[Path]:
    service_dir = tmp_path / "k8s" / "helm_services" / "my-helm-service"
    os.makedirs(service_dir / "chart" / "templates")
    with open(service_dir / "_helm.yaml", "w") as f:
        f.write(safe_dump(HELM_CONFIG))
    with open(service_dir / "_values.yaml", "w") as f:
        f.write(safe_dump(SERVICE_VALUES))
    with open(service_dir / "10.values.yaml", "w") as f:
        f.write(VALUES_TEMPLATE)
    with open(service_dir / "chart" / "Chart.yaml", "w") as f:
        f.write(safe_dump(CHART_YAML))
    with open(service_dir / "chart" / "values.yaml", "w") as f:
        f.write(safe_dump(CHART_VALUES))
    with open(service_dir / "chart" / "templates" / "deployment.yaml", "w") as f:
        f.write(DEPLOYMENT_TEMPLATE)

    os.makedirs(tmp_path / "k8s" / "clusters")
    with open(tmp_path / "k8s" / "clusters" / "cluster1.yaml", "w") as f:
        f.write(safe_dump(CLUSTER))

    os.makedirs(tmp_path / "cli_config")
    with open(tmp_path / "cli_config" / "configuration.yaml", "w") as f:
        f.write(safe_dump(CONFIGURATION))

    start_workspace_root = workspace_root().as_posix()
    start_config_file = os.environ.get("SENTRY_KUBE_CONFIG_FILE")
    set_workspace_root_start(tmp_path.as_posix())
    os.environ["SENTRY_KUBE_CONFIG_FILE"] = str(
        tmp_path / "cli_config" / "configuration.yaml"
    )
    # The cluster configuration cache is keyed on (K8sConfig, cluster name),
    # which collide between tests sharing the conftest configuration: make
    # sure this fixture neither reads nor leaves stale entries.
    load_cluster_configuration.cache_clear()
    init_cluster_context("customer1", "cluster1")
    yield tmp_path
    load_cluster_configuration.cache_clear()
    set_workspace_root_start(start_workspace_root)
    if start_config_file is not None:
        os.environ["SENTRY_KUBE_CONFIG_FILE"] = start_config_file


def _release(name="my-helm-service", namespace="default", chart=None):
    return HelmRelease(
        name=name,
        chart=chart
        or HelmChart(
            name="chart",
            repo=None,
            version=None,
            dynamic_app_version=True,
            dynamic_version_path="image.tag",
        ),
        namespace=namespace,
        templates=[],
        strategy=HelmReleaseStrategy.from_spec("standard"),
    )


class TestSplitReleaseManifests:
    def test_split_and_naming(self):
        files = _split_release_manifests(_release(), HELM_TEMPLATE_OUTPUT)
        assert sorted(files) == [
            "default-deployment-my-helm-service.yaml",
            "other-service-my-helm-service.yaml",
        ]
        deployment = safe_load(files["default-deployment-my-helm-service.yaml"])
        assert deployment["kind"] == "Deployment"
        assert deployment["spec"]["replicas"] == 3

    def test_empty_documents_are_skipped(self):
        files = _split_release_manifests(_release(), "---\n\n---\n# comment only\n")
        assert files == {}

    def test_name_collisions_get_deterministic_suffixes(self):
        stream = "\n---\n".join(
            [
                "kind: ConfigMap\nmetadata:\n  name: cm\ndata:\n  a: '1'",
                "kind: ConfigMap\nmetadata:\n  name: cm\ndata:\n  a: '2'",
            ]
        )
        files = _split_release_manifests(_release(), stream)
        assert sorted(files) == [
            "default-configmap-cm-2.yaml",
            "default-configmap-cm.yaml",
        ]


class TestFetchChart:
    def test_local_chart(self, helm_workspace):
        service_path = helm_workspace / "k8s" / "helm_services" / "my-helm-service"
        chart = HelmChart(
            name="chart",
            repo=None,
            version=None,
            dynamic_app_version=True,
            dynamic_version_path="image.tag",
        )
        assert fetch_chart(chart, service_path) == service_path / "chart"

    def test_local_chart_missing(self, tmp_path):
        chart = HelmChart(
            name="chart",
            repo=None,
            version=None,
            dynamic_app_version=True,
            dynamic_version_path="image.tag",
        )
        with pytest.raises(HelmException):
            fetch_chart(chart, tmp_path)

    def test_remote_chart_requires_pinned_version(self, tmp_path):
        chart = HelmChart(
            name="keda",
            repo="https://kedacore.github.io/charts",
            version=None,
            dynamic_app_version=True,
            dynamic_version_path="image.tag",
        )
        with pytest.raises(HelmException, match="pin a version"):
            fetch_chart(chart, tmp_path)

    @staticmethod
    def _fake_pull(commands):
        def inner(cmd, raise_on_err=False, capture_stderr=False):
            commands.append(cmd)
            assert cmd[0] == "pull"
            destination = Path(cmd[cmd.index("--destination") + 1])
            (destination / "keda-2.14.0.tgz").write_bytes(b"fake-chart")
            return ""

        return inner

    def test_https_chart_is_pulled_then_cached(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SENTRY_KUBE_HELM_CHART_CACHE", str(tmp_path / "cache"))
        chart = HelmChart(
            name="keda",
            repo="https://kedacore.github.io/charts",
            version="2.14.0",
            dynamic_app_version=True,
            dynamic_version_path="image.tag",
        )
        commands: list[list[str]] = []
        with patch(
            "libsentrykube.helm._run_helm", side_effect=self._fake_pull(commands)
        ):
            first = fetch_chart(chart, tmp_path)
            second = fetch_chart(chart, tmp_path)

        assert first == second
        assert first.name == "keda-2.14.0.tgz"
        assert first.read_bytes() == b"fake-chart"
        # The second fetch must be served from the cache.
        assert len(commands) == 1
        cmd = commands[0]
        assert cmd[1] == "keda"
        assert cmd[cmd.index("--version") + 1] == "2.14.0"
        assert cmd[cmd.index("--repo") + 1] == "https://kedacore.github.io/charts"

    def test_oci_chart_pull_has_no_repo_flag(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SENTRY_KUBE_HELM_CHART_CACHE", str(tmp_path / "cache"))
        repo = "oci://us-central1-docker.pkg.dev/project/charts/keda"
        chart = HelmChart(
            name="keda",
            repo=repo,
            version="2.14.0",
            dynamic_app_version=True,
            dynamic_version_path="image.tag",
        )
        commands: list[list[str]] = []
        with patch(
            "libsentrykube.helm._run_helm", side_effect=self._fake_pull(commands)
        ):
            fetch_chart(chart, tmp_path)

        assert len(commands) == 1
        assert commands[0][1] == repo
        assert "--repo" not in commands[0]

    def test_failed_pull_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SENTRY_KUBE_HELM_CHART_CACHE", str(tmp_path / "cache"))
        chart = HelmChart(
            name="keda",
            repo="https://kedacore.github.io/charts",
            version="2.14.0",
            dynamic_app_version=True,
            dynamic_version_path="image.tag",
        )
        with patch(
            "libsentrykube.helm._run_helm",
            side_effect=HelmException("connection refused"),
        ):
            with pytest.raises(HelmException, match="connection refused"):
                fetch_chart(chart, tmp_path)
        # A failed pull must not leave a (broken) cache entry behind.
        with patch(
            "libsentrykube.helm._run_helm",
            side_effect=HelmException("connection refused"),
        ):
            with pytest.raises(HelmException, match="connection refused"):
                fetch_chart(chart, tmp_path)


class TestMaterializeManifests:
    def _materialized_dir(self, workspace):
        return (
            workspace
            / "k8s"
            / "materialized_helm_manifests"
            / "cluster1"
            / "my-helm-service"
        )

    def test_materialize_and_rerun_is_stable(self, helm_workspace):
        commands: list[list[str]] = []
        values_payloads: list[dict] = []

        def fake_template(cmd, raise_on_err=False, capture_stderr=False):
            commands.append(cmd)
            assert cmd[0] == "template"
            # The values files only live for the duration of the helm
            # invocation: capture them here.
            values_payloads.append(
                safe_load(Path(cmd[cmd.index("-f") + 1]).read_text())
            )
            return HELM_TEMPLATE_OUTPUT

        with patch("libsentrykube.helm._run_helm", side_effect=fake_template):
            changed = materialize_manifests("customer1", "my-helm-service", "cluster1")
        assert changed is True

        output_dir = self._materialized_dir(helm_workspace)
        assert sorted(p.name for p in output_dir.iterdir()) == [
            "default-deployment-my-helm-service.yaml",
            "other-service-my-helm-service.yaml",
        ]
        contents = {p.name: p.read_text() for p in output_dir.iterdir() if p.is_file()}

        # Deterministic helm invocation: release name, pinned kube version,
        # CRDs included and the merged values passed in order.
        cmd = commands[0]
        assert cmd[1] == "my-helm-service"
        assert cmd[cmd.index("--namespace") + 1] == "default"
        assert "--include-crds" in cmd
        assert cmd[cmd.index("--kube-version") + 1]
        assert values_payloads[0] == {
            "replicaCount": 3,
            "nodeSelector": {"nodepool": "relay-pop"},
        }

        # Re-running with no input change touches nothing and is
        # byte-identical.
        with patch("libsentrykube.helm._run_helm", side_effect=fake_template):
            changed = materialize_manifests("customer1", "my-helm-service", "cluster1")
        assert changed is False
        assert {
            p.name: p.read_text() for p in output_dir.iterdir() if p.is_file()
        } == contents

    def test_orphaned_files_are_removed(self, helm_workspace):
        output_dir = self._materialized_dir(helm_workspace)
        output_dir.mkdir(parents=True)
        (output_dir / "default-deployment-stale.yaml").write_text("kind: Deployment\n")

        with patch("libsentrykube.helm._run_helm", return_value=HELM_TEMPLATE_OUTPUT):
            changed = materialize_manifests("customer1", "my-helm-service", "cluster1")
        assert changed is True
        assert not (output_dir / "default-deployment-stale.yaml").exists()

    def test_multiple_releases_get_subdirectories(self, helm_workspace):
        service_dir = helm_workspace / "k8s" / "helm_services" / "my-helm-service"
        with open(service_dir / "_helm.yaml", "w") as f:
            f.write(
                safe_dump(
                    {
                        "chart": "chart",
                        "releases": [
                            {"name": "production", "namespace": "relay"},
                            {"name": "canary", "namespace": "relay"},
                        ],
                    }
                )
            )

        release_names = []

        def fake_template(cmd, raise_on_err=False, capture_stderr=False):
            release_names.append((cmd[1], cmd[cmd.index("--namespace") + 1]))
            return f"kind: Deployment\nmetadata:\n  name: {cmd[1]}\n"

        with patch("libsentrykube.helm._run_helm", side_effect=fake_template):
            changed = materialize_manifests("customer1", "my-helm-service", "cluster1")
        assert changed is True
        assert release_names == [
            ("production", "relay"),
            ("canary", "relay"),
        ]

        output_dir = self._materialized_dir(helm_workspace)
        assert (output_dir / "production" / "relay-deployment-production.yaml").exists()
        assert (output_dir / "canary" / "relay-deployment-canary.yaml").exists()

    def test_render_failure_is_loud(self, helm_workspace):
        with patch(
            "libsentrykube.helm._run_helm",
            side_effect=HelmException("template error"),
        ):
            with pytest.raises(HelmException, match="template error"):
                materialize_manifests("customer1", "my-helm-service", "cluster1")


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm binary not available")
class TestMaterializeManifestsIntegration:
    def test_local_chart_end_to_end(self, helm_workspace):
        changed = materialize_manifests("customer1", "my-helm-service", "cluster1")
        assert changed is True

        output_dir = (
            helm_workspace
            / "k8s"
            / "materialized_helm_manifests"
            / "cluster1"
            / "my-helm-service"
        )
        deployment_file = output_dir / "default-deployment-my-helm-service.yaml"
        deployment = safe_load(deployment_file.read_text())
        # The merged sentry-kube values must win over the chart defaults
        # and the real scheduling constraints must be visible.
        assert deployment["spec"]["replicas"] == 3
        assert deployment["spec"]["template"]["spec"]["nodeSelector"] == {
            "nodepool": "relay-pop"
        }

        content = deployment_file.read_text()
        assert (
            materialize_manifests("customer1", "my-helm-service", "cluster1") is False
        )
        assert deployment_file.read_text() == content
