import os
from typing import Iterator, Generator
import tempfile
from pathlib import Path
from yaml import safe_dump

import pytest
from libsentrykube.utils import set_workspace_root_start
from libsentrykube.utils import workspace_root


@pytest.fixture(autouse=True)
def set_workspaceroot() -> Iterator[None]:
    """
    Most tests rely on the workspaceroot directory to be set to the
    workspace directory before loading configuration or services.
    The default value is not good for tests, so we ensure all
    tests are properly set up.
    """

    start_workspace_root = workspace_root().as_posix()
    set_workspace_root_start((workspace_root() / "libsentrykube/tests").as_posix())
    os.environ["SENTRY_KUBE_CONFIG_FILE"] = str(workspace_root() / "config.yaml")
    yield
    set_workspace_root_start(start_workspace_root)


CLUSTER_1 = {
    "id": "cluster1",
    "services": [
        "k8s/services/my_service",
        "k8s/services/another_service",
    ],
    "my_service": {"key1": "value1"},
}

CLUSTER_2 = {
    "id": "cluster2",
    "services": [
        "k8s/services/my_service",
    ],
}

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
                "cluster_def_root": "clusters",
                "services_in_cluster_config": "True",
                "materialized_manifests": "materialized_manifests",
            },
        },
        "customer2": {
            "bastion": {
                "spawner_endpoint": "FIXME",
                "site": "test_site",
            },
            "k8s": {
                "root": "k8s",
                "cluster_def_root": "clusters",
                "services_in_cluster_config": "True",
                "materialized_manifests": "materialized_manifests",
            },
        },
    },
}

COMMON_SHARED_CONFIG = {
    "config": {"foo": "123", "baz": "test", "settings": {"abc": 10, "def": "test"}}
}

CLUSTER_OVERRIDE_CONFIG = {"config": {"foo": "not-foo", "settings": {"abc": 20}}}


@pytest.fixture
def config_structure() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as temp_dir:
        k8s = Path(temp_dir) / "k8s"

        services = k8s / "services"
        os.makedirs(services / "my_service")
        my_service = services / "my_service"
        with open(my_service / "deployment.yaml", "w") as f:
            f.write("")
        with open(my_service / "_values.yaml", "w") as f:
            f.write(safe_dump({"key1": "value1"}))

        os.makedirs(services / "my_service" / "region_overrides" / "customer1")

        os.makedirs(services / "another_service")
        another_service = services / "another_service"
        with open(another_service / "deployment.yaml", "w") as f:
            f.write("")

        os.makedirs(k8s / "clusters")
        clusters = k8s / "clusters"
        with open(clusters / "cluster1.yaml", "w") as f:
            f.write(safe_dump(CLUSTER_1))
        with open(clusters / "cluster2.yaml", "w") as f:
            f.write(safe_dump(CLUSTER_2))

        os.makedirs(Path(temp_dir) / "cli_config")
        with open(Path(temp_dir) / "cli_config/configuration.yaml", "w") as f:
            f.write(safe_dump(CONFIGURATION))

        yield temp_dir


@pytest.fixture
def hierarchical_override_structure() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as temp_dir:
        k8s = Path(temp_dir) / "k8s"
        os.makedirs(k8s / "clusters")
        clusters = k8s / "clusters"
        with open(clusters / "cluster1.yaml", "w") as f:
            f.write(safe_dump(CLUSTER_1))
        with open(clusters / "cluster2.yaml", "w") as f:
            f.write(safe_dump(CLUSTER_2))

        service_dir = k8s / "services" / "my_service"
        region_overrides = service_dir / "region_overrides"
        os.makedirs(region_overrides)

        os.makedirs(k8s / "services" / "another_service")
        common_config = region_overrides / "common_shared_config"
        os.makedirs(common_config)

        with open(common_config / "_values.yaml", "w") as f:
            f.write(safe_dump(COMMON_SHARED_CONFIG))

        region_dir = common_config / "customer1"
        os.makedirs(region_dir)

        with open(region_dir / "cluster1.yaml", "w") as f:
            f.write(safe_dump(CLUSTER_OVERRIDE_CONFIG))

        os.makedirs(Path(temp_dir) / "cli_config")
        with open(Path(temp_dir) / "cli_config" / "configuration.yaml", "w") as f:
            f.write(safe_dump(CONFIGURATION))

        os.environ["SENTRY_KUBE_CONFIG_FILE"] = str(
            Path(temp_dir) / "cli_config" / "configuration.yaml"
        )

        yield temp_dir


@pytest.fixture
def regional_cluster_specific_override_structure() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as temp_dir:
        k8s = Path(temp_dir) / "k8s"
        os.makedirs(k8s / "clusters")
        clusters = k8s / "clusters"
        with open(clusters / "cluster1.yaml", "w") as f:
            f.write(safe_dump(CLUSTER_1))
        with open(clusters / "cluster2.yaml", "w") as f:
            f.write(safe_dump(CLUSTER_2))

        service_dir = k8s / "services" / "my_service"
        region_overrides = service_dir / "region_overrides"
        os.makedirs(region_overrides)

        os.makedirs(k8s / "services" / "another_service")

        region_dir = region_overrides / "customer1"
        os.makedirs(region_dir)

        with open(region_dir / "_values.yaml", "w") as f:
            f.write(safe_dump(COMMON_SHARED_CONFIG))

        with open(region_dir / "cluster1.yaml", "w") as f:
            f.write(safe_dump(CLUSTER_OVERRIDE_CONFIG))

        os.makedirs(Path(temp_dir) / "cli_config")
        with open(Path(temp_dir) / "cli_config" / "configuration.yaml", "w") as f:
            f.write(safe_dump(CONFIGURATION))

        yield temp_dir


@pytest.fixture
def initialized_config_structure(config_structure: str) -> Generator[str, None, None]:
    directory = config_structure

    start_workspace_root = workspace_root().as_posix()
    set_workspace_root_start(directory)
    os.environ["SENTRY_KUBE_CONFIG_FILE"] = str(
        workspace_root() / "cli_config/configuration.yaml"
    )
    yield directory
    set_workspace_root_start(start_workspace_root)
