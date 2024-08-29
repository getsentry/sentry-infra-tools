import os
from typing import Iterator

import pytest
from libsentrykube.utils import set_workspace_root_start
from libsentrykube.utils import workspace_root


@pytest.fixture(autouse=True)
def set_workspaceroot() -> Iterator[None]:
    """
    Most tests rely on the workspaceroot directory to be set to the
    workspace directory before loading configuration or services.
    The default value is not good for tests, so we ensure all
    tests are properly set up.
    """

    start_workspace_root = workspace_root().as_posix()
    set_workspace_root_start((workspace_root() / "libsentrykube/tests").as_posix())
    os.environ["SENTRY_KUBE_CONFIG_FILE"] = str(workspace_root() / "config.yaml")
    yield
    set_workspace_root_start(start_workspace_root)
