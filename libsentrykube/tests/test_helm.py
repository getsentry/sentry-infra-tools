from pathlib import Path

from libsentrykube.helm import HelmChart
from libsentrykube.helm import HelmRelease
from libsentrykube.helm import HelmStrategyStandard


def test_filter_template_files_preserves_release_order() -> None:
    service_path = Path("/service")
    default_values = service_path / "default.yaml"
    regional_values = service_path / "de.values.yaml"
    release = HelmRelease(
        name="service",
        chart=HelmChart(
            name="chart",
            repo=None,
            version=None,
            dynamic_app_version=False,
            dynamic_version_path="image.tag",
        ),
        namespace="default",
        templates=["default.yaml", "de.values.yaml"],
        strategy=HelmStrategyStandard("standard"),
    )

    template_files = [regional_values, default_values, service_path / "unused.yaml"]

    assert release.filter_template_files(service_path, template_files) == [
        default_values,
        regional_values,
    ]
