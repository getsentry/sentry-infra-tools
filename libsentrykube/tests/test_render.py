from libsentrykube.kube import render_services
from libsentrykube.utils import set_workspace_root_start


def test_render_basic() -> None:
    # TODO: Refactor root to be a property of config object, not a global
    set_workspace_root_start("./")
    print(repr(list(render_services("test", "test", ["service5"]))))
