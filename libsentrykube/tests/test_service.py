import pytest
from libsentrykube.context import init_cluster_context
import os
from pathlib import Path
from unittest.mock import patch, Mock, mock_open

from libsentrykube.service import (
    MergeConfig,
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
    get_service_flags,
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
    merge_config = MergeConfig.defaults()
    for service in ["service1", "service2"]:
        init_cluster_context(region, cluster)
        returned = get_service_values(
            service_name=service, merge_config=merge_config, external=False
        )
        assert returned == expected_service_values[region][cluster][service]


def test_get_service_values_external():
    region = "saas"
    cluster = "customer"
    merge_config = MergeConfig.defaults()
    for service in ["service1", "service2"]:
        init_cluster_context(region, cluster)
        returned = get_service_values(
            service_name=f"k8s_root/services/{service}",
            merge_config=merge_config,
            external=True,
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
    merge_config = MergeConfig.defaults()
    for service in ["service1", "service2"]:
        init_cluster_context(region, cluster)
        returned = get_service_value_overrides(
            service_name=service,
            region_name=region,
            cluster_name=cluster,
            merge_config=merge_config,
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
    merge_config = MergeConfig.defaults()
    init_cluster_context(region, cluster)
    returned = get_service_value_overrides(
        service_name=service,
        region_name=region,
        cluster_name=cluster,
        merge_config=merge_config,
        external=False,
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
    merge_config = MergeConfig.defaults()

    returned = get_service_value_overrides(
        service_name="my_service",
        region_name="customer1",
        cluster_name="cluster1",
        merge_config=merge_config,
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
    merge_config = MergeConfig.defaults()

    returned_values = get_service_ctx_overrides(
        service_name="my_helm_service",
        region_name="customer1",
        merge_config=merge_config,
        cluster_name="cluster1",
        namespace="helm",
        cluster_as_folder=True,
    )
    returned_helmcfg = get_service_ctx_overrides(
        service_name="my_helm_service",
        region_name="customer1",
        merge_config=merge_config,
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
    merge_config = MergeConfig.defaults()
    base = {"workers": {"rabbit-worker-1": {"some": "data"}}}
    new = {"consumer_groups": {"consumer-1": {"other": "data"}}}
    expected = {
        "workers": {"rabbit-worker-1": {"some": "data"}},
        "consumer_groups": {"consumer-1": {"other": "data"}},
    }
    result = merge_values_files_no_conflict(
        base.copy(), new, "_values_consumers.yaml", merge_config
    )
    assert result == expected


def test_verify_values_files_no_conflict_with_conflict():
    base = {"workers": {"rabbit-worker-1": {"some": "data"}}}
    new = {"workers": {"rabbit-worker-2": {"other": "data"}}}
    merge_config = MergeConfig.defaults()
    with pytest.raises(ValueError) as excinfo:
        merge_values_files_no_conflict(
            base.copy(), new, "_values_consumers.yaml", merge_config
        )

    assert "Conflict detected when merging file" in str(excinfo.value)
    assert "duplicate key 'workers'" in str(excinfo.value)


def test_verify_values_files_no_conflict_with_conflict_overwrite():
    base = {"workers": {"rabbit-worker-1": {"some": "data"}}}
    new = {"workers": {"rabbit-worker-2": {"other": "data"}}}
    merge_config = MergeConfig({"default": "reject", "paths": {"workers": "overwrite"}})
    expected = {
        "workers": {
            "rabbit-worker-2": {
                "other": "data",
            },
        },
    }
    result = merge_values_files_no_conflict(
        base.copy(), new, "_values_consumers.yaml", merge_config
    )
    assert result == expected


def test_verify_values_files_no_conflict_with_conflict_append():
    base = {"workers": {"rabbit-worker-1": {"some": "data"}}}
    new = {"workers": {"rabbit-worker-2": {"other": "data"}}}
    merge_config = MergeConfig({"default": "append"})
    expected = {
        "workers": {
            "rabbit-worker-1": {
                "some": "data",
            },
            "rabbit-worker-2": {
                "other": "data",
            },
        },
    }
    result = merge_values_files_no_conflict(
        base.copy(), new, "_values_consumers.yaml", merge_config
    )
    assert result == expected


def test_get_service_flags_file_exists():
    """Test get_service_flags when the _sk_flags.yaml file exists."""
    expected_flags = {"feature_enabled": True, "max_instances": 5, "timeout": 30}

    # Create a mock service directory with the flags file
    mock_service_dir = Mock()
    mock_service_dir.is_dir.return_value = True
    mock_flags_file = Mock()
    mock_flags_file.exists.return_value = True

    # Mock the path concatenation
    def mock_truediv(self, other):
        if other == "_sk_flags.yaml":
            return mock_flags_file
        return Mock()

    mock_service_dir.__truediv__ = mock_truediv

    with (
        patch("libsentrykube.service.get_service_path", return_value=mock_service_dir),
        patch(
            "builtins.open",
            mock_open(read_data="feature_enabled: true\nmax_instances: 5\ntimeout: 30"),
        ),
        patch("yaml.safe_load", return_value=expected_flags),
    ):
        result = get_service_flags("test-service")
        assert result == expected_flags


def test_get_service_flags_file_not_exists():
    """Test get_service_flags when the _sk_flags.yaml file doesn't exist."""
    # Create a mock service directory without the flags file
    mock_service_dir = Mock()
    mock_service_dir.is_dir.return_value = True
    mock_flags_file = Mock()
    mock_flags_file.exists.return_value = False

    # Mock the path concatenation
    def mock_truediv(self, other):
        if other == "_sk_flags.yaml":
            return mock_flags_file
        return Mock()

    mock_service_dir.__truediv__ = mock_truediv

    with patch("libsentrykube.service.get_service_path", return_value=mock_service_dir):
        result = get_service_flags("test-service")
        assert result == {}


def test_get_service_flags_service_directory_not_exists():
    """Test get_service_flags when the service directory doesn't exist."""
    import click

    # Create a mock service directory that doesn't exist
    mock_service_dir = Mock()
    mock_service_dir.is_dir.return_value = False

    with (
        patch("libsentrykube.service.get_service_path", return_value=mock_service_dir),
        patch("click.echo") as mock_echo,
    ):
        with pytest.raises(click.Abort):
            get_service_flags("test-service")
        mock_echo.assert_called_once()


def test_get_service_flags_with_namespace():
    """Test get_service_flags with a specific namespace."""
    expected_flags = {"namespace_flag": True}

    # Create a mock service directory with the flags file
    mock_service_dir = Mock()
    mock_service_dir.is_dir.return_value = True
    mock_flags_file = Mock()
    mock_flags_file.exists.return_value = True

    # Mock the path concatenation
    def mock_truediv(self, other):
        if other == "_sk_flags.yaml":
            return mock_flags_file
        return Mock()

    mock_service_dir.__truediv__ = mock_truediv

    with (
        patch("libsentrykube.service.get_service_path", return_value=mock_service_dir),
        patch("builtins.open", mock_open(read_data="namespace_flag: true")),
        patch("yaml.safe_load", return_value=expected_flags),
    ):
        result = get_service_flags("test-service", namespace="helm")
        assert result == expected_flags


def test_get_service_flags_empty_yaml():
    """Test get_service_flags when the YAML file is empty or contains only comments."""
    # Create a mock service directory with the flags file
    mock_service_dir = Mock()
    mock_service_dir.is_dir.return_value = True
    mock_flags_file = Mock()
    mock_flags_file.exists.return_value = True

    # Mock the path concatenation
    def mock_truediv(self, other):
        if other == "_sk_flags.yaml":
            return mock_flags_file
        return Mock()

    mock_service_dir.__truediv__ = mock_truediv

    with (
        patch("libsentrykube.service.get_service_path", return_value=mock_service_dir),
        patch(
            "builtins.open",
            mock_open(read_data="# This is a comment\n# Another comment"),
        ),
        patch("yaml.safe_load", return_value=None),
    ):
        result = get_service_flags("test-service")
        assert result == {}


def test_get_service_flags_invalid_yaml():
    """Test get_service_flags when the YAML file is invalid."""
    # Create a mock service directory with the flags file
    mock_service_dir = Mock()
    mock_service_dir.is_dir.return_value = True
    mock_flags_file = Mock()
    mock_flags_file.exists.return_value = True

    # Mock the path concatenation
    def mock_truediv(self, other):
        if other == "_sk_flags.yaml":
            return mock_flags_file
        return Mock()

    mock_service_dir.__truediv__ = mock_truediv

    with (
        patch("libsentrykube.service.get_service_path", return_value=mock_service_dir),
        patch("builtins.open", mock_open(read_data="invalid: yaml: content:")),
        patch("yaml.safe_load", side_effect=Exception("Invalid YAML")),
    ):
        with pytest.raises(Exception, match="Invalid YAML"):
            get_service_flags("test-service")


def test_merge_config_typical():
    document = """
    default: reject
    paths:
        worker_groups: append
    """

    config = MergeConfig(MergeConfig.load(document))
    assert config.default == MergeConfig.MergeStrategy.REJECT
    assert config.paths["worker_groups"] == MergeConfig.MergeStrategy.APPEND


def test_merge_config_no_paths():
    document = """
    default: overwrite
    """

    config = MergeConfig(MergeConfig.load(document))
    assert config.default == MergeConfig.MergeStrategy.OVERWRITE
    assert len(config.paths) == 0


def test_merge_config_no_defaults():
    document = """
    paths:
        worker_groups: append
    """

    config = MergeConfig(MergeConfig.load(document))
    assert config.default == MergeConfig.MergeStrategy.REJECT
    assert len(config.paths) == 1


def test_merge_config_invalid():
    document = """
    default: foobar
    paths:
        worker_groups: append
    """

    with pytest.raises(Exception, match="is not a valid"):
        _config = MergeConfig(MergeConfig.load(document))
