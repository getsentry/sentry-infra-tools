import pytest
from libsentrykube.context import init_cluster_context
import os
from pathlib import Path
from unittest.mock import patch, Mock

from libsentrykube.service import (
    get_hierarchical_value_overrides,
    get_service_ctx_overrides,
    get_service_data,
    get_service_values,
    get_service_value_overrides,
    get_tools_managed_service_value_overrides,
    get_service_value_override_path,
    get_service_path,
    get_service_template_files,
    write_managed_values_overrides,
    merge_values_files_no_conflict,
)
from libsentrykube.utils import set_workspace_root_start
from libsentrykube.utils import workspace_root


expected_service_data = {
    "saas": {"customer": {"service1": {}, "service2": {"key3": "three"}}}
}

expected_service_values = {
    "saas": {
        "customer": {
            "service1": {
                "key1": "value1",
                "key2": {
                    "subkey2_1": "value2_1",
                    "subkey2_2": 2,
                    "subkey2_3": ["value2_3_1"],
                },
                "consumer_key1": "value1",
                "consumer_key2": {
                    "subkey2_1": "value2_1",
                    "subkey2_2": 2,
                    "subkey2_3": ["value2_3_1"],
                },
            },
            "service2": {},
        }
    }
}

expected_service_value_overrides = {
    "saas": {
        "customer": {
            "service1": {
                "key2": {
                    "subkey2_3": ["value2_3_1_replaced"],
                    "subkey2_4": [
                        "value2_4_1_replaced"  # From the managed file
                    ],
                },
                "consumer_key2": {
                    "subkey2_3": ["value2_3_1_replaced"],
                    "subkey2_4": [
                        "value2_4_1_replaced"  # From the managed file
                    ],
                },
            },
            "service2": {},
        }
    }
}

expected_service_value_managed_overrides = {
    "saas": {
        "customer": {
            "service1": {
                "key2": {
                    "subkey2_4": ["value2_4_1_managed_replaced"],
                    "subkey2_5": ["value2_5_1_managed_replaced"],
                },
                "consumer_key2": {
                    "subkey2_4": ["value2_4_1_managed_replaced"],
                    "subkey2_5": ["value2_5_1_managed_replaced"],
                },
            },
            "service2": {},
        }
    }
}

expected_hierarchical_and_regional_cluster_values = {
    "config": {"foo": "not-foo", "baz": "test", "settings": {"abc": 20, "def": "test"}}
}

expected_hierarchical_and_regional_cluster_helmcfg = {"releases": ["production"]}

expected_regional_cluster_values = {
    "config": {
        "foo": "not-foo",
        "settings": {"abc": 20},
    }
}

expected_regional_cluster_helmcfg = {"releases": ["production", "canary"]}


def test_get_service_values_not_external():
    region = "saas"
    cluster = "customer"
    for service in ["service1", "service2"]:
        init_cluster_context(region, cluster)
        returned = get_service_values(service_name=service, external=False)
        assert returned == expected_service_values[region][cluster][service]


def test_get_service_values_external():
    region = "saas"
    cluster = "customer"
    for service in ["service1", "service2"]:
        init_cluster_context(region, cluster)
        returned = get_service_values(
            service_name=f"k8s_root/services/{service}", external=True
        )
        assert returned == expected_service_values[region][cluster][service]


def test_service_override_path() -> None:
    region = "saas"
    cluster = "customer"

    init_cluster_context(region, cluster)
    returned = get_service_value_override_path(
        service_name="service1",
        region_name=region,
        external=False,
    )
    # saas is mapped to us
    assert returned == get_service_path("service1") / "region_overrides" / "us"


def test_get_service_value_overrides_present():
    region = "saas"
    cluster = "customer"
    for service in ["service1", "service2"]:
        init_cluster_context(region, cluster)
        returned = get_service_value_overrides(
            service_name=service,
            region_name=region,
            cluster_name=cluster,
            external=False,
        )
        assert returned == expected_service_value_overrides[region][cluster][service]


def test_get_service_value_managed_overrides():
    region = "saas"
    cluster = "customer"

    init_cluster_context(region, cluster)
    for service in ["service1", "service2"]:
        returned = get_tools_managed_service_value_overrides(
            service_name=service,
            region_name=region,
            cluster_name=cluster,
            external=False,
        )
        assert (
            returned
            == expected_service_value_managed_overrides[region][cluster][service]
        )


def test_get_service_value_overrides_missing():
    region = "saas"
    cluster = "customer"
    service = "service2"
    init_cluster_context(region, cluster)
    returned = get_service_value_overrides(
        service_name=service, region_name=region, cluster_name=cluster, external=False
    )
    assert returned == expected_service_value_overrides[region][cluster][service]


def test_get_service_data_present():
    region = "saas"
    cluster = "customer"
    service = "service2"
    init_cluster_context(region, cluster)
    service_data, _ = get_service_data(
        customer_name=region, service_name=service, cluster_name=cluster
    )
    assert service_data == expected_service_data[region][cluster][service]


def test_write_managed_file(config_structure) -> None:
    # TODO: Refactor the other tests to use a temporary dir as config
    # and follow this pattern, then remove the autouse fixture.
    start_workspace_root = workspace_root().as_posix()
    set_workspace_root_start(config_structure)
    os.environ["SENTRY_KUBE_CONFIG_FILE"] = str(
        workspace_root() / "cli_config/configuration.yaml"
    )
    init_cluster_context("customer1", "cluster1")

    write_managed_values_overrides(
        {"key2": "value2"}, "my_service", "customer1", "cluster1"
    )
    assert get_tools_managed_service_value_overrides(
        service_name="my_service",
        region_name="customer1",
        cluster_name="cluster1",
        external=False,
    ) == {
        "key2": "value2",
    }

    path = (
        get_service_value_override_path(
            service_name="my_service",
            region_name="customer1",
            external=False,
        )
        / "cluster1.managed.yaml"
    )
    assert path.exists()
    assert path.is_file()

    set_workspace_root_start(start_workspace_root)


def test_get_hierarchical_value_overrides(hierarchical_override_structure: str) -> None:
    set_workspace_root_start(hierarchical_override_structure)
    os.environ["SENTRY_KUBE_CONFIG_FILE"] = str(
        workspace_root() / "cli_config/configuration.yaml"
    )
    init_cluster_context("customer1", "cluster1")

    returned = get_hierarchical_value_overrides(
        service_name="my_service", region_name="customer1", cluster_name="cluster1"
    )

    assert returned == expected_hierarchical_and_regional_cluster_values


def test_get_helm_hierarchical_value_overrides(
    hierarchical_override_structure: str,
) -> None:
    set_workspace_root_start(hierarchical_override_structure)
    os.environ["SENTRY_KUBE_CONFIG_FILE"] = str(
        workspace_root() / "cli_config/configuration.yaml"
    )
    init_cluster_context("customer1", "cluster1")

    returned_values = get_hierarchical_value_overrides(
        service_name="my_helm_service",
        region_name="customer1",
        cluster_name="cluster1",
        namespace="helm",
    )
    returned_helmcfg = get_hierarchical_value_overrides(
        service_name="my_helm_service",
        region_name="customer1",
        cluster_name="cluster1",
        namespace="helm",
        src_files_prefix="_helm",
    )

    assert returned_values == expected_hierarchical_and_regional_cluster_values
    assert returned_helmcfg == expected_hierarchical_and_regional_cluster_helmcfg


def test_regional_cluster_value_overrides(
    regional_cluster_specific_override_structure: str,
) -> None:
    set_workspace_root_start(regional_cluster_specific_override_structure)
    os.environ["SENTRY_KUBE_CONFIG_FILE"] = str(
        workspace_root() / "cli_config/configuration.yaml"
    )
    init_cluster_context("customer1", "cluster1")

    returned = get_service_value_overrides(
        service_name="my_service",
        region_name="customer1",
        cluster_name="cluster1",
    )

    assert returned == expected_regional_cluster_values


def test_helm_regional_cluster_value_overrides(
    regional_cluster_specific_override_structure: str,
) -> None:
    set_workspace_root_start(regional_cluster_specific_override_structure)
    os.environ["SENTRY_KUBE_CONFIG_FILE"] = str(
        workspace_root() / "cli_config/configuration.yaml"
    )
    init_cluster_context("customer1", "cluster1")

    returned_values = get_service_ctx_overrides(
        service_name="my_helm_service",
        region_name="customer1",
        cluster_name="cluster1",
        namespace="helm",
        cluster_as_folder=True,
    )
    returned_helmcfg = get_service_ctx_overrides(
        service_name="my_helm_service",
        region_name="customer1",
        cluster_name="cluster1",
        namespace="helm",
        src_files_prefix="_helm",
        cluster_as_folder=True,
    )

    assert returned_values == expected_regional_cluster_values
    assert returned_helmcfg == expected_regional_cluster_helmcfg


def test_get_service_template_files():
    # Create mock files
    mock_files = [
        Path("template1.yaml"),
        Path("template2.yml"),
        Path("template3.yaml.j2"),
        Path("template4.yml.j2"),
        Path("_values.yaml"),  # should be ignored (starts with _)
        Path("other.txt"),  # should be ignored (wrong extension)
    ]

    mock_service_dir = Mock()
    mock_service_dir.is_dir.return_value = True
    mock_service_dir.iterdir.return_value = mock_files

    with patch("libsentrykube.service.get_service_path", return_value=mock_service_dir):
        # Convert generator to list for testing
        templates = list(get_service_template_files("test-service"))

        # Verify we got the expected templates
        assert len(templates) == 4
        assert Path("template1.yaml") in templates
        assert Path("template2.yml") in templates
        assert Path("template3.yaml.j2") in templates
        assert Path("template4.yml.j2") in templates


def test_verify_values_files_no_conflict_no_conflict():
    base = {"workers": {"rabbit-worker-1": {"some": "data"}}}
    new = {"consumer_groups": {"consumer-1": {"other": "data"}}}
    expected = {
        "workers": {"rabbit-worker-1": {"some": "data"}},
        "consumer_groups": {"consumer-1": {"other": "data"}},
    }
    result = merge_values_files_no_conflict(base.copy(), new, "_values_consumers.yaml")
    assert result == expected


def test_verify_values_files_no_conflict_with_conflict():
    base = {"workers": {"rabbit-worker-1": {"some": "data"}}}
    new = {"workers": {"rabbit-worker-2": {"other": "data"}}}
    with pytest.raises(ValueError) as excinfo:
        merge_values_files_no_conflict(base.copy(), new, "_values_consumers.yaml")

    assert "Conflict detected when merging file" in str(excinfo.value)
    assert "duplicate keys {'workers'}" in str(excinfo.value)
