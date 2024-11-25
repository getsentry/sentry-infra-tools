import os
import click

from libsentrykube.context import init_cluster_context
from libsentrykube.kube import _consolidate_variables
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


def test_group_hierarchy_consolidation(hierarchical_override_structure: str) -> None:
    set_workspace_root_start(hierarchical_override_structure)
    os.environ["SENTRY_KUBE_CONFIG_FILE"] = str(
        workspace_root() / "cli_config/configuration.yaml"
    )
    init_cluster_context("customer1", "cluster1")

    returned = _consolidate_variables(
        customer_name="customer1",
        service_name="my_service",
        cluster_name="cluster1",
        external=False,
    )
    assert returned == expected_hierarchical_and_regional_cluster_values


def test_cluster_override_consolidation(
    regional_cluster_specific_override_structure,
) -> None:
    set_workspace_root_start(regional_cluster_specific_override_structure)
    os.environ["SENTRY_KUBE_CONFIG_FILE"] = str(
        workspace_root() / "cli_config/configuration.yaml"
    )
    init_cluster_context("customer1", "cluster1")

    returned = _consolidate_variables(
        customer_name="customer1",
        service_name="my_service",
        cluster_name="cluster1",
        external=False,
    )
    assert returned == expected_hierarchical_and_regional_cluster_values


def test_hierarchical_and_regional_combined_consolidation(
    regional_and_hierarchical_override_structure: str,
):
    set_workspace_root_start(regional_and_hierarchical_override_structure)
    os.environ["SENTRY_KUBE_CONFIG_FILE"] = str(
        workspace_root() / "cli_config/configuration.yaml"
    )
    init_cluster_context("customer1", "cluster1")
    returned = _consolidate_variables(
        customer_name="customer1",
        service_name="my_service",
        cluster_name="cluster1",
        external=False,
    )
    assert returned == expected_combined_cluster_values


def test_single_customer_cluster_file(duplicate_customer_clusters_in_service: str):
    try:
        set_workspace_root_start(duplicate_customer_clusters_in_service)
        os.environ["SENTRY_KUBE_CONFIG_FILE"] = str(
            workspace_root() / "cli_config/configuration.yaml"
        )
        init_cluster_context("customer1", "cluster1")
        _consolidate_variables(
            customer_name="customer1",
            service_name="my_service",
            cluster_name="cluster1",
            external=False,
        )
        # Fails the test if no exception is raised
        assert False
    except click.exceptions.Abort:
        pass
