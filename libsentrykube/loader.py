from importlib.metadata import entry_points
from typing import Optional, Sequence

__registry: Optional[Sequence[str]] = None


def load_macros() -> Sequence[str]:
    global __registry
    if __registry is None:
        __registry = []
        for ep in entry_points(group="libsentrykube.macros"):
            ext = ep.load()
            ext.install(ep.name)
            __registry.append(ext)
    return __registry
