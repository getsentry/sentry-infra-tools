import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from libsentrykube.context import init_cluster_context
from libsentrykube.helm import (
    HelmChart,
    HelmRelease,
    HelmStrategyStandard,
    materialize_values,
    render,
)
from libsentrykube.service import build_helm_materialized_directory
from libsentrykube.utils import pretty, set_workspace_root_start, workspace_root


def _release() -> HelmRelease:
    return HelmRelease(
        name="kro",
        chart=HelmChart(
            name="kro",
            repo="oci://registry.k8s.io/kro/charts/kro",
            version="0.7.1",
            dynamic_app_version=False,
            dynamic_version_path="image.tag",
        ),
        namespace="kro-system",
        templates=[],
        strategy=HelmStrategyStandard("standard"),
    )


def _render_helm_params(service_dir: Path, region: str = "us") -> list[str]:
    calls: list[list[str]] = []

    def fake_run_helm(cmd: list[str], raise_on_err: bool = False) -> str:
        calls.append(cmd)
        return ""

    with (
        patch("libsentrykube.service.get_service_path", return_value=service_dir),
        patch(
            "libsentrykube.helm.helm_release_ctx",
            return_value=iter([(_release(), ["values.yaml"])]),
        ),
        patch("libsentrykube.helm._run_helm", side_effect=fake_run_helm),
    ):
        render(region, "kro")

    assert len(calls) == 1
    return calls[0]


@pytest.mark.parametrize(
    "flags, expected",
    [
        (None, False),
        ("jinja_whitespace_easymode: false\n", False),
        ("include_crds: false\n", False),
        ("include_crds: true\n", True),
    ],
)
def test_render_include_crds_flag(tmp_path: Path, flags: str | None, expected: bool):
    if flags is not None:
        (tmp_path / "_sk_flags.yaml").write_text(flags)

    helm_params = _render_helm_params(tmp_path)

    assert helm_params[0] == "template"
    assert ("--include-crds" in helm_params) is expected


def test_render_include_crds_region_override(tmp_path: Path):
    overrides = tmp_path / "region_overrides" / "s4s2"
    overrides.mkdir(parents=True)
    (overrides / "_sk_flags.yaml").write_text("include_crds: true\n")

    assert "--include-crds" in _render_helm_params(tmp_path, region="s4s2")
    assert "--include-crds" not in _render_helm_params(tmp_path, region="us")


def rel(name, templates, namespace="default"):
    return SimpleNamespace(name=name, namespace=namespace), list(templates.items())


@pytest.fixture
def materialize(tmp_path):
    service_dir = tmp_path / "my_service"

    def build_dir(region_name, cluster_name, service_name, release=None):
        path = service_dir / release if release else service_dir
        path.mkdir(parents=True, exist_ok=True)
        return path

    def run(releases, release=None, namespace=None):
        with (
            patch("libsentrykube.helm._render_values", return_value=releases),
            patch(
                "libsentrykube.service.build_helm_materialized_directory",
                side_effect=build_dir,
            ),
        ):
            return materialize_values(
                "customer1",
                "my_service",
                "cluster1",
                release=release,
                namespace=namespace,
            )

    return service_dir, run


def test_materialize_values_renders_and_cleans(config_structure):
    set_workspace_root_start(config_structure)
    os.environ["SENTRY_KUBE_CONFIG_FILE"] = str(
        workspace_root() / "cli_config/configuration.yaml"
    )
    init_cluster_context("customer1", "cluster1")
    service_path = Path(config_structure) / "k8s/helm_services/my_helm_service"
    (service_path / "00.default.yaml").write_text("key1: {{ values.key1 }}\n")
    output_dir = build_helm_materialized_directory(
        "customer1", "cluster1", "my_helm_service"
    )
    (output_dir / "values.yaml").write_text("key1: stale\n")
    (output_dir / "stale-release").mkdir()

    assert materialize_values("customer1", "my_helm_service", "cluster1") is True
    assert sorted(p.name for p in output_dir.iterdir()) == ["00.default.yaml"]
    assert materialize_values("customer1", "my_helm_service", "cluster1") is False


def test_materialize_values_removes_stale_files(materialize):
    service_dir, run = materialize
    service_dir.mkdir()
    (service_dir / "values.yaml").write_text("stale: true\n")
    (service_dir / "old.yml.j2").write_text("stale: true\n")

    assert run([rel("my_service", {"00.default.yaml": "a: 1\n"})]) is True

    assert sorted(p.name for p in service_dir.iterdir()) == ["00.default.yaml"]


def test_materialize_values_unchanged_returns_false(materialize):
    service_dir, run = materialize
    releases = [
        rel("my_service", {"00.default.yaml": "a: 1\n", "50.us.yaml": "b: 2\n"})
    ]
    assert run(releases) is True
    mtimes = {p.name: p.stat().st_mtime_ns for p in service_dir.iterdir()}

    assert run(releases) is False

    assert {p.name: p.stat().st_mtime_ns for p in service_dir.iterdir()} == mtimes


def test_materialize_values_stale_removal_only_returns_true(materialize):
    service_dir, run = materialize
    releases = [rel("my_service", {"00.default.yaml": "a: 1\n"})]
    run(releases)
    (service_dir / "values.yaml").write_text("stale: true\n")

    assert run(releases) is True

    assert not (service_dir / "values.yaml").exists()


def test_materialize_values_filtered_release_leaves_others_alone(materialize):
    service_dir, run = materialize
    run(
        [
            rel("my_service", {"values.yaml": "root: 1\n"}),
            rel("canary", {"values.yaml": "canary: 1\n"}),
            rel("production", {"values.yaml": "prod: 1\n", "extra.yaml": "x: 1\n"}),
        ]
    )

    assert run(
        [
            rel("my_service", {"00.default.yaml": "root: 2\n"}),
            rel("canary", {"00.default.yaml": "canary: 2\n"}),
            rel("production", {"00.default.yaml": "prod: 2\n"}),
        ],
        release="canary",
    )

    assert sorted(p.name for p in (service_dir / "canary").iterdir()) == [
        "00.default.yaml"
    ]
    assert sorted(p.name for p in (service_dir / "production").iterdir()) == [
        "extra.yaml",
        "values.yaml",
    ]
    assert sorted(p.name for p in service_dir.iterdir() if p.is_file()) == [
        "values.yaml"
    ]
    assert (service_dir / "values.yaml").read_text() == pretty("root: 1\n")


def test_materialize_values_root_cleanup_leaves_release_dirs(materialize):
    service_dir, run = materialize
    run(
        [
            rel("my_service", {"values.yaml": "root: 1\n"}),
            rel("canary", {"values.yaml": "canary: 1\n"}),
        ]
    )

    run(
        [
            rel("my_service", {"00.default.yaml": "root: 1\n"}),
            rel("canary", {"values.yaml": "canary: 1\n"}),
        ]
    )

    assert not (service_dir / "values.yaml").exists()
    assert (service_dir / "00.default.yaml").exists()
    assert (service_dir / "canary" / "values.yaml").exists()


def test_materialize_values_same_name_releases_share_directory(materialize):
    service_dir, run = materialize
    releases = [
        rel("pop", {"00.default.yaml": "a: 1\n", "50.us.yaml": "us: 1\n"}, "us"),
        rel("pop", {"00.default.yaml": "a: 1\n", "50.de.yaml": "de: 1\n"}, "de"),
    ]
    assert run(releases) is True

    assert run(releases) is False
    assert run(releases, namespace="de") is False
    assert sorted(p.name for p in (service_dir / "pop").iterdir()) == [
        "00.default.yaml",
        "50.de.yaml",
        "50.us.yaml",
    ]


def test_materialize_values_prunes_removed_release_dirs(materialize):
    service_dir, run = materialize
    run(
        [
            rel("production", {"values.yaml": "prod: 1\n"}),
            rel("canary", {"values.yaml": "canary: 1\n"}),
        ]
    )

    assert run([rel("production", {"values.yaml": "prod: 1\n"})]) is True

    assert sorted(p.name for p in service_dir.iterdir()) == ["production"]


def test_materialize_values_filtered_run_keeps_unrendered_release_dirs(materialize):
    service_dir, run = materialize
    run(
        [
            rel("production", {"values.yaml": "prod: 1\n"}),
            rel("canary", {"values.yaml": "canary: 1\n"}),
        ]
    )

    assert (
        run([rel("production", {"values.yaml": "prod: 1\n"})], release="production")
        is False
    )

    assert (service_dir / "canary" / "values.yaml").exists()
