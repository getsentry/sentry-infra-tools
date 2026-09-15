import json
import subprocess
from dataclasses import dataclass
from unittest.mock import MagicMock, call, patch

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
) -> subprocess.CompletedProcess[str]:
    metadata: dict[str, object] = {"resourceVersion": resource_version}
    if include_generated_at_annotation:
        metadata["annotations"] = {"generated_at": "old"}
    return _success(
        json.dumps(
            {
                "metadata": metadata,
                "data": {
                    "values.json": json.dumps(
                        {"options": options, "generated_at": "old"}
                    )
                },
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
    assert "--region" in result.output
    assert "--exclude-region" in result.output
    assert "--apply" in result.output


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
        _success("yes\n"),
        _configmap("1", {"sample-rate": 1.0}),
        _success("yes\n"),
        _success("yes\n"),
        _configmap("2", {"sample-rate": 1.0}),
        _success("yes\n"),
        _success("yes\n"),
        _configmap("3", {"sample-rate": 1.0}),
    ]

    result = CliRunner().invoke(
        options, ["set", "--option", "sample-rate", "--value", "false"]
    )

    assert result.exit_code == 0, result.output
    assert "DRY RUN: would set sample-rate=false in 3 ConfigMaps" in result.output
    assert "sentry-options-getsentry-control-silo" in result.output
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
        _success("yes\n"),
        _configmap("7", {"sample-rate": 1.0, "unrelated-option": "preserved"}),
        _success(),
    ]

    result = CliRunner().invoke(
        options,
        [
            "set",
            "--region",
            "saas",
            "--service",
            "getsentry",
            "--option",
            "sample-rate",
            "--value",
            "false",
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
        _success("yes\n"),
        _configmap("1", {"sample-rate": 1.0}),
        _success("no\n"),
        _success("yes\n"),
        _success("yes\n"),
        _configmap("3", {"sample-rate": 1.0}),
    ]

    result = CliRunner().invoke(
        options,
        [
            "set",
            "--option",
            "sample-rate",
            "--value",
            "false",
            "--apply",
        ],
    )

    assert result.exit_code != 0
    assert "us/default/getsentry-control: cannot get ConfigMap" in result.output
    assert not any(
        _is_configmap_patch(args.args[0]) for args in mock_run.call_args_list
    )
    assert mock_run.call_count == 7
    assert mock_run.call_args_list[-1] == call(
        [
            "kubectl",
            "--context",
            "control-context",
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


def test_set_rejects_non_standard_json_before_reading_any_cluster() -> None:
    result = CliRunner().invoke(
        options, ["set", "--option", "sample-rate", "--value", "NaN"]
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
            "--region",
            "not-a-region",
            "--option",
            "sample-rate",
            "--value",
            "false",
        ],
    )

    assert result.exit_code != 0
    assert "Unknown region(s): not-a-region" in result.output


def test_set_rejects_combining_included_and_excluded_regions() -> None:
    result = CliRunner().invoke(
        options,
        [
            "set",
            "--region",
            "us",
            "--exclude-region",
            "control",
            "--option",
            "sample-rate",
            "--value",
            "false",
        ],
    )

    assert result.exit_code != 0
    assert "Use either --region or --exclude-region, not both" in result.output


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
        _success("yes\n"),
        _configmap("1", {"sample-rate": 1.0}),
    ]

    result = CliRunner().invoke(
        options,
        [
            "set",
            "--exclude-region",
            "control",
            "--service",
            "getsentry-control",
            "--option",
            "sample-rate",
            "--value",
            "false",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "in 1 ConfigMaps" in result.output
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
        _success("yes\n"),
        _configmap("1", {"sample-rate": 1.0}, include_generated_at_annotation=False),
    ]

    result = CliRunner().invoke(
        options,
        [
            "set",
            "--region",
            "us",
            "--service",
            "getsentry",
            "--option",
            "sample-rate",
            "--value",
            "false",
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
def test_get_reads_the_option_from_each_selected_configmap(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
) -> None:
    _mock_clusters(mock_config, mock_list_clusters)
    mock_run.side_effect = [
        _success("yes\n"),
        _configmap("1", {"sample-rate": False}),
    ]

    result = CliRunner().invoke(
        options,
        [
            "get",
            "--region",
            "us",
            "--service",
            "getsentry",
            "--option",
            "sample-rate",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "us/default/getsentry: false" in result.output
    assert not any(
        _is_configmap_patch(args.args[0]) for args in mock_run.call_args_list
    )
