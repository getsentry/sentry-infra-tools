from libsentrykube.lint import kube_linter, get_kubelinter_config
from typing import Generator
from pathlib import Path
import os
import tempfile
from yaml import safe_dump
import pytest
from libsentrykube.utils import set_workspace_root_start

MANIFEST = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nginx-deployment
  labels:
    app: nginx
spec:
  replicas: 3
  selector:
    matchLabels:
      app: nginx
  template:
    metadata:
      labels:
        app: nginx
    spec:
      containers:
      - name: nginx
        image: nginx:latest
        ports:
        - containerPort: 80
"""

CONFIGURATION = {
    "sites": {
        "test_site": {
            "name": "us",
            "region": "us-central1",
            "zone": "b",
            "network": "global/networks/sentry",
            "subnetwork": "regions/us-central1/subnetworks/sentry-default",
        }
    },
    "silo_regions": {
        "customer1": {
            "bastion": {
                "spawner_endpoint": "FIXME",
                "site": "test_site",
            },
            "k8s": {
                "root": "k8s",
                "cluster_def_root": "clusters/customer1",
                "materialized_manifests": "materialized_manifests",
            },
        },
        "customer2": {
            "bastion": {
                "spawner_endpoint": "FIXME",
                "site": "test_site",
            },
            "k8s": {
                "root": "somewhere/k8s",
                "cluster_def_root": "customers",
                "cluster_name": "customer2_cluster",
                "materialized_manifests": "materialized_manifests",
            },
        },
    },
    "service_container_map": {
        "foo": {
            "deployment": "foo-web-production",
            "container": "foo",
        },
    },
}

SNUBA_CONFIG = {"checks": {"exclude": ["check1", "check2"], "include": ["check3"]}}

SNUBA_CONFIG2 = {"checks": {"exclude": ["check3"], "include": ["check1"]}}


@pytest.fixture
def valid_structure() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as temp_dir:
        kubelinter_config = Path(temp_dir) / "k8s/clusters/customer1/kubelinter"
        os.makedirs(kubelinter_config)
        with open(kubelinter_config / "snuba.yaml", "w") as f:
            f.write(safe_dump(SNUBA_CONFIG))

        kubelinter_config = (
            Path(temp_dir) / "somewhere/k8s/customers/customer2_cluster/kubelinter"
        )
        os.makedirs(kubelinter_config)
        with open(kubelinter_config / "snuba.yaml", "w") as f:
            f.write(safe_dump(SNUBA_CONFIG2))

        os.makedirs(Path(temp_dir) / "cli_config")
        with open(Path(temp_dir) / "cli_config/configuration.yaml", "w") as f:
            f.write(safe_dump(CONFIGURATION))

        yield temp_dir


def test_lint() -> None:
    enabled_checks = {
        "latest-tag",
        "no-anti-affinity",
        "no-read-only-root-fs",
        "run-as-non-root",
        "unset-cpu-requirements",
        "unset-memory-requirements",
    }

    errors = kube_linter(MANIFEST, inclusions=enabled_checks)
    assert len(errors) == 6

    assert [check["Check"] for check in errors] == [
        "latest-tag",
        "no-anti-affinity",
        "no-read-only-root-fs",
        "run-as-non-root",
        "unset-cpu-requirements",
        "unset-memory-requirements",
    ]


def test_kubelinter_config(valid_structure) -> None:
    set_workspace_root_start(valid_structure)
    del os.environ["SENTRY_KUBE_CONFIG_FILE"]

    include, exclude = get_kubelinter_config("customer1", "cluster1", "snuba")
    assert include == {"check3"}
    assert exclude == {"check1", "check2"}


def test_kubelinter_config_file_cluster(valid_structure) -> None:
    set_workspace_root_start(valid_structure)
    del os.environ["SENTRY_KUBE_CONFIG_FILE"]

    include, exclude = get_kubelinter_config("customer2", "customer2_cluster", "snuba")
    assert include == {"check1"}
    assert exclude == {"check3"}
