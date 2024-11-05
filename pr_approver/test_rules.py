import pytest
from pr_approver.rules import ApprovalDecision, assess_service_registry_change
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
