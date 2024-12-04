import os
import pytest

from libsentrykube.context import init_cluster_context
from libsentrykube.kube import _consolidate_variables
from libsentrykube.service import CustomerTooOftenDefinedException
from libsentrykube.utils import set_workspace_root_start, workspace_root

expected_consolidated_values = {
    "saas": {
        "customer": {
            "service1": {
                "key1": "value1",  # From the value file
                "key2": {
                    "subkey2_1": "value2_1",  # From the value file
                    "subkey2_2": 2,  # From the value file
                    "subkey2_3": ["value2_3_1_replaced"],  # From the region override
                    "subkey2_4": [
                        "value2_4_1_managed_replaced"
                    ],  # From the managed file
                    "subkey2_5": [
                        "value2_5_1_managed_replaced"
                    ],  # From the managed file
                },
            },
            "service2": {
                "key3": "three",  # From the cluster file
            },
            "service4": {
                "key1": "value4",  # Cluster file overrides everything
            },
        }
    }
}

expected_hierarchical_and_regional_cluster_values = {
    "config": {
        "example": "example",
        "foo": "not-foo",
        "baz": "test",
        "settings": {"abc": 20, "def": "test"},
    },
    "key1": "value1",
}

expected_combined_cluster_values = {
    "config": {
        "regional": "cool-region",
        "example": "example",
        "foo": "not-foo",
        "baz": "test",
        "settings": {"abc": 20, "def": "test"},
    },
    "key1": "value1",
}

expected_regional_without_cluster_specific_values = {
    "config": {
        "example": "example",
        "foo": "regional-foo-will-be-overwritten-by-cluster-specific-config",
        "regional": "cool-region",
    },
    "key1": "value1",
}

expected_hierarchical_without_cluster_specific_values = {
    "config": {"example": "example", "foo": "bar"},
    "key1": "value1",
}

expected_hierarchical_and_regional_without_cluster_specific_values = {
    "config": {
        "example": "example",
        "foo": "regional-foo-will-be-overwritten-by-cluster-specific-config",
        "baz": "test",
        "regional": "cool-region",
        "settings": {"abc": 10, "def": "test"},
    },
    "key1": "value1",
}


def test_consolidate_variables_not_external():
    region = "saas"
    cluster = "customer"
    init_cluster_context(region, cluster)
    for service in ["service1", "service2", "service4"]:
        returned = _consolidate_variables(
            customer_name=region,
            service_name=service,
            cluster_name=cluster,
            external=False,
        )
        assert returned == expected_consolidated_values[region][cluster][service]


def test_consolidate_variables_group_hierarchy(
    hierarchical_override_structure: str,
) -> None:
    initialize_cluster(hierarchical_override_structure)
    returned = _consolidate_variables(
        customer_name="customer1",
        service_name="my_service",
        cluster_name="cluster1",
        external=False,
    )
    assert returned == expected_hierarchical_and_regional_cluster_values


def test_consolidate_variables_cluster_override(
    regional_cluster_specific_override_structure,
) -> None:
    initialize_cluster(regional_cluster_specific_override_structure)
    returned = _consolidate_variables(
        customer_name="customer1",
        service_name="my_service",
        cluster_name="cluster1",
        external=False,
    )
    assert returned == expected_hierarchical_and_regional_cluster_values


def test_consolidate_variables_hierarchical_and_regional_combined(
    regional_and_hierarchical_override_structure: str,
):
    initialize_cluster(regional_and_hierarchical_override_structure)
    returned = _consolidate_variables(
        customer_name="customer1",
        service_name="my_service",
        cluster_name="cluster1",
        external=False,
    )
    assert returned == expected_combined_cluster_values


def test_consolidate_variables_multiple_cluster_files_same_customer(
    duplicate_customer_clusters_in_service: str,
):
    with pytest.raises(CustomerTooOftenDefinedException):
        initialize_cluster(duplicate_customer_clusters_in_service)
        _consolidate_variables(
            customer_name="customer1",
            service_name="my_service",
            cluster_name="cluster1",
            external=False,
        )


def test_consolidate_variables_multiple_dirs_same_customer(
    duplicate_customer_dirs_in_service: str,
):
    with pytest.raises(CustomerTooOftenDefinedException):
        initialize_cluster(duplicate_customer_dirs_in_service)
        _consolidate_variables(
            customer_name="customer1",
            service_name="my_service",
            cluster_name="cluster1",
            external=False,
        )


def test_consolidate_variables_regional_config_without_cluster_specific_file(
    regional_without_cluster_specific_override_structure: str,
):
    initialize_cluster(regional_without_cluster_specific_override_structure)
    returned = _consolidate_variables(
        customer_name="customer1",
        service_name="my_service",
        cluster_name="cluster1",
        external=False,
    )
    assert returned == expected_regional_without_cluster_specific_values


def test_consolidate_variables_hierarchical_config_without_cluster_specific_file(
    hierarchy_without_cluster_specific_override_structure: str,
):
    initialize_cluster(hierarchy_without_cluster_specific_override_structure)
    returned = _consolidate_variables(
        customer_name="customer1",
        service_name="my_service",
        cluster_name="cluster1",
        external=False,
    )
    assert returned == expected_hierarchical_without_cluster_specific_values


def test_consolidate_variables_hierarchical_and_regional_config_without_cluster_specific_file(
    hierarchy_with_nested_region_without_cluster_specific_override_structure: str,
):
    initialize_cluster(
        hierarchy_with_nested_region_without_cluster_specific_override_structure
    )
    returned = _consolidate_variables(
        customer_name="customer1",
        service_name="my_service",
        cluster_name="cluster1",
        external=False,
    )
    assert (
        returned == expected_hierarchical_and_regional_without_cluster_specific_values
    )


def initialize_cluster(
    workspace_root_path: str, customer_name="customer1", cluster_name="cluster1"
):
    set_workspace_root_start(workspace_root_path)
    os.environ["SENTRY_KUBE_CONFIG_FILE"] = str(
        workspace_root() / "cli_config/configuration.yaml"
    )
    init_cluster_context(customer_name, cluster_name)
