import hashlib
import json
import subprocess
import threading
from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import click
import pytest
from click.testing import CliRunner

import sentry_kube.cli.options as options_module
from sentry_kube.cli import main
from sentry_kube.cli.options import _validate_against_schema, options


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


def _kubectl_side_effect(
    *,
    can_i: dict[tuple[str, str], subprocess.CompletedProcess[str]] | None = None,
    get: dict[tuple[str, str], subprocess.CompletedProcess[str]] | None = None,
    patch: dict[tuple[str, str], subprocess.CompletedProcess[str]] | None = None,
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Build a subprocess.run side_effect for `set`'s preflight/apply, keyed
    by (--context, configmap name) per verb.

    `set` now preflights and applies every target concurrently, so a
    positional side_effect list races against thread scheduling. Keying by
    the actual command arguments keeps the test deterministic regardless of
    which thread runs first.
    """

    can_i = can_i or {}
    get = get or {}
    patch = patch or {}

    def side_effect(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        context = command[command.index("--context") + 1]
        if "can-i" in command:
            configmap_name = next(
                arg.split("/", 1)[1] for arg in command if arg.startswith("configmap/")
            )
            return can_i[(context, configmap_name)]
        if _is_configmap_patch(command):
            configmap_name = command[command.index("configmap") + 1]
            return patch[(context, configmap_name)]
        configmap_name = command[command.index("configmap") + 1]
        return get[(context, configmap_name)]

    return side_effect


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


@pytest.fixture(autouse=True)
def mock_gcloud_reauth() -> Generator[MagicMock, None, None]:
    """Never shell out to the real gcloud CLI from a test."""
    with patch("sentry_kube.cli.options.ensure_gcloud_reauthed") as reauth:
        yield reauth


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


@pytest.mark.parametrize(
    ("included", "excluded", "expected_regions"),
    [
        ((), (), {"us", "control"}),
        ((), ("control",), {"us"}),
        ((), ("us2",), {"us", "control"}),
        (("us2",), (), {"us2"}),
        (("us", "us2"), (), {"us", "us2"}),
    ],
)
def test_options_targets_exclude_retired_us2_unless_explicitly_included(
    included: tuple[str, ...],
    excluded: tuple[str, ...],
    expected_regions: set[str],
) -> None:
    config = MagicMock()
    config.silo_regions = {
        region: MagicMock(k8s_config=f"{region}-config", aliases=[])
        for region in ("us", "control", "us2")
    }
    with patch("sentry_kube.cli.options.list_clusters_for_customer") as list_clusters:
        list_clusters.side_effect = lambda k8s_config: [
            FakeCluster("default", ["getsentry"], {"context": k8s_config})
        ]

        targets = options_module._find_targets(
            config, included, excluded, ("getsentry",)
        )

    assert {target.region for target in targets} == expected_regions
    assert {entry.args[0] for entry in list_clusters.call_args_list} == {
        f"{region}-config" for region in expected_regions
    }


@patch("sentry_kube.cli.options._fetch_schemas")
@patch("sentry_kube.cli.options.ensure_kubectl", return_value="kubectl")
@patch("sentry_kube.cli.options.subprocess.run")
@patch("sentry_kube.cli.options.list_clusters_for_customer")
@patch("sentry_kube.cli.options.Config")
@pytest.mark.parametrize("refresh", [False, True])
def test_set_fetches_schemas_when_no_snapshot_is_supplied(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
    mock_fetch_schemas: MagicMock,
    tmp_path: Path,
    refresh: bool,
) -> None:
    _mock_clusters(mock_config, mock_list_clusters)
    repos_config = tmp_path / "repos.json"
    repos_config.write_text("{}")
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
            "--include",
            "us",
            "--service",
            "getsentry",
            "--repos-config",
            str(repos_config),
            *(["--refresh"] if refresh else []),
        ],
    )

    assert result.exit_code == 0, result.output
    assert mock_fetch_schemas.call_args.args[0] == repos_config
    assert mock_fetch_schemas.call_args.args[1].name == "schemas"
    assert mock_fetch_schemas.call_args.kwargs == {"refresh": refresh}


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
    targets = (
        ("control-context", "sentry-options-getsentry-control-silo", "1"),
        ("us-context", "sentry-options-getsentry", "2"),
        ("us-context", "sentry-options-getsentry-control-silo", "3"),
    )
    mock_run.side_effect = _kubectl_side_effect(
        can_i={(context, configmap): _success("yes\n") for context, configmap, _ in targets},
        get={
            (context, configmap): _configmap(version, {"sample-rate": 1.0})
            for context, configmap, version in targets
        },
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

    assert result.exit_code == 0, result.output
    assert "DRY RUN: would set sample-rate=false in 3 ConfigMaps" in result.output
    assert "sentry-options-getsentry-control-silo" in result.output
    assert result.output.count("sample-rate 1.0 -> false") == 3
    # The final plan section lists targets in a fixed, sorted order even
    # though preflight itself ran them concurrently.
    plan = result.output.split("DRY RUN:", 1)[1]
    assert plan.index("control/default/getsentry-control") < plan.index(
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
def test_dry_run_announces_that_no_changes_will_be_made(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
) -> None:
    _mock_clusters(mock_config, mock_list_clusters)
    mock_run.side_effect = _kubectl_side_effect(
        can_i={("us-context", "sentry-options-getsentry"): _success("yes\n")},
        get={("us-context", "sentry-options-getsentry"): _configmap("1", {})},
    )

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
        ],
    )

    assert result.exit_code == 0, result.output
    assert "dry-run; no changes will be made" in result.output


@patch("sentry_kube.cli.options.ensure_kubectl", return_value="kubectl")
@patch("sentry_kube.cli.options.subprocess.run")
@patch("sentry_kube.cli.options.list_clusters_for_customer")
@patch("sentry_kube.cli.options.Config")
def test_apply_does_not_announce_a_dry_run(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
) -> None:
    _mock_clusters(mock_config, mock_list_clusters)
    mock_run.side_effect = _kubectl_side_effect(
        can_i={("us-context", "sentry-options-getsentry"): _success("yes\n")},
        get={("us-context", "sentry-options-getsentry"): _configmap("1", {})},
        patch={("us-context", "sentry-options-getsentry"): _success()},
    )

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

    assert result.exit_code == 0, result.output
    assert "dry-run" not in result.output.lower()
    assert "us: sample-rate <unset> -> false" in result.output


@patch("sentry_kube.cli.options.ensure_kubectl", return_value="kubectl")
@patch("sentry_kube.cli.options.subprocess.run")
@patch("sentry_kube.cli.options.list_clusters_for_customer")
@patch("sentry_kube.cli.options.Config")
def test_set_echoes_the_exact_kubectl_commands_it_runs(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
) -> None:
    _mock_clusters(mock_config, mock_list_clusters)
    mock_run.side_effect = _kubectl_side_effect(
        can_i={("us-context", "sentry-options-getsentry"): _success("yes\n")},
        get={("us-context", "sentry-options-getsentry"): _configmap("1", {})},
    )

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
        ],
    )

    assert result.exit_code == 0, result.output
    assert (
        "+ kubectl --context us-context --namespace default auth can-i patch "
        "configmap/sentry-options-getsentry"
    ) in result.output
    assert (
        "+ kubectl --context us-context --namespace default get configmap "
        "sentry-options-getsentry --output=json"
    ) in result.output


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
    patch_data: list[dict[str, object]] = []

    preflight_results = iter([
        _success("yes\n"),
        _configmap("7", {"sample-rate": 1.0, "unrelated-option": "preserved"}),
    ])

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if _is_configmap_patch(command):
            patch_path = Path(command[command.index("--patch-file") + 1])
            patch_data.extend(json.loads(patch_path.read_text()))
            return _success()
        return next(preflight_results)

    mock_run.side_effect = run

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
    assert "--patch-file" in patch_command
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
    mock_run.side_effect = _kubectl_side_effect(
        can_i={
            ("control-context", "sentry-options-getsentry-control-silo"): _success("yes\n"),
            ("us-context", "sentry-options-getsentry"): _success("no\n"),
            ("us-context", "sentry-options-getsentry-control-silo"): _success("yes\n"),
        },
        get={
            ("control-context", "sentry-options-getsentry-control-silo"): _configmap(
                "1", {"sample-rate": 1.0}
            ),
            ("us-context", "sentry-options-getsentry-control-silo"): _configmap(
                "3", {"sample-rate": 1.0}
            ),
        },
    )

    result = CliRunner().invoke(
        options,
        [
            "set",
            "sample-rate",
            "false",
            "--schemas",
            "schemas",
            "--all-regions",
            "--apply",
        ],
    )

    assert result.exit_code != 0
    assert "us/default/getsentry: cannot patch ConfigMap" in result.output
    assert not any(
        _is_configmap_patch(args.args[0]) for args in mock_run.call_args_list
    )
    # us/getsentry's denied "can-i" short-circuits before any "get", so 5
    # total calls: 2 can-i + 1 can-i-denied + 2 get. Order is not guaranteed
    # since preflight now runs every target concurrently.
    assert mock_run.call_count == 5
    assert call(
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
    ) in mock_run.call_args_list


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
def test_set_treats_unquoted_text_as_a_json_string(
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
            "foo",
            "--schemas",
            "schemas",
        ],
    )

    assert result.exit_code != 0
    mock_schema_validation.assert_called_once_with(
        Path("schemas"), "sample-rate", "foo"
    )
    mock_config.assert_not_called()


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


def test_set_apply_without_scope_requires_all_regions_confirmation() -> None:
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
    assert "--all-regions" in result.output


@patch("sentry_kube.cli.options.ensure_kubectl", return_value="kubectl")
@patch("sentry_kube.cli.options.subprocess.run")
@patch("sentry_kube.cli.options.list_clusters_for_customer")
@patch("sentry_kube.cli.options.Config")
def test_set_all_regions_flag_confirms_a_fleet_wide_apply(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
    mock_gcloud_reauth: MagicMock,
) -> None:
    _mock_clusters(mock_config, mock_list_clusters)
    targets = (
        ("control-context", "sentry-options-getsentry-control-silo"),
        ("us-context", "sentry-options-getsentry"),
        ("us-context", "sentry-options-getsentry-control-silo"),
    )
    mock_run.side_effect = _kubectl_side_effect(
        can_i={key: _success("yes\n") for key in targets},
        get={key: _configmap(str(i), {"sample-rate": 1.0}) for i, key in enumerate(targets)},
        patch={key: _success() for key in targets},
    )

    result = CliRunner().invoke(
        options,
        [
            "set",
            "sample-rate",
            "false",
            "--schemas",
            "schemas",
            "--all-regions",
            "--apply",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "APPLIED: set sample-rate=false in 3 ConfigMaps" in result.output
    # One reauth up front covers both the preflight and apply fan-outs.
    mock_gcloud_reauth.assert_called_once()


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


@pytest.mark.parametrize(
    ("configmap_kwargs", "expected_error"),
    [
        (
            {"include_generated_at_annotation": False},
            "has no generated_at annotation",
        ),
        (
            {"include_generated_at_value": False},
            "values.json has no generated_at timestamp",
        ),
    ],
)
@patch("sentry_kube.cli.options.ensure_kubectl", return_value="kubectl")
@patch("sentry_kube.cli.options.subprocess.run")
@patch("sentry_kube.cli.options.list_clusters_for_customer")
@patch("sentry_kube.cli.options.Config")
def test_set_preflight_requires_generated_at_fields(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
    configmap_kwargs: dict[str, bool],
    expected_error: str,
) -> None:
    _mock_clusters(mock_config, mock_list_clusters)
    mock_run.side_effect = [
        _success("yes\n"),
        _configmap("1", {"sample-rate": 1.0}, **configmap_kwargs),
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
    assert expected_error in result.output
    assert not any(
        _is_configmap_patch(args.args[0]) for args in mock_run.call_args_list
    )


@patch("sentry_kube.cli.options.Config")
def test_set_schema_loading_failure_stops_before_discovering_targets(
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


def test_set_validates_before_warming_cluster_access(
    mock_schema_validation: MagicMock,
) -> None:
    events: list[str] = []

    def _validate(*_args: object, **_kwargs: object) -> None:
        events.append("validate")

    def _warm_cluster_access() -> str:
        events.append("access")
        return "kubectl"

    def _select_targets(*_args: object, **_kwargs: object) -> list[object]:
        events.append("targets")
        return []

    mock_schema_validation.side_effect = _validate

    with (
        patch.object(
            options_module, "_ensure_cluster_access", side_effect=_warm_cluster_access
        ),
        patch.object(options_module, "_selected_targets", side_effect=_select_targets),
    ):
        result = CliRunner().invoke(
            options,
            ["set", "sample-rate", "false", "--schemas", "schemas"],
        )

    assert result.exit_code == 0, result.output
    assert events == ["validate", "access", "targets"]


@patch("sentry_kube.cli.options.ensure_kubectl", return_value="kubectl")
@patch("sentry_kube.cli.options.subprocess.run")
@patch("sentry_kube.cli.options.list_clusters_for_customer")
@patch("sentry_kube.cli.options.Config")
def test_get_reads_the_option_from_each_selected_configmap(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
    mock_gcloud_reauth: MagicMock,
) -> None:
    _mock_clusters(mock_config, mock_list_clusters)
    mock_run.side_effect = _kubectl_side_effect(
        get={
            ("us-context", "sentry-options-getsentry"): _configmap(
                "1", {"sample-rate": False}
            ),
        }
    )

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
    assert "us: false" in result.output
    # A single synchronous reauth happens before the concurrent fan-out, so
    # parallel kubectl calls never race on gcloud's token cache.
    mock_gcloud_reauth.assert_called_once()
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
def test_get_verbose_prints_configmap_name_and_context(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
) -> None:
    _mock_clusters(mock_config, mock_list_clusters)
    mock_run.side_effect = _kubectl_side_effect(
        get={
            ("us-context", "sentry-options-getsentry"): _configmap(
                "1", {"sample-rate": False}
            ),
        }
    )

    result = CliRunner().invoke(
        options,
        [
            "get",
            "sample-rate",
            "--include",
            "us",
            "--service",
            "getsentry",
            "--verbose",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "us/default/getsentry: false" in result.output
    assert "sentry-options-getsentry; us-context" in result.output


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
    mock_run.side_effect = _kubectl_side_effect(
        get={
            ("control-context", "sentry-options-getsentry-control-silo"): _configmap(
                "1", {"sample-rate": False}
            ),
            ("us-context", "sentry-options-getsentry"): _configmap(
                "2", {"sample-rate": True}
            ),
            ("us-context", "sentry-options-getsentry-control-silo"): _configmap(
                "3", {}
            ),
        }
    )

    result = CliRunner().invoke(options, ["get", "sample-rate"])

    assert result.exit_code == 0, result.output
    assert "control/control-silo: false" in result.output
    assert "us: true" in result.output
    assert "us/control-silo: <unset>" in result.output


@patch("sentry_kube.cli.options.ensure_kubectl", return_value="kubectl")
@patch("sentry_kube.cli.options.subprocess.run")
@patch("sentry_kube.cli.options.list_clusters_for_customer")
@patch("sentry_kube.cli.options.Config")
def test_get_prints_already_read_targets_when_a_later_one_errors(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
) -> None:
    _mock_clusters(mock_config, mock_list_clusters)
    mock_run.side_effect = _kubectl_side_effect(
        get={
            ("control-context", "sentry-options-getsentry-control-silo"): _configmap(
                "1", {"sample-rate": False}
            ),
            ("us-context", "sentry-options-getsentry"): subprocess.CompletedProcess(
                [], 1, "", "error: context does not exist"
            ),
            ("us-context", "sentry-options-getsentry-control-silo"): _configmap(
                "3", {}
            ),
        }
    )

    result = CliRunner().invoke(options, ["get", "sample-rate"])

    assert result.exit_code != 0
    assert "control/control-silo: false" in result.output
    assert "us/control-silo: <unset>" in result.output
    assert "Could not read every selected ConfigMap" in result.output


def _write_fake_schema(schemas_dir: Path) -> None:
    namespace_dir = schemas_dir / "getsentry"
    namespace_dir.mkdir(parents=True)
    (namespace_dir / "schema.json").write_text("{}")


def test_fetch_schemas_caches_a_fresh_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache_root = tmp_path / "cache"
    output = tmp_path / "output"
    repos_bytes = b'{"repos": {}}'

    with (
        patch.object(options_module, "_options_cache_root", return_value=cache_root),
        patch.object(
            options_module,
            "_repos_config_bytes",
            return_value=(repos_bytes, "test source"),
        ),
        patch.object(
            options_module,
            "_fetch_schemas_with_client",
            side_effect=lambda _config, out: _write_fake_schema(out),
        ),
    ):
        options_module._fetch_schemas(None, output)

    assert (output / "getsentry" / "schema.json").is_file()

    checksum = hashlib.sha256(repos_bytes).hexdigest()
    cached_snapshot = cache_root / "by-checksum" / checksum / "snapshot"
    assert (cached_snapshot / "getsentry" / "schema.json").is_file()
    assert json.loads((cache_root / "latest.json").read_text())["checksum"] == checksum
    assert "checksum" not in capsys.readouterr().err


def test_fetch_schemas_reuses_snapshot_on_repeated_calls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache_root = tmp_path / "cache"
    with (
        patch.object(options_module, "_options_cache_root", return_value=cache_root),
        patch.object(
            options_module,
            "_repos_config_bytes",
            return_value=(b'{"repos": {}}', "test source"),
        ),
        patch.object(
            options_module,
            "_fetch_schemas_with_client",
            side_effect=lambda _config, out: _write_fake_schema(out),
        ) as fetch,
    ):
        options_module._fetch_schemas(None, tmp_path / "first")
        capsys.readouterr()
        options_module._fetch_schemas(None, tmp_path / "second")

    fetch.assert_called_once()
    assert (tmp_path / "second" / "getsentry" / "schema.json").is_file()
    assert "Using cached schema snapshot" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("seconds", "description"),
    [
        (0, "just now"),
        (1, "1 second ago"),
        (90, "1 minute ago"),
        (120, "2 minutes ago"),
        (3600, "1 hour ago"),
        (172800, "2 days ago"),
    ],
)
def test_cached_schema_message_reports_age_and_refresh_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], seconds: int, description: str
) -> None:
    checksum = "test-checksum"
    entry_dir = tmp_path / "by-checksum" / checksum
    _write_fake_schema(entry_dir / "snapshot")
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    fetched_at = (now - timedelta(seconds=seconds)).isoformat()
    (entry_dir / "meta.json").write_text(json.dumps({"fetched_at": fetched_at}))
    fresh = seconds < 3600
    with patch.object(options_module, "datetime") as clock:
        clock.now.return_value = now
        clock.fromisoformat.side_effect = datetime.fromisoformat
        assert options_module._use_cached_schemas(
            tmp_path, checksum, tmp_path / "output", fresh_only=fresh
        )

    message = capsys.readouterr().err
    assert description in message
    assert fetched_at not in message
    assert ("Pass --refresh" in message) == fresh


@pytest.mark.parametrize("color", [False, True])
def test_options_output_colors_include_commands_from_worker_threads(color: bool) -> None:
    @click.command()
    def command() -> None:
        options_module._report("dry-run; no changes will be made (pass --apply to apply)")
        options_module._report("Fetch failed: offline", fg="red")
        options_module._report("Fetched and cached fresh schema snapshot.", fg="green")
        with patch.object(options_module.subprocess, "run", return_value=_success()):
            options_module._fan_out(["one"], lambda _: options_module._run(["kubectl"]))

    result = CliRunner().invoke(command, color=color)
    assert result.exit_code == 0, result.output
    for message, fg in [
        ("dry-run; no changes will be made (pass --apply to apply)", "yellow"),
        ("Fetch failed: offline", "red"),
        ("Fetched and cached fresh schema snapshot.", "green"),
        ("+ kubectl", "bright_black"),
    ]:
        assert (click.style(message, fg=fg) if color else message) in result.output
    if not color:
        assert "\x1b[" not in result.output


@pytest.mark.parametrize("color", [False, True])
def test_options_usage_errors_are_red_and_keep_parameter_details(color: bool) -> None:
    result = CliRunner().invoke(options, ["set", " ", "1"], color=color)
    assert result.exit_code == 2
    message = "Invalid value for 'OPTION': must not be blank or have surrounding whitespace"
    assert (click.style(message, fg="red") if color else message) in result.output
    assert "Usage:" in result.output
    if not color:
        assert "\x1b[" not in result.output


@pytest.mark.parametrize("color", [False, True])
def test_options_errors_are_red(color: bool, mock_schema_validation: MagicMock) -> None:
    mock_schema_validation.side_effect = click.ClickException("invalid schema")
    result = CliRunner().invoke(
        options, ["set", "test", "1", "--schemas", "schemas"], color=color
    )
    assert result.exit_code == 1
    error = "Error: invalid schema"
    assert (click.style(error, fg="red") if color else error) in result.output
    if not color:
        assert "\x1b[" not in result.output


@pytest.mark.parametrize(
    ("age_seconds", "refresh", "changed_config", "should_fetch"),
    [
        (3599, False, False, False),
        (3600, False, False, True),
        (-60, False, False, True),
        (0, True, False, True),
        (0, False, True, True),
    ],
)
def test_fetch_schemas_cache_expiry_and_bypass(
    tmp_path: Path,
    age_seconds: int,
    refresh: bool,
    changed_config: bool,
    should_fetch: bool,
) -> None:
    cache_root = tmp_path / "cache"
    repos_bytes = b'{"repos": {}}'
    checksum = hashlib.sha256(repos_bytes).hexdigest()
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    entry_dir = cache_root / "by-checksum" / checksum
    _write_fake_schema(entry_dir / "snapshot")
    (entry_dir / "meta.json").write_text(
        json.dumps({"fetched_at": (now - timedelta(seconds=age_seconds)).isoformat()})
    )
    (cache_root / "latest.json").write_text(json.dumps({"checksum": checksum}))
    output = tmp_path / "output"

    with (
        patch.object(options_module, "_options_cache_root", return_value=cache_root),
        patch.object(
            options_module,
            "_repos_config_bytes",
            return_value=(b"{}" if changed_config else repos_bytes, "test source"),
        ),
        patch.object(options_module, "datetime") as clock,
        patch.object(
            options_module,
            "_fetch_schemas_with_client",
            side_effect=lambda _config, out: _write_fake_schema(out),
        ) as fetch,
    ):
        clock.now.return_value = now
        clock.fromisoformat.side_effect = datetime.fromisoformat
        options_module._fetch_schemas(None, output, refresh=refresh)

    assert fetch.call_count == int(should_fetch)
    assert (output / "getsentry" / "schema.json").is_file()


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        "{}",
        "[]",
        '{"fetched_at": "bad"}',
        '{"fetched_at": null}',
        '{"fetched_at": "2026-09-30T00:00:00"}',
    ],
)
def test_fetch_schemas_refreshes_when_cache_age_is_unknown(
    tmp_path: Path, metadata: str | None
) -> None:
    cache_root = tmp_path / "cache"
    repos_bytes = b'{"repos": {}}'
    checksum = hashlib.sha256(repos_bytes).hexdigest()
    entry_dir = cache_root / "by-checksum" / checksum
    _write_fake_schema(entry_dir / "snapshot")
    if metadata is not None:
        (entry_dir / "meta.json").write_text(metadata)

    with (
        patch.object(options_module, "_options_cache_root", return_value=cache_root),
        patch.object(
            options_module,
            "_repos_config_bytes",
            return_value=(repos_bytes, "test source"),
        ),
        patch.object(
            options_module,
            "_fetch_schemas_with_client",
            side_effect=lambda _config, out: _write_fake_schema(out),
        ) as fetch,
    ):
        options_module._fetch_schemas(None, tmp_path / "output")

    fetch.assert_called_once()


def test_fetch_schemas_keeps_fresh_schemas_when_caching_fails(tmp_path: Path) -> None:
    """Caching is an optimization; a write failure there shouldn't discard an
    otherwise-successful fetch already sitting in `output`.
    """

    cache_root = tmp_path / "cache"
    output = tmp_path / "output"
    repos_bytes = b'{"repos": {}}'

    with (
        patch.object(options_module, "_options_cache_root", return_value=cache_root),
        patch.object(
            options_module,
            "_repos_config_bytes",
            return_value=(repos_bytes, "test source"),
        ),
        patch.object(
            options_module,
            "_fetch_schemas_with_client",
            side_effect=lambda _config, out: _write_fake_schema(out),
        ),
        patch.object(
            options_module,
            "_cache_schema_snapshot",
            side_effect=OSError("disk full"),
        ),
    ):
        options_module._fetch_schemas(None, output)

    assert (output / "getsentry" / "schema.json").is_file()


@pytest.mark.parametrize("refresh", [False, True])
def test_fetch_schemas_falls_back_to_matching_cached_checksum_on_failure(
    tmp_path: Path, refresh: bool,
) -> None:
    cache_root = tmp_path / "cache"
    output = tmp_path / "output"
    repos_bytes = b'{"repos": {}}'
    checksum = hashlib.sha256(repos_bytes).hexdigest()

    entry_dir = cache_root / "by-checksum" / checksum
    _write_fake_schema(entry_dir / "snapshot")
    (entry_dir / "meta.json").write_text(
        json.dumps({"checksum": checksum, "fetched_at": "2020-01-01T00:00:00+00:00"})
    )
    (cache_root / "latest.json").write_text(json.dumps({"checksum": checksum}))

    with (
        patch.object(options_module, "_options_cache_root", return_value=cache_root),
        patch.object(
            options_module,
            "_repos_config_bytes",
            return_value=(repos_bytes, "test source"),
        ),
        patch.object(
            options_module,
            "_fetch_schemas_with_client",
            side_effect=click.ClickException("network unreachable"),
        ),
    ):
        options_module._fetch_schemas(None, output, refresh=refresh)

    assert (output / "getsentry" / "schema.json").is_file()


def test_schema_directory_uses_local_snapshot_even_with_refresh(tmp_path: Path) -> None:
    with patch.object(options_module, "_fetch_schemas") as fetch:
        with options_module._schema_directory(tmp_path, None, refresh=True) as snapshot:
            assert snapshot == tmp_path

    fetch.assert_not_called()


def test_fetch_schemas_falls_back_to_cache_when_failed_fetch_left_partial_output(
    tmp_path: Path,
) -> None:
    """A failed fetch can leave `output` partially populated (the client
    writes namespaces as it goes); the cache fallback must still work even
    though `output` already exists.
    """

    cache_root = tmp_path / "cache"
    output = tmp_path / "output"
    repos_bytes = b'{"repos": {}}'
    checksum = hashlib.sha256(repos_bytes).hexdigest()

    entry_dir = cache_root / "by-checksum" / checksum
    _write_fake_schema(entry_dir / "snapshot")
    (entry_dir / "meta.json").write_text(
        json.dumps({"checksum": checksum, "fetched_at": "2020-01-01T00:00:00+00:00"})
    )
    (cache_root / "latest.json").write_text(json.dumps({"checksum": checksum}))

    def _fail_after_partial_write(_config: Path, out: Path) -> None:
        out.mkdir(parents=True)
        (out / "partial-namespace").mkdir()
        raise click.ClickException("network unreachable")

    with (
        patch.object(options_module, "_options_cache_root", return_value=cache_root),
        patch.object(
            options_module,
            "_repos_config_bytes",
            return_value=(repos_bytes, "test source"),
        ),
        patch.object(
            options_module,
            "_fetch_schemas_with_client",
            side_effect=_fail_after_partial_write,
        ),
    ):
        options_module._fetch_schemas(None, output)

    assert (output / "getsentry" / "schema.json").is_file()
    assert not (output / "partial-namespace").exists()


def test_fetch_schemas_falls_back_to_latest_cache_when_repos_json_unavailable(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "cache"
    output = tmp_path / "output"
    other_checksum = "deadbeef" * 8

    entry_dir = cache_root / "by-checksum" / other_checksum
    _write_fake_schema(entry_dir / "snapshot")
    (entry_dir / "meta.json").write_text(
        json.dumps(
            {"checksum": other_checksum, "fetched_at": "2020-01-01T00:00:00+00:00"}
        )
    )
    (cache_root / "latest.json").write_text(json.dumps({"checksum": other_checksum}))

    with (
        patch.object(options_module, "_options_cache_root", return_value=cache_root),
        patch.object(
            options_module,
            "_repos_config_bytes",
            side_effect=click.ClickException("no network"),
        ),
    ):
        options_module._fetch_schemas(None, output)

    assert (output / "getsentry" / "schema.json").is_file()


def test_fetch_schemas_raises_when_fetch_fails_and_no_cache_exists(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "cache"
    output = tmp_path / "output"

    with (
        patch.object(options_module, "_options_cache_root", return_value=cache_root),
        patch.object(
            options_module,
            "_repos_config_bytes",
            return_value=(b'{"repos": {}}', "test source"),
        ),
        patch.object(
            options_module,
            "_fetch_schemas_with_client",
            side_effect=click.ClickException("network unreachable"),
        ),
    ):
        with pytest.raises(click.ClickException):
            options_module._fetch_schemas(None, output)


@pytest.mark.parametrize("apply", [False, True])
@patch("sentry_kube.cli.options.ensure_kubectl", return_value="kubectl")
@patch("sentry_kube.cli.options.subprocess.run")
@patch("sentry_kube.cli.options.list_clusters_for_customer")
@patch("sentry_kube.cli.options.Config")
def test_set_invalid_value_fails_before_cluster_access(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
    mock_schema_validation: MagicMock,
    tmp_path: Path,
    apply: bool,
) -> None:
    namespace = tmp_path / "getsentry"
    namespace.mkdir()
    option_key = "getsentry.options-dual-read-test"
    (namespace / "schema.json").write_text(
        json.dumps({
            "version": "1.0",
            "type": "object",
            "properties": {
                option_key: {"type": "integer", "default": 42, "description": ""},
            },
        })
    )
    mock_schema_validation.side_effect = _validate_against_schema
    arguments = ["set", option_key, "false", "--schemas", str(tmp_path)]
    if apply:
        arguments += ["--apply", "--all-regions"]
    result = CliRunner().invoke(options, arguments)

    assert result.exit_code != 0
    assert f"{option_key} (type: integer)" in result.output
    assert 'is not of type "integer"' in result.output
    assert "No ConfigMaps were read or patched" in result.output
    assert " -> false" not in result.output
    assert mock_run.call_args_list == []
    _mock_kubectl.assert_not_called()
    mock_config.assert_not_called()
    mock_list_clusters.assert_not_called()


def test_set_validates_on_the_main_thread(mock_schema_validation: MagicMock) -> None:
    validation_threads: list[threading.Thread] = []

    def record_thread(*_args: object) -> None:
        validation_threads.append(threading.current_thread())

    mock_schema_validation.side_effect = record_thread
    with (
        patch.object(options_module, "_ensure_cluster_access", return_value="kubectl"),
        patch.object(options_module, "_selected_targets", return_value=[]),
    ):
        result = CliRunner().invoke(
            options, ["set", "sample-rate", "false", "--schemas", "schemas"]
        )
    assert result.exit_code == 0, result.output
    assert validation_threads == [threading.main_thread()]


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({}, "<unset>"),
        ({"option": None}, "null"),
        ({"option": "false"}, '"false"'),
        ({"option": {"enabled": True}}, '{"enabled":true}'),
    ],
)
def test_describe_option_preserves_json_types(
    values: dict[str, object], expected: str
) -> None:
    assert options_module._describe_option(values, "option") == expected


def test_validate_against_schema_reports_type_for_valid_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    namespace = tmp_path / "getsentry"
    namespace.mkdir()
    (namespace / "schema.json").write_text(json.dumps({
        "version": "1.0", "type": "object", "properties": {
            "option": {"type": "integer", "default": 42, "description": ""},
        },
    }))
    _validate_against_schema(tmp_path, "option", 7)
    assert capsys.readouterr().out == "option (type: integer)\n"
    with pytest.raises(click.ClickException, match="No clusters were contacted"):
        _validate_against_schema(tmp_path, "unknown", 7)
    with pytest.raises(click.ClickException, match="No clusters were contacted"):
        _validate_against_schema(tmp_path / "missing", "option", 7)


def test_apply_patch_passes_large_payload_via_temporary_file() -> None:
    target = options_module.ConfigMapTarget("us", "default", "getsentry", "us-context")
    values_json = json.dumps({"options": {"large": '"' * 140_000}})
    prepared = options_module.PreparedPatch(
        target, "sentry-options-getsentry", "7", "now", values_json, "<unset>"
    )
    patch_paths: list[Path] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert "--patch" not in command
        assert max(len(arg.encode()) for arg in command) < 128 * 1024
        path = Path(command[command.index("--patch-file") + 1])
        patch_paths.append(path)
        patch_data = json.loads(path.read_text())
        assert patch_data == [
            {"op": "test", "path": "/metadata/resourceVersion", "value": "7"},
            {"op": "replace", "path": "/data/values.json", "value": values_json},
            {"op": "replace", "path": "/metadata/annotations/generated_at", "value": "now"},
        ]
        return _success()

    with patch.object(options_module.subprocess, "run", side_effect=run):
        options_module._apply_patch("kubectl", prepared)

    assert len(patch_paths) == 1
    assert not patch_paths[0].exists()


def test_apply_patches_reports_os_errors_and_finishes_other_targets() -> None:
    prepared = [
        options_module.PreparedPatch(
            options_module.ConfigMapTarget(region, "default", "getsentry", f"{region}-context"),
            "sentry-options-getsentry", "7", "now", "{}", "<unset>",
        )
        for region in ("us", "de")
    ]
    completed: list[str] = []
    patch_paths: list[Path] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--patch-file" in command:
            patch_paths.append(Path(command[command.index("--patch-file") + 1]))
        context = command[command.index("--context") + 1]
        if context == "us-context":
            raise OSError("cannot start kubectl")
        completed.append(context)
        return _success()

    with patch.object(options_module.subprocess, "run", side_effect=run):
        with pytest.raises(click.ClickException, match="Some ConfigMaps were not patched") as error:
            options_module._apply_patches("kubectl", prepared)

    assert "us/default/getsentry" in str(error.value)
    assert "cannot start kubectl" in str(error.value)
    assert completed == ["de-context"]
    assert all(not path.exists() for path in patch_paths)


@pytest.mark.parametrize(
    "schema",
    [
        {"version": "1.0", "type": "object"},
        {
            "version": "1.0", "type": "object", "properties": {
                "option": {"$ref": "#/$defs/missing", "default": 42, "description": ""},
            },
        },
    ],
    ids=["missing-properties", "invalid-reference"],
)
def test_validate_against_schema_rejects_malformed_snapshot_before_type_reporting(
    schema: dict[str, object], tmp_path: Path,
) -> None:
    namespace = tmp_path / "getsentry"
    namespace.mkdir()
    (namespace / "schema.json").write_text(json.dumps(schema))
    with patch.object(options_module, "_report_option_type") as report_type:
        with pytest.raises(click.ClickException, match="No clusters were contacted"):
            _validate_against_schema(tmp_path, "option", False)
    report_type.assert_not_called()
