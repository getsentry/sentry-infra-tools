from pathlib import Path
from unittest.mock import patch, MagicMock
from libsentrykube.utils import get_service_registry_filepath, workspace_root


@patch("importlib.import_module", return_value='/')
@patch("importlib.resources.files", return_value=Path("a/b/c"))
def test_get_service_registry_package_installed(
    mock_import_module, 
    mock_import_resources_path
):
    assert get_service_registry_filepath() == Path("a/b/c/sentry_service_registry/services.json")


def test_get_service_registry_package_not_installed():
    root = str(workspace_root())
    assert get_service_registry_filepath() == Path(f"{root}/shared_config/_materialized_configs/service_registry/combined/service_registry.json")