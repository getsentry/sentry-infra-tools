from typing import Optional, Sequence
from pkg_resources import iter_entry_points

__registry: Optional[Sequence[str]] = None


def load_macros() -> Sequence[str]:
    global __registry
    if __registry is None:
        __registry = []
        for ep in iter_entry_points("libsentrykube.macros"):
            ext = ep.load()
            ext.install(ep.name)
            __registry.append(ext)
    return __registry
