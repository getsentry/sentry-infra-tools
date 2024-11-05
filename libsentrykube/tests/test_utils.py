from pathlib import Path
import pytest
from unittest.mock import patch
from libsentrykube.utils import (
    KUBECTL_BINARY,
    KUBECTL_VERSION,
    ensure_kubectl,
    get_service_registry_filepath,
    workspace_root,
)


@patch("importlib.import_module", return_value="/")
@patch("importlib.resources.files", return_value=Path("a/b/c"))
def test_get_service_registry_package_installed(
    mock_import_module, mock_import_resources_path
):
    assert get_service_registry_filepath() == Path(
        "a/b/c/config/combined/service_registry.json"
    )


def test_get_service_registry_package_not_installed():
    root = str(workspace_root())
    assert get_service_registry_filepath() == Path(
        f"{root}/shared_config/_materialized_configs/service_registry/combined/service_registry.json"
    )


def test_ensure_kubectl_existing_file(tmp_path):
    with patch(
        "libsentrykube.utils.ensure_libsentrykube_folder", return_value=tmp_path
    ):
        kubectl_path = tmp_path / "kubectl" / f"v{KUBECTL_VERSION}" / KUBECTL_BINARY
        kubectl_path.parent.mkdir(parents=True)
        kubectl_path.touch()

        result = ensure_kubectl(KUBECTL_BINARY, KUBECTL_VERSION)
        assert result == kubectl_path


def test_ensure_kubectl_unsupported_binary():
    with pytest.raises(
        RuntimeError,
        match="Unsupported binary 'unsupported', please install it manually or update SENTRY_KUBE_KUBECTL_BINARY.",
    ):
        ensure_kubectl("unsupported", KUBECTL_VERSION)
