import os
from typing import Iterator, Generator, List
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
    "helm": {
        "services": [
            {
                "path": "k8s/helm_services/my_helm_service",
                "values": {"key1": "cluster_svc_val1", "key2": "cluster_svc_val2"},
            },
            "k8s/helm_services/my_service",
        ],
        "values": {"key1": "cluster_val1"},
    },
}

CLUSTER_2 = {
    "id": "cluster2",
    "services": [
        "k8s/services/my_service",
    ],
    "helm": {"services": ["k8s/helm_services/my_helm_service"]},
}

CONFIGURATION = {
    "silo_regions": {
        "customer1": {
            "k8s": {
                "root": "k8s",
                "cluster_def_root": "clusters",
                "services_in_cluster_config": "True",
                "materialized_manifests": "materialized_manifests",
            },
        },
        "customer2": {
            "k8s": {
                "root": "k8s",
                "cluster_def_root": "clusters",
                "services_in_cluster_config": "True",
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

TOP_LEVEL_CONFIG = {"config": {"example": "example", "foo": "bar"}}

COMMON_SHARED_CONFIG = {
    "config": {"foo": "123", "baz": "test", "settings": {"abc": 10, "def": "test"}}
}

REGIONAL_SHARED_CONFIG = {
    "config": {
        "foo": "regional-foo-will-be-overwritten-by-cluster-specific-config",
        "regional": "cool-region",
    }
}

CLUSTER_OVERRIDE_CONFIG = {"config": {"foo": "not-foo", "settings": {"abc": 20}}}

HELM_BASIC_CONFIG = {"chart": "chart"}
HELM_OVERRIDE_CONFIG1 = {"releases": ["production"]}
HELM_OVERRIDE_CONFIG2 = {"releases": ["production", "canary"]}


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

        helm_services = k8s / "helm_services"
        os.makedirs(helm_services / "my_helm_service")
        my_helm_service = helm_services / "my_helm_service"
        with open(my_helm_service / "_helm.yaml", "w") as f:
            f.write("")
        with open(my_helm_service / "_values.yaml", "w") as f:
            f.write(safe_dump({"key1": "svc_value1"}))

        os.makedirs(
            helm_services / "my_helm_service" / "region_overrides" / "customer1"
        )

        os.makedirs(helm_services / "my_service")
        another_helm_service = helm_services / "my_service"
        with open(another_helm_service / "_helm.yaml", "w") as f:
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
    """
    Creates the following folder structure:
    temp_dir/
    ├── cli_config/
    │   └── configuration.yaml
    └── k8s/
        ├── services/
        │   ├── my_service/
        │   │   ├── _values.yaml
        │   │   └── region_overrides/
        │   │       └── common_shared_config/
        │   │           ├── _values.yaml
        │   │           └── customer1/
        │   │               └── cluster1.yaml
        │   └── another_service/
        └── helm_services/
            ├── my_helm_service/
            │   ├── _helm.yaml
            │   ├── _values.yaml
            │   └── region_overrides/
            │       └── common_shared_config/
            │           ├── _values.yaml
            │           └── customer1/
            │               └── cluster1/
            │                   ├── _helm.yaml
            │                   └── _values.yaml
            └── my_service/
                └── _helm.yaml
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        k8s = Path(temp_dir) / "k8s"
        create_cluster_data_files(k8s)

        create_structure(
            [
                "services/my_service/region_overrides/common_shared_config/customer1",
                "services/another_service",
                "helm_services/my_helm_service/region_overrides/common_shared_config/customer1/cluster1",
                "helm_services/my_service",
            ],
            root=k8s,
        )

        service_dir = k8s / "services" / "my_service"

        write_data_file(service_dir / "_values.yaml", TOP_LEVEL_CONFIG)

        write_data_file(
            service_dir / "region_overrides" / "common_shared_config" / "_values.yaml",
            COMMON_SHARED_CONFIG,
        )
        write_data_file(
            service_dir
            / "region_overrides"
            / "common_shared_config"
            / "customer1"
            / "cluster1.yaml",
            CLUSTER_OVERRIDE_CONFIG,
        )

        helm_service_dir = k8s / "helm_services" / "my_helm_service"
        write_data_file(helm_service_dir / "_helm.yaml", HELM_BASIC_CONFIG)
        write_data_file(helm_service_dir / "_values.yaml", TOP_LEVEL_CONFIG)
        write_data_file(
            helm_service_dir
            / "region_overrides"
            / "common_shared_config"
            / "_values.yaml",
            COMMON_SHARED_CONFIG,
        )
        write_data_file(
            helm_service_dir
            / "region_overrides"
            / "common_shared_config"
            / "customer1"
            / "cluster1"
            / "_helm.yaml",
            HELM_OVERRIDE_CONFIG1,
        )
        write_data_file(
            helm_service_dir
            / "region_overrides"
            / "common_shared_config"
            / "customer1"
            / "cluster1"
            / "_values.yaml",
            CLUSTER_OVERRIDE_CONFIG,
        )

        helm_service_dir = k8s / "helm_services" / "my_service"
        write_data_file(helm_service_dir / "_helm.yaml", HELM_BASIC_CONFIG)

        create_cli_config(Path(temp_dir))

        yield temp_dir


@pytest.fixture
def regional_cluster_specific_override_structure() -> Generator[str, None, None]:
    """
    Creates the following folder structure:
    temp_dir/
    ├── cli_config/
    │   └── configuration.yaml
    └── k8s/
        ├── services/
        │   ├── my_service/
        │   │   ├── _values.yaml
        │   │   └── region_overrides/
        │   │       └── customer1/
        │   │           ├── _values.yaml
        │   │           └── cluster1.yaml
        │   └── another_service/
        └── helm_services/
            ├── my_helm_service/
            │   ├── _helm.yaml
            │   ├── _values.yaml
            │   └── region_overrides/
            │       └── customer1/
            │           ├── _helm.yaml
            │           ├── _values.yaml
            │           └── cluster1/
            │               ├── _helm.yaml
            │               └── _values.yaml
            └── my_service/
                └── _helm.yaml
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        k8s = Path(temp_dir) / "k8s"
        create_cluster_data_files(k8s)

        create_structure(
            [
                "services/my_service/region_overrides/customer1",
                "services/another_service/",
                "helm_services/my_helm_service/region_overrides/customer1/cluster1",
                "helm_services/my_service/",
            ],
            root=k8s,
        )

        service_dir = k8s / "services" / "my_service"
        write_data_file(service_dir / "_values.yaml", TOP_LEVEL_CONFIG)

        write_data_file(
            service_dir / "region_overrides" / "customer1" / "_values.yaml",
            COMMON_SHARED_CONFIG,
        )
        write_data_file(
            service_dir / "region_overrides" / "customer1" / "cluster1.yaml",
            CLUSTER_OVERRIDE_CONFIG,
        )

        helm_service_dir = k8s / "helm_services" / "my_helm_service"
        write_data_file(helm_service_dir / "_helm.yaml", HELM_BASIC_CONFIG)
        write_data_file(helm_service_dir / "_values.yaml", TOP_LEVEL_CONFIG)
        write_data_file(
            helm_service_dir / "region_overrides" / "customer1" / "_helm.yaml",
            HELM_OVERRIDE_CONFIG1,
        )
        write_data_file(
            helm_service_dir / "region_overrides" / "customer1" / "_values.yaml",
            COMMON_SHARED_CONFIG,
        )
        write_data_file(
            helm_service_dir
            / "region_overrides"
            / "customer1"
            / "cluster1"
            / "_helm.yaml",
            HELM_OVERRIDE_CONFIG2,
        )
        write_data_file(
            helm_service_dir
            / "region_overrides"
            / "customer1"
            / "cluster1"
            / "_values.yaml",
            CLUSTER_OVERRIDE_CONFIG,
        )

        create_cli_config(Path(temp_dir))

        yield temp_dir


@pytest.fixture
def regional_and_hierarchical_override_structure() -> Generator[str, None, None]:
    """
    Creates the following folder structure:
    temp_dir/
    ├── cli_config/
    │   └── configuration.yaml
    └── k8s/
        └── services/
            ├── my_service/
            │   ├── _values.yaml
            │   └── region_overrides/
            │       └── group_one/
            │           ├── _values.yaml
            │           └── customer1/
            │               ├── _values.yaml
            │               └── cluster1.yaml
            └── another_service/
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        k8s = Path(temp_dir) / "k8s"
        create_cluster_data_files(k8s)

        service_dir = k8s / "services" / "my_service"
        create_structure(
            [
                "services/my_service/region_overrides/group_one/customer1",
                "services/another_service",
                "helm_services/my_helm_service/region_overrides/group_one/customer1",
                "helm_services/my_service",
            ],
            root=k8s,
        )

        write_data_file(service_dir / "_values.yaml", TOP_LEVEL_CONFIG)
        write_data_file(
            service_dir / "region_overrides" / "group_one" / "_values.yaml",
            COMMON_SHARED_CONFIG,
        )
        write_data_file(
            service_dir
            / "region_overrides"
            / "group_one"
            / "customer1"
            / "_values.yaml",
            REGIONAL_SHARED_CONFIG,
        )
        write_data_file(
            service_dir
            / "region_overrides"
            / "group_one"
            / "customer1"
            / "cluster1.yaml",
            CLUSTER_OVERRIDE_CONFIG,
        )

        create_cli_config(Path(temp_dir))

        yield temp_dir


@pytest.fixture
def duplicate_customer_clusters_in_service() -> Generator[str, None, None]:
    """
    Creates the following folder structure:
    temp_dir/
    ├── cli_config/
    │   └── configuration.yaml
    └── k8s/
        └── services/
            ├── my_service/
            │   └── region_overrides/
            │       ├── customer1/
            │       │   └── cluster1.yaml
            │       └── group_one/
            │           └── customer1/
            │               └── cluster1.yaml
            └── another_service/
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        k8s = Path(temp_dir) / "k8s"
        create_cluster_data_files(k8s)

        service_dir = k8s / "services" / "my_service"

        create_structure(
            [
                "services/my_service/region_overrides/customer1/",
                "services/my_service/region_overrides/group_one/customer1/",
                "services/another_service/",
                "helm_services/my_helm_service/region_overrides/customer1/",
                "helm_services/my_helm_service/region_overrides/group_one/customer1/",
                "helm_services/my_service/",
            ],
            root=k8s,
        )

        write_data_file(
            service_dir / "region_overrides" / "customer1" / "cluster1.yaml",
            CLUSTER_OVERRIDE_CONFIG,
        )
        write_data_file(
            service_dir
            / "region_overrides"
            / "group_one"
            / "customer1"
            / "cluster1.yaml",
            CLUSTER_OVERRIDE_CONFIG,
        )

        create_cli_config(Path(temp_dir))

        yield temp_dir


@pytest.fixture
def duplicate_customer_dirs_in_service() -> Generator[str, None, None]:
    """
    Creates the following folder structure:
    temp_dir/
    ├── cli_config/
    │   └── configuration.yaml
    └── k8s/
        └── services/
            ├── my_service/
            │   └── region_overrides/
            │       ├── customer1/
            │       └── group_one/
            │           └── customer1/
            └── another_service/
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        k8s = Path(temp_dir) / "k8s"
        create_cluster_data_files(k8s)

        create_structure(
            [
                "services/my_service/region_overrides/customer1/",
                "services/my_service/region_overrides/group_one/customer1/",
                "services/another_service/",
                "helm_services/my_helm_service/region_overrides/customer1/",
                "helm_services/my_helm_service/region_overrides/group_one/customer1/",
                "helm_services/my_service/",
            ],
            root=k8s,
        )

        create_cli_config(Path(temp_dir))

        yield temp_dir


@pytest.fixture
def regional_without_cluster_specific_override_structure() -> Generator[
    str, None, None
]:
    """
    Creates the following folder structure:
    temp_dir/
    ├── cli_config/
    │   └── configuration.yaml
    └── k8s/
        └── services/
            ├── my_service/
            │   ├── _values.yaml
            │   └── region_overrides/
            │       └── customer1/
            │           └── _values.yaml
            └── another_service/
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        k8s = Path(temp_dir) / "k8s"
        create_cluster_data_files(k8s)

        service_dir = k8s / "services" / "my_service"

        create_structure(
            [
                "services/my_service/region_overrides/customer1/",
                "services/another_service",
                "helm_services/my_helm_service/region_overrides/customer1/",
                "helm_services/my_service",
            ],
            root=k8s,
        )

        write_data_file(service_dir / "_values.yaml", TOP_LEVEL_CONFIG)
        write_data_file(
            service_dir / "region_overrides" / "customer1" / "_values.yaml",
            REGIONAL_SHARED_CONFIG,
        )

        create_cli_config(Path(temp_dir))

        yield temp_dir


@pytest.fixture
def hierarchy_without_cluster_specific_override_structure() -> Generator[
    str, None, None
]:
    """
    Creates the following folder structure:
    temp_dir/
    ├── cli_config/
    │   └── configuration.yaml
    └── k8s/
        └── services/
            ├── my_service/
            │   ├── _values.yaml
            │   └── region_overrides/
            │       └── group1/
            │           ├── _values.yaml
            │           └── customer1/
            └── another_service/
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        k8s = Path(temp_dir) / "k8s"
        create_cluster_data_files(k8s)

        service_dir = k8s / "services" / "my_service"

        create_structure(
            [
                "services/my_service/region_overrides/group1/customer1/",
                "services/another_service",
                "helm_services/my_helm_service/region_overrides/group1/customer1/",
                "helm_services/my_service",
            ],
            root=k8s,
        )

        write_data_file(service_dir / "_values.yaml", TOP_LEVEL_CONFIG)
        write_data_file(
            service_dir / "region_overrides" / "group1" / "_values.yaml",
            COMMON_SHARED_CONFIG,
        )

        create_cli_config(Path(temp_dir))
        yield temp_dir


@pytest.fixture
def hierarchy_with_nested_region_without_cluster_specific_override_structure() -> (
    Generator[str, None, None]
):
    """
    Creates the following folder structure:
    temp_dir/
    ├── cli_config/
    │   └── configuration.yaml
    └── k8s/
        └── services/
            ├── my_service/
            │   ├── _values.yaml
            │   └── region_overrides/
            │       └── group1/
            │           ├── _values.yaml
            │           └── customer1/
            │               └── _values.yaml
            └── another_service/
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        k8s = Path(temp_dir) / "k8s"
        create_cluster_data_files(k8s)

        service_dir = k8s / "services" / "my_service"

        create_structure(
            [
                "services/my_service/region_overrides/group1/customer1/",
                "services/another_service",
                "helm_services/my_helm_service/region_overrides/group1/customer1/",
                "helm_services/my_service",
            ],
            root=k8s,
        )

        write_data_file(service_dir / "_values.yaml", TOP_LEVEL_CONFIG)
        write_data_file(
            service_dir / "region_overrides" / "group1" / "_values.yaml",
            COMMON_SHARED_CONFIG,
        )
        write_data_file(
            service_dir / "region_overrides" / "group1" / "customer1" / "_values.yaml",
            REGIONAL_SHARED_CONFIG,
        )

        create_cli_config(Path(temp_dir))
        yield temp_dir


def create_structure(paths: List[str], root: Path) -> None:
    for path in paths:
        os.makedirs(root / path)


def write_data_file(path: Path, data: dict) -> None:
    with open(path, "w") as f:
        f.write(safe_dump(data))


def create_cluster_data_files(k8s_root: Path) -> None:
    os.makedirs(k8s_root / "clusters")
    write_data_file(k8s_root / "clusters" / "cluster1.yaml", CLUSTER_1)
    write_data_file(k8s_root / "clusters" / "cluster2.yaml", CLUSTER_2)


def create_cli_config(temp_dir: Path) -> None:
    os.makedirs(temp_dir / "cli_config")
    write_data_file(temp_dir / "cli_config" / "configuration.yaml", CONFIGURATION)


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
