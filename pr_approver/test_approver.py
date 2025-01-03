import pytest
from typing import Generator
import tempfile
from pathlib import Path
import os
from yaml import safe_dump
from pr_approver.rules import (
    assess_service_registry_change,
    ignore_file,
    ApprovalDecision,
)
from pr_approver.approver import assess_pr

BASE_SERVICE = {
    "name": "Snuba",
    "component": "component",
    "tier": 0,
    "teams": ["search_and_storage"],
    "domain_experts": ["somebody@sentry"],
    "notes": None,
    "slack_channels": ["discuss-eng-sns"],
    "alert_slack_channels": ["feed-datdog"],
}


def create_all_files(root: Path) -> None:
    service_registry = Path(root) / "shared_config/service_registry"
    os.makedirs(service_registry)
    (service_registry / "snuba.yaml").write_text(safe_dump(BASE_SERVICE))
    (service_registry / "another_snuba.yaml").write_text(safe_dump(BASE_SERVICE))

    service_libs = Path(root) / "shared_config/service_registry/libs"
    os.makedirs(service_libs / "subdir")
    (service_libs / "lib.yaml").write_text("{}")

    materialized_files = (
        Path(root) / "shared_config/_materialized_configs/service_registry"
    )
    os.makedirs(materialized_files)
    (materialized_files / "services.yaml").write_text("{}")


@pytest.fixture
def files_structure() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as temp_dir:
        base = Path(temp_dir) / "base"
        os.makedirs(base)
        create_all_files(base)

        pr = Path(temp_dir) / "pr"
        os.makedirs(pr)
        create_all_files(pr)

        yield temp_dir


def test_approver(files_structure) -> None:
    config = [
        ("shared_config/_materialized_configs/**", ignore_file),
        ("shared_config/service_registry/*.yaml", assess_service_registry_change),
    ]

    base_dir = Path(files_structure) / "base"
    pr_dir = Path(files_structure) / "pr"

    assert (
        assess_pr(
            base_dir,
            pr_dir,
            [Path("somewhere_else/subdir/file.yaml")],
            config,
        )
        == ApprovalDecision.DECLINE
    )

    (Path(files_structure) / "pr/shared_config/service_registry/snuba.yaml").write_text(
        safe_dump({**BASE_SERVICE, **{"name": "Definitively not snuba"}})
    )

    assert (
        assess_pr(
            base_dir,
            pr_dir,
            [
                Path("shared_config/service_registry/snuba.yaml"),
                Path(
                    "shared_config/_materialized_configs/service_registry/services.yaml"
                ),
            ],
            config,
        )
        == ApprovalDecision.APPROVE
    )
