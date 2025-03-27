import copy

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from libsentrykube.utils import deep_merge_dict


@dataclass(frozen=True)
class HelmRelease:
    name: str
    chart: str
    chart_repo: tuple[str, str] | None
    chart_version: str | None
    values: dict[str, Any]

    @property
    def is_chart_local(self) -> bool:
        return self.chart_repo is None

    @property
    def chart_local_path(self) -> Path | None:
        if not self.is_chart_local:
            return None
        return Path(self.chart)


@dataclass(frozen=True)
class HelmData:
    services: list[str]
    global_data: dict[str, Any]
    svc_data: dict[str, dict[str, Any]]

    @property
    def service_names(self) -> list[str]:
        return [Path(p).name for p in self.services]

    def service_data(self, service_name) -> dict[str, Any]:
        rv = copy.deepcopy(self.global_data)
        deep_merge_dict(rv, self.svc_data.get(service_name, {}))
        return rv
