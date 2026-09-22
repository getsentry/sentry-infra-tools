import hashlib
import json
import subprocess
import threading
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import click
import pytest
from click.testing import CliRunner

import sentry_kube.cli.options as options_module
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


@patch("sentry_kube.cli.options._fetch_schemas")
@patch("sentry_kube.cli.options.ensure_kubectl", return_value="kubectl")
@patch("sentry_kube.cli.options.subprocess.run")
@patch("sentry_kube.cli.options.list_clusters_for_customer")
@patch("sentry_kube.cli.options.Config")
def test_set_fetches_schemas_when_no_snapshot_is_supplied(
    mock_config: MagicMock,
    mock_list_clusters: MagicMock,
    mock_run: MagicMock,
    _mock_kubectl: MagicMock,
    mock_fetch_schemas: MagicMock,
    tmp_path: Path,
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
        ],
    )

    assert result.exit_code == 0, result.output
    assert mock_fetch_schemas.call_args.args[0] == repos_config
    assert mock_fetch_schemas.call_args.args[1].name == "schemas"


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


def test_set_fetches_schemas_and_warms_cluster_access_concurrently(
    mock_schema_validation: MagicMock,
) -> None:
    """`set` overlaps the schema fetch/validate with the kubectl/gcloud
    warmup rather than running them serially. A `Barrier` proves this: each
    side only proceeds once both have started, so a regression to running
    them one after another deadlocks (and times out) instead of passing.
    """

    barrier = threading.Barrier(2, timeout=2)

    def _validate(*_args: object, **_kwargs: object) -> None:
        barrier.wait()

    def _warm_cluster_access() -> str:
        barrier.wait()
        return "kubectl"

    mock_schema_validation.side_effect = _validate

    with (
        patch.object(
            options_module, "_ensure_cluster_access", side_effect=_warm_cluster_access
        ),
        patch.object(options_module, "_selected_targets", return_value=[]),
    ):
        result = CliRunner().invoke(
            options,
            ["set", "sample-rate", "false", "--schemas", "schemas"],
        )

    assert result.exit_code == 0, result.output


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


def test_fetch_schemas_caches_a_fresh_snapshot(tmp_path: Path) -> None:
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


def test_fetch_schemas_falls_back_to_matching_cached_checksum_on_failure(
    tmp_path: Path,
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
        options_module._fetch_schemas(None, output)

    assert (output / "getsentry" / "schema.json").is_file()


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
