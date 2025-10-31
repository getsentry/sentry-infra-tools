import os
import pytest

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup
from unittest.mock import patch

from libsentrykube.context import init_cluster_context
from libsentrykube.kube import (
    _consolidate_variables,
    _include_raw,
    _normalize_yaml_content,
)
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
                "consumer_key1": "value1",
                "consumer_key2": {
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


def test_include_raw() -> None:
    dummy_file_text = """name: foo
description: '{{ look at me I'm a jinja-templated var }}'
"""
    with patch("libsentrykube.kube.FileSystemLoader.get_source") as mock_get_source:
        mock_get_source.return_value = (dummy_file_text, None, None)
        assert _include_raw(
            "fake-file.txt", FileSystemLoader("."), Environment()
        ) == Markup(dummy_file_text)


def test_normalize_yaml_content_none():
    """Test that None input returns None."""
    assert _normalize_yaml_content(None) is None


def test_normalize_yaml_content_empty_string():
    """Test that empty string returns None."""
    assert _normalize_yaml_content("") is None
    assert _normalize_yaml_content("   ") is None


def test_normalize_yaml_content_single_document():
    """Test normalization of a single YAML document."""
    yaml_content = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: test-config
  namespace: default
data:
  key1: value1
  key2: value2
"""
    result = _normalize_yaml_content(yaml_content)
    assert result is not None
    assert "kind: ConfigMap" in result
    assert "name: test-config" in result
    # Verify keys are sorted
    assert result.index("key1") < result.index("key2")


def test_normalize_yaml_content_multiple_documents():
    """Test normalization of multiple YAML documents."""
    yaml_content = """
apiVersion: v1
kind: Service
metadata:
  name: my-service
  namespace: default
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-config
  namespace: default
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-deployment
  namespace: default
"""
    result = _normalize_yaml_content(yaml_content)
    assert result is not None
    # Documents should be sorted by kind (ConfigMap < Deployment < Service)
    cm_pos = result.index("kind: ConfigMap")
    deploy_pos = result.index("kind: Deployment")
    svc_pos = result.index("kind: Service")
    assert cm_pos < deploy_pos < svc_pos


def test_normalize_yaml_content_sorts_by_name():
    """Test that documents with the same kind are sorted by name."""
    yaml_content = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: zebra-config
  namespace: default
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: alpha-config
  namespace: default
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: middle-config
  namespace: default
"""
    result = _normalize_yaml_content(yaml_content)
    assert result is not None
    alpha_pos = result.index("name: alpha-config")
    middle_pos = result.index("name: middle-config")
    zebra_pos = result.index("name: zebra-config")
    assert alpha_pos < middle_pos < zebra_pos


def test_normalize_yaml_content_sorts_by_namespace():
    """Test that documents with the same kind and name are sorted by namespace."""
    yaml_content = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: test-config
  namespace: prod
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: test-config
  namespace: default
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: test-config
  namespace: staging
"""
    result = _normalize_yaml_content(yaml_content)
    assert result is not None
    # Should be sorted: default < prod < staging
    default_pos = result.index("namespace: default")
    prod_pos = result.index("namespace: prod")
    staging_pos = result.index("namespace: staging")
    assert default_pos < prod_pos < staging_pos


def test_normalize_yaml_content_filters_empty_documents():
    """Test that empty/None documents are filtered out."""
    yaml_content = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: test-config
data:
  key: value
---
---
apiVersion: v1
kind: Service
metadata:
  name: test-service
"""
    result = _normalize_yaml_content(yaml_content)
    assert result is not None
    # Should only have 2 documents (empty ones filtered out)
    assert result.count("---") == 1  # One separator between two documents


def test_normalize_yaml_content_sorts_keys():
    """Test that keys within documents are sorted."""
    yaml_content = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: test-config
  namespace: default
data:
  zebra: z
  alpha: a
  middle: m
"""
    result = _normalize_yaml_content(yaml_content)
    assert result is not None
    # Within data section, keys should be sorted
    data_section = result[result.index("data:") :]
    alpha_pos = data_section.index("alpha")
    middle_pos = data_section.index("middle")
    zebra_pos = data_section.index("zebra")
    assert alpha_pos < middle_pos < zebra_pos


def test_normalize_yaml_content_idempotent():
    """Test that normalizing the same content twice produces the same result."""
    yaml_content = """
apiVersion: v1
kind: Service
metadata:
  name: my-service
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-config
"""
    result1 = _normalize_yaml_content(yaml_content)
    result2 = _normalize_yaml_content(result1)
    assert result1 == result2


def initialize_cluster(
    workspace_root_path: str,
    customer_name="customer1",
    cluster_name="cluster1",
):
    set_workspace_root_start(workspace_root_path)
    os.environ["SENTRY_KUBE_CONFIG_FILE"] = str(
        workspace_root() / "cli_config/configuration.yaml"
    )
    init_cluster_context(customer_name, cluster_name)


def test_materialize_transition_single_to_split_by_kind(tmp_path):
    """Test transition from single deployment.yaml to split-by-kind files."""
    from libsentrykube.kube import materialize

    mock_yaml = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: test-config
  namespace: default
data:
  key: value
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: test-deployment
  namespace: default
spec:
  replicas: 1
"""

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    # Create initial single deployment.yaml file
    with open(output_dir / "deployment.yaml", "w") as f:
        f.write(mock_yaml)

    # Mock the functions
    with (
        patch("libsentrykube.kube.render_templates") as mock_render,
        patch("libsentrykube.kube.build_materialized_directory") as mock_build,
    ):
        mock_render.return_value = mock_yaml
        mock_build.return_value = output_dir

        # Call materialize with split_by_kind=True
        result = materialize("customer1", "my_service", "cluster1", split_by_kind=True)

        # Should return True since we're changing structure
        assert result is True

        # deployment.yaml should be removed
        assert not (output_dir / "deployment.yaml").exists()

        # Should have created split files
        files = sorted(os.listdir(output_dir))
        assert len(files) == 2
        assert "default-configmap-test-config.yaml" in files
        assert "default-deployment-test-deployment.yaml" in files


def test_materialize_transition_split_to_single(tmp_path):
    """Test transition from split-by-kind files to single deployment.yaml."""
    from libsentrykube.kube import materialize

    mock_yaml = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: test-config
  namespace: default
data:
  key: value
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: test-deployment
  namespace: default
spec:
  replicas: 1
"""

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    # Create initial split files
    configmap_content = """
apiVersion: v1
data:
  key: value
kind: ConfigMap
metadata:
  name: test-config
  namespace: default
"""
    deployment_content = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: test-deployment
  namespace: default
spec:
  replicas: 1
"""

    with open(output_dir / "default-configmap-test-config.yaml", "w") as f:
        f.write(configmap_content)
    with open(output_dir / "default-deployment-test-deployment.yaml", "w") as f:
        f.write(deployment_content)

    # Mock the functions
    with (
        patch("libsentrykube.kube.render_templates") as mock_render,
        patch("libsentrykube.kube.build_materialized_directory") as mock_build,
    ):
        mock_render.return_value = mock_yaml
        mock_build.return_value = output_dir

        # Call materialize with split_by_kind=False
        result = materialize("customer1", "my_service", "cluster1", split_by_kind=False)

        # Should return True since we're changing structure
        assert result is True

        # Split files should be removed
        assert not (output_dir / "default-configmap-test-config.yaml").exists()
        assert not (output_dir / "default-deployment-test-deployment.yaml").exists()

        # Should have created single deployment.yaml
        assert (output_dir / "deployment.yaml").exists()


def test_materialize_split_by_kind_removes_deleted_resources(tmp_path):
    """Test that when using split-by-kind, removed resources delete their files."""
    from libsentrykube.kube import materialize

    # Initial state: 3 resources
    initial_yaml = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: test-config
  namespace: default
data:
  key: value
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: test-deployment
  namespace: default
spec:
  replicas: 1
---
apiVersion: v1
kind: Service
metadata:
  name: test-service
  namespace: default
spec:
  ports:
  - port: 80
"""

    # Updated state: only 2 resources (Service removed)
    updated_yaml = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: test-config
  namespace: default
data:
  key: value
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: test-deployment
  namespace: default
spec:
  replicas: 1
"""

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    # First call: create initial split files
    with (
        patch("libsentrykube.kube.render_templates") as mock_render,
        patch("libsentrykube.kube.build_materialized_directory") as mock_build,
    ):
        mock_render.return_value = initial_yaml
        mock_build.return_value = output_dir

        materialize("customer1", "my_service", "cluster1", split_by_kind=True)

        # Verify all 3 files exist
        assert (output_dir / "default-configmap-test-config.yaml").exists()
        assert (output_dir / "default-deployment-test-deployment.yaml").exists()
        assert (output_dir / "default-service-test-service.yaml").exists()

    # Second call: update with service removed
    with (
        patch("libsentrykube.kube.render_templates") as mock_render,
        patch("libsentrykube.kube.build_materialized_directory") as mock_build,
    ):
        mock_render.return_value = updated_yaml
        mock_build.return_value = output_dir

        result = materialize("customer1", "my_service", "cluster1", split_by_kind=True)

        # Should return True since content changed
        assert result is True

        # Verify only 2 files exist now
        assert (output_dir / "default-configmap-test-config.yaml").exists()
        assert (output_dir / "default-deployment-test-deployment.yaml").exists()
        # Service file should be removed
        assert not (output_dir / "default-service-test-service.yaml").exists()
