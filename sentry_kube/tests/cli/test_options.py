import json
import subprocess
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import click
import pytest
from click.testing import CliRunner

from sentry_kube.cli import main
from sentry_kube.cli.options import options


@dataclass
class FakeCluster:
    name: str
    service_names: list[str]
    services_data: dict[str, str]


def _success(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, stdout, "")


def _configmap(
    resource_version: str,
    options: dict[str, object],
    *,
    include_generated_at_annotation: bool = True,
    include_generated_at_value: bool = True,
) -> subprocess.CompletedProcess[str]:
    metadata: dict[str, object] = {"resourceVersion": resource_version}
    if include_generated_at_annotation:
        metadata["annotations"] = {"generated_at": "old"}
    values: dict[str, object] = {"options": options}
    if include_generated_at_value:
        values["generated_at"] = "old"
    return _success(
        json.dumps(
            {
                "metadata": metadata,
                "data": {"values.json": json.dumps(values)},
            }
        )
    )


def _is_configmap_patch(command: list[str]) -> bool:
    return "patch" in command and command[command.index("patch") + 1] == "configmap"


def _mock_clusters(mock_config: MagicMock, mock_list_clusters: MagicMock) -> None:
    mock_config.return_value.silo_regions = {
        "us": MagicMock(k8s_config="us-config"),
        "control": MagicMock(k8s_config="control-config"),
    }
    mock_list_clusters.side_effect = lambda k8s_config: {
        "us-config": [
            FakeCluster(
                "default",
                ["getsentry", "getsentry-control"],
                {"context": "us-context"},
            )
        ],
        "control-config": [
            FakeCluster(
                "default", ["getsentry-control"], {"context": "control-context"}
            )
        ],
    }[k8s_config]


@pytest.fixture(autouse=True)
def mock_schema_validation() -> Generator[MagicMock, None, None]:
    """Keep Kubernetes command tests independent of the compiled dependency."""
    with patch("sentry_kube.cli.options._validate_against_schema") as validate:
        yield validate


def test_options_is_available_without_selecting_one_customer() -> None:
    result = CliRunner().invoke(main, ["options", "--help"])

    assert result.exit_code == 0, result.output
    assert "options" in result.output
    assert "break-glass" not in result.output
    assert "get" in result.output
    assert "set" in result.output


def test_set_help_explains_fleet_and_region_scoping() -> None:
    result = CliRunner().invoke(options, ["set", "--help"])

    assert result.exit_code == 0, result.output
    assert "Examples:" in result.output
    assert "--include" in result.output
    assert "--exclude" in result.output
    assert "--region" not in result.output
    assert "--exclude-region" not in result.output
    assert "OPTION VALUE" in result.output
    assert "--apply" in result.output
    assert "--schemas" in result.output
    assert "--options-namespace" not in result.output
    assert "--kubernetes-namespace" not in result.output


@patch("sentry_kube.cli.options.shutil.which", return_value="sentry-options-cli")
@patch("sentry_kube.cli.options.ensure_kubectl", return_value="kubectl")
@patch("sentry_kube.cli.options.subprocess.run")
@patch("sentry_kube.cli.options.list_clusters_for_customer")
@patch("sentry_kube.cli.options.Config")
def test_set_fetches_schemas_when_no_snapshot_is_supplied(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
    _mock_schema_cli: MagicMock,
    tmp_path: Path,
) -> None:
    _mock_clusters(mock_config, mock_list_clusters)
    repos_config = tmp_path / "repos.json"
    repos_config.write_text("{}")
    mock_run.side_effect = [
        _success(),  # sentry-options-cli fetch-schemas
        _success("yes\n"),
        _configmap("1", {"sample-rate": 1.0}),
    ]

    result = CliRunner().invoke(
        options,
        [
            "set",
            "sample-rate",
            "false",
            "--include",
            "us",
            "--service",
            "getsentry",
            "--repos-config",
            str(repos_config),
        ],
    )

    assert result.exit_code == 0, result.output
    fetch_command = mock_run.call_args_list[0].args[0]
    assert fetch_command[:3] == ["sentry-options-cli", "--quiet", "fetch-schemas"]
    assert "--config" in fetch_command
    assert "--out" in fetch_command


@patch("sentry_kube.cli.options.ensure_kubectl", return_value="kubectl")
@patch("sentry_kube.cli.options.subprocess.run")
@patch("sentry_kube.cli.options.list_clusters_for_customer")
@patch("sentry_kube.cli.options.Config")
def test_dry_run_preflights_every_relevant_configmap_without_patching(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
) -> None:
    _mock_clusters(mock_config, mock_list_clusters)
    mock_run.side_effect = [
        _success("yes\n"),
        _configmap("1", {"sample-rate": 1.0}),
        _success("yes\n"),
        _configmap("2", {"sample-rate": 1.0}),
        _success("yes\n"),
        _configmap("3", {"sample-rate": 1.0}),
    ]

    result = CliRunner().invoke(
        options,
        [
            "set",
            "sample-rate",
            "false",
            "--schemas",
            "schemas",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "DRY RUN: would set sample-rate=false in 3 ConfigMaps" in result.output
    assert "sentry-options-getsentry-control-silo" in result.output
    assert result.output.index("control/default/getsentry-control") < result.output.index(
        "us/default/getsentry"
    )
    access_checks = [
        invocation.args[0]
        for invocation in mock_run.call_args_list
        if "can-i" in invocation.args[0]
    ]
    assert len(access_checks) == 3
    assert all(
        command[command.index("can-i") + 1] == "patch" for command in access_checks
    )
    assert not any(
        _is_configmap_patch(args.args[0]) for args in mock_run.call_args_list
    )


@patch("sentry_kube.cli.options.ensure_kubectl", return_value="kubectl")
@patch("sentry_kube.cli.options.subprocess.run")
@patch("sentry_kube.cli.options.list_clusters_for_customer")
@patch("sentry_kube.cli.options.Config")
def test_apply_patches_after_preflight_with_resource_version(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
) -> None:
    _mock_clusters(mock_config, mock_list_clusters)
    mock_config.return_value.silo_regions["us"].aliases = ["saas"]
    mock_run.side_effect = [
        _success("yes\n"),
        _configmap("7", {"sample-rate": 1.0, "unrelated-option": "preserved"}),
        _success(),
    ]

    result = CliRunner().invoke(
        options,
        [
            "set",
            "sample-rate",
            "false",
            "--schemas",
            "schemas",
            "--include",
            "saas",
            "--service",
            "getsentry",
            "--apply",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "APPLIED: set sample-rate=false in 1 ConfigMap" in result.output
    patch_command = mock_run.call_args_list[-1].args[0]
    assert patch_command[patch_command.index("patch") + 1] == "configmap"
    patch_data = json.loads(patch_command[patch_command.index("--patch") + 1])
    assert patch_data[0] == {
        "op": "test",
        "path": "/metadata/resourceVersion",
        "value": "7",
    }
    updated_values = json.loads(patch_data[1]["value"])
    assert updated_values["options"] == {
        "sample-rate": False,
        "unrelated-option": "preserved",
    }
    assert updated_values["generated_at"].endswith("Z")
    assert "." in updated_values["generated_at"]
    assert patch_data[2] == {
        "op": "replace",
        "path": "/metadata/annotations/generated_at",
        "value": updated_values["generated_at"],
    }


@patch("sentry_kube.cli.options.ensure_kubectl", return_value="kubectl")
@patch("sentry_kube.cli.options.subprocess.run")
@patch("sentry_kube.cli.options.list_clusters_for_customer")
@patch("sentry_kube.cli.options.Config")
def test_failed_preflight_prevents_every_patch(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
) -> None:
    _mock_clusters(mock_config, mock_list_clusters)
    mock_run.side_effect = [
        _success("yes\n"),
        _configmap("1", {"sample-rate": 1.0}),
        _success("no\n"),
        _success("yes\n"),
        _configmap("3", {"sample-rate": 1.0}),
    ]

    result = CliRunner().invoke(
        options,
        [
            "set",
            "sample-rate",
            "false",
            "--schemas",
            "schemas",
            "--apply",
        ],
    )

    assert result.exit_code != 0
    assert "us/default/getsentry: cannot patch ConfigMap" in result.output
    assert not any(
        _is_configmap_patch(args.args[0]) for args in mock_run.call_args_list
    )
    assert mock_run.call_count == 5
    assert mock_run.call_args_list[-1] == call(
        [
            "kubectl",
            "--context",
            "us-context",
            "--namespace",
            "default",
            "get",
            "configmap",
            "sentry-options-getsentry-control-silo",
            "--output=json",
        ],
        capture_output=True,
        check=False,
        text=True,
    )


@pytest.mark.parametrize("value", ("NaN", "1e999"))
def test_set_rejects_non_standard_json_before_reading_any_cluster(value: str) -> None:
    result = CliRunner().invoke(
        options,
        [
            "set",
            "sample-rate",
            value,
            "--schemas",
            "schemas",
        ],
    )

    assert result.exit_code != 0
    assert "must be valid JSON" in result.output


@patch("sentry_kube.cli.options.Config")
def test_set_rejects_an_unknown_region_before_reading_any_cluster(
    mock_config: MagicMock,
) -> None:
    mock_config.return_value.silo_regions = {
        "us": MagicMock(k8s_config="us-config", aliases=["saas"]),
    }

    result = CliRunner().invoke(
        options,
        [
            "set",
            "sample-rate",
            "false",
            "--schemas",
            "schemas",
            "--include",
            "not-a-region",
        ],
    )

    assert result.exit_code != 0
    assert "Unknown region(s): not-a-region" in result.output


def test_set_rejects_combining_included_and_excluded_regions() -> None:
    result = CliRunner().invoke(
        options,
        [
            "set",
            "sample-rate",
            "false",
            "--schemas",
            "schemas",
            "--include",
            "us",
            "--exclude",
            "control",
        ],
    )

    assert result.exit_code != 0
    assert "Use either --include or --exclude, not both" in result.output


@patch("sentry_kube.cli.options.ensure_kubectl", return_value="kubectl")
@patch("sentry_kube.cli.options.subprocess.run")
@patch("sentry_kube.cli.options.list_clusters_for_customer")
@patch("sentry_kube.cli.options.Config")
def test_set_excludes_requested_regions_from_the_default_fleet_scope(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
) -> None:
    _mock_clusters(mock_config, mock_list_clusters)
    mock_run.side_effect = [
        _success("yes\n"),
        _configmap("1", {"sample-rate": 1.0}),
    ]

    result = CliRunner().invoke(
        options,
        [
            "set",
            "sample-rate",
            "false",
            "--schemas",
            "schemas",
            "--exclude",
            "control",
            "--service",
            "getsentry-control",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "in 1 ConfigMap" in result.output
    assert "us/default/getsentry-control" in result.output
    assert "control/default/getsentry-control" not in result.output


@patch("sentry_kube.cli.options.ensure_kubectl", return_value="kubectl")
@patch("sentry_kube.cli.options.subprocess.run")
@patch("sentry_kube.cli.options.list_clusters_for_customer")
@patch("sentry_kube.cli.options.Config")
def test_set_preflight_requires_the_writer_generated_at_annotation(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
) -> None:
    _mock_clusters(mock_config, mock_list_clusters)
    mock_run.side_effect = [
        _success("yes\n"),
        _configmap("1", {"sample-rate": 1.0}, include_generated_at_annotation=False),
    ]

    result = CliRunner().invoke(
        options,
        [
            "set",
            "sample-rate",
            "false",
            "--schemas",
            "schemas",
            "--include",
            "us",
            "--service",
            "getsentry",
            "--apply",
        ],
    )

    assert result.exit_code != 0
    assert "has no generated_at annotation" in result.output
    assert not any(
        _is_configmap_patch(args.args[0]) for args in mock_run.call_args_list
    )


@patch("sentry_kube.cli.options.ensure_kubectl", return_value="kubectl")
@patch("sentry_kube.cli.options.subprocess.run")
@patch("sentry_kube.cli.options.list_clusters_for_customer")
@patch("sentry_kube.cli.options.Config")
def test_set_preflight_requires_the_values_generated_at_timestamp(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
) -> None:
    _mock_clusters(mock_config, mock_list_clusters)
    mock_run.side_effect = [
        _success("yes\n"),
        _configmap("1", {"sample-rate": 1.0}, include_generated_at_value=False),
    ]

    result = CliRunner().invoke(
        options,
        [
            "set",
            "sample-rate",
            "false",
            "--schemas",
            "schemas",
            "--include",
            "us",
            "--service",
            "getsentry",
            "--apply",
        ],
    )

    assert result.exit_code != 0
    assert "values.json has no generated_at timestamp" in result.output
    assert not any(
        _is_configmap_patch(args.args[0]) for args in mock_run.call_args_list
    )


@patch("sentry_kube.cli.options.Config")
def test_set_validates_the_schema_before_discovering_targets(
    mock_config: MagicMock, mock_schema_validation: MagicMock
) -> None:
    mock_schema_validation.side_effect = click.ClickException(
        "schema validation failed; no clusters were contacted"
    )

    result = CliRunner().invoke(
        options,
        [
            "set",
            "sample-rate",
            "false",
            "--schemas",
            "schemas",
        ],
    )

    assert result.exit_code != 0
    assert "schema validation failed" in result.output
    mock_schema_validation.assert_called_once_with(
        Path("schemas"), "sample-rate", False
    )
    mock_config.assert_not_called()


@patch("sentry_kube.cli.options.ensure_kubectl", return_value="kubectl")
@patch("sentry_kube.cli.options.subprocess.run")
@patch("sentry_kube.cli.options.list_clusters_for_customer")
@patch("sentry_kube.cli.options.Config")
def test_get_reads_the_option_from_each_selected_configmap(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
) -> None:
    _mock_clusters(mock_config, mock_list_clusters)
    mock_run.side_effect = [
        _configmap("1", {"sample-rate": False}),
    ]

    result = CliRunner().invoke(
        options,
        [
            "get",
            "sample-rate",
            "--include",
            "us",
            "--service",
            "getsentry",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "us/default/getsentry: false" in result.output
    assert mock_run.call_args.args[0] == [
        "kubectl",
        "--context",
        "us-context",
        "--namespace",
        "default",
        "get",
        "configmap",
        "sentry-options-getsentry",
        "--output=json",
    ]
    assert not any(
        _is_configmap_patch(args.args[0]) for args in mock_run.call_args_list
    )


@patch("sentry_kube.cli.options.ensure_kubectl", return_value="kubectl")
@patch("sentry_kube.cli.options.subprocess.run")
@patch("sentry_kube.cli.options.list_clusters_for_customer")
@patch("sentry_kube.cli.options.Config")
def test_get_prints_each_target_region_and_value(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
) -> None:
    _mock_clusters(mock_config, mock_list_clusters)
    mock_run.side_effect = [
        _configmap("1", {"sample-rate": False}),
        _configmap("2", {"sample-rate": True}),
        _configmap("3", {}),
    ]

    result = CliRunner().invoke(options, ["get", "sample-rate"])

    assert result.exit_code == 0, result.output
    assert "control/default/getsentry-control: false" in result.output
    assert "us/default/getsentry: true" in result.output
    assert "us/default/getsentry-control: <unset>" in result.output
