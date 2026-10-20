from pathlib import Path
from unittest.mock import patch

import pytest

from libsentrykube.helm import HelmChart, HelmRelease, HelmStrategyStandard, render


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
