import pytest
from pr_approver.rules import (
    ApprovalDecision,
    assess_data_disk_size_increase,
    assess_service_registry_change,
)
from typing import Mapping, Any
import tempfile
from pathlib import Path
from yaml import safe_dump
import os

DECISION_TEST = [
    pytest.param(
        ApprovalDecision.IGNORE,
        ApprovalDecision.IGNORE,
        ApprovalDecision.IGNORE,
        id="Same value in same value out",
    ),
    pytest.param(
        ApprovalDecision.IGNORE,
        ApprovalDecision.APPROVE,
        ApprovalDecision.APPROVE,
        id="Approve wins over Ignore",
    ),
    pytest.param(
        ApprovalDecision.IGNORE,
        ApprovalDecision.DECLINE,
        ApprovalDecision.DECLINE,
        id="Decline wins over Ignore",
    ),
    pytest.param(
        ApprovalDecision.APPROVE,
        ApprovalDecision.DECLINE,
        ApprovalDecision.DECLINE,
        id="Decline wins over Approve",
    ),
]


@pytest.mark.parametrize(
    "decision1, decision2, result",
    DECISION_TEST,
)
def test_decision(
    decision1: ApprovalDecision, decision2: ApprovalDecision, result: ApprovalDecision
) -> None:
    assert ApprovalDecision.compare(decision1, decision2) == result


BASE_SERVICE = {
    "name": "Snuba",
    "component": "component",
    "domain_experts": ["somebody@sentry"],
    "notes": None,
    "alert_slack_channels": ["feed-datdog"],
}

APPROVAL_TEST = [
    pytest.param(
        None,
        {
            **BASE_SERVICE,
            **{
                "teams": ["search_and_storage"],
                "slack_channels": ["discuss-eng-sns"],
            },
        },
        ApprovalDecision.APPROVE,
        id="Adding services does not require review",
    ),
    pytest.param(
        {
            **BASE_SERVICE,
            **{
                "tier": 0,
                "teams": ["search_and_storage"],
                "slack_channels": ["discuss-eng-sns"],
            },
        },
        None,
        ApprovalDecision.DECLINE,
        id="Removing tier 0 service requires review",
    ),
    pytest.param(
        {
            **BASE_SERVICE,
            **{
                "tier": 3,
                "teams": ["search_and_storage"],
                "slack_channels": ["discuss-eng-sns"],
            },
        },
        None,
        ApprovalDecision.APPROVE,
        id="Removing tier 3 service does not require review",
    ),
    pytest.param(
        {
            **BASE_SERVICE,
            **{
                "tier": 3,
                "teams": ["search_and_storage"],
                "slack_channels": ["discuss-eng-sns"],
            },
        },
        {
            **BASE_SERVICE,
            **{
                "tier": 0,
                "teams": ["search_and_storage"],
                "slack_channels": ["discuss-eng-sns"],
            },
        },
        ApprovalDecision.DECLINE,
        id="Bumping up tier requires review",
    ),
    pytest.param(
        {
            **BASE_SERVICE,
            **{
                "tier": 3,
                "teams": ["search_and_storage"],
                "slack_channels": ["discuss-eng-sns"],
            },
        },
        {
            **BASE_SERVICE,
            **{
                "tier": 0,
                "teams": [],
                "slack_channels": ["discuss-eng-sns"],
            },
        },
        ApprovalDecision.DECLINE,
        id="Abandoning service requires review",
    ),
    pytest.param(
        {
            **BASE_SERVICE,
            **{
                "tier": 3,
                "teams": ["search_and_storage", "another-team"],
                "slack_channels": ["discuss-eng-sns"],
            },
        },
        {
            **BASE_SERVICE,
            **{
                "tier": 3,
                "teams": ["search_and_storage"],
                "slack_channels": ["discuss-eng-sns"],
            },
        },
        ApprovalDecision.APPROVE,
        id="Removing one team without abandoning service does not require review",
    ),
    pytest.param(
        {
            **BASE_SERVICE,
            **{
                "tier": 3,
                "teams": ["search_and_storage"],
                "slack_channels": ["discuss-eng-sns"],
            },
        },
        {
            **BASE_SERVICE,
            **{
                "tier": 0,
                "teams": ["search_and_storage"],
                "slack_channels": [],
            },
        },
        ApprovalDecision.DECLINE,
        id="Removing slack channel requires review",
    ),
    pytest.param(
        {
            **BASE_SERVICE,
            **{
                "name": "Snuba",
                "tier": 1,
                "teams": ["search_and_storage"],
                "slack_channels": ["discuss-eng-sns"],
            },
        },
        {
            **BASE_SERVICE,
            **{
                "name": "Not Snuba",
                "tier": 1,
                "teams": ["search_and_storage"],
                "slack_channels": ["discuss-eng-sns"],
            },
        },
        ApprovalDecision.APPROVE,
        id="Changing name does not require review",
    ),
]


@pytest.mark.parametrize(
    "base, pr, expected_result",
    APPROVAL_TEST,
)
def test_approval(
    base: Mapping[str, Any] | None,
    pr: Mapping[str, Any] | None,
    expected_result: ApprovalDecision,
) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        base_dir = Path(temp_dir) / "base"
        os.makedirs(base_dir)
        pr_dir = Path(temp_dir) / "pr"
        os.makedirs(pr_dir)

        if base is not None:
            (Path(base_dir) / "service.yaml").write_text(safe_dump(base))
        if pr is not None:
            print(Path(pr_dir) / "service.yaml")
            (Path(pr_dir) / "service.yaml").write_text(safe_dump(pr))

        assert (
            assess_service_registry_change(
                Path("service.yaml"),
                Path(temp_dir) / "base",
                Path(temp_dir) / "pr",
            )
            == expected_result
        )


DISK_INCREASE_BASE = """inputs = {
  data_disk_size = 600
}
"""

DISK_INCREASE_PR = """inputs = {
  data_disk_size = 1200
}
"""

DISK_DECREASE_PR = """inputs = {
  data_disk_size = 300
}
"""

DISK_PLUS_OTHER_CHANGE_PR = """inputs = {
  data_disk_size = 1200
  machine_type   = "n2-standard-8"
}
"""

DISK_PLUS_UNRELATED_LINE_CHANGE_PR = """inputs = {
  data_disk_size = 1200
  num_replicas   = 5
}
"""

DISK_INCREASE_TESTS = [
    pytest.param(
        DISK_INCREASE_BASE,
        DISK_INCREASE_PR,
        ApprovalDecision.APPROVE,
        id="Increasing data_disk_size is approved",
    ),
    pytest.param(
        DISK_INCREASE_BASE,
        DISK_INCREASE_BASE,
        ApprovalDecision.IGNORE,
        id="No-op diff is ignored",
    ),
    pytest.param(
        DISK_INCREASE_BASE,
        DISK_DECREASE_PR,
        ApprovalDecision.DECLINE,
        id="Decreasing data_disk_size requires review",
    ),
    pytest.param(
        DISK_INCREASE_BASE,
        DISK_PLUS_OTHER_CHANGE_PR,
        ApprovalDecision.DECLINE,
        id="Adding unrelated lines requires review",
    ),
    pytest.param(
        "inputs = {\n  data_disk_size = 600\n  num_replicas   = 3\n}\n",
        DISK_PLUS_UNRELATED_LINE_CHANGE_PR,
        ApprovalDecision.DECLINE,
        id="Modifying another line in the same diff requires review",
    ),
    pytest.param(
        None,
        DISK_INCREASE_PR,
        ApprovalDecision.DECLINE,
        id="Adding a new file requires review",
    ),
    pytest.param(
        DISK_INCREASE_BASE,
        None,
        ApprovalDecision.DECLINE,
        id="Deleting a file requires review",
    ),
]


@pytest.mark.parametrize(
    "base, pr, expected_result",
    DISK_INCREASE_TESTS,
)
def test_data_disk_size_increase(
    base: str | None,
    pr: str | None,
    expected_result: ApprovalDecision,
) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        base_dir = Path(temp_dir) / "base"
        os.makedirs(base_dir)
        pr_dir = Path(temp_dir) / "pr"
        os.makedirs(pr_dir)

        if base is not None:
            (base_dir / "local.hcl").write_text(base)
        if pr is not None:
            (pr_dir / "local.hcl").write_text(pr)

        assert (
            assess_data_disk_size_increase(
                Path("local.hcl"),
                base_dir,
                pr_dir,
            )
            == expected_result
        )
