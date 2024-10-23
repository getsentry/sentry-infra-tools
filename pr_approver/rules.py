from __future__ import annotations

from typing import Callable, Iterable
from functools import reduce
from enum import Enum
from yaml import safe_load, YAMLError
from pathlib import Path
from dictdiffer import diff


class ApprovalDecision(Enum):
    IGNORE = "ignore"
    APPROVE = "approve"
    DECLINE = "decline"

    @classmethod
    def compare(
        cls, decision1: ApprovalDecision, decision2: ApprovalDecision
    ) -> ApprovalDecision:
        priorities = {
            ApprovalDecision.IGNORE: 0,
            ApprovalDecision.APPROVE: 1,
            ApprovalDecision.DECLINE: 2,
        }
        swapped = {value: key for key, value in priorities.items()}

        higher = max(priorities[decision1], priorities[decision2])
        return swapped[higher]

    @classmethod
    def combine(cls, decisions: Iterable[ApprovalDecision]) -> ApprovalDecision:
        return reduce(cls.compare, decisions, ApprovalDecision.IGNORE)


diff_approver = Callable[[Path, Path, Path], ApprovalDecision]


def ignore_file(file_path: Path, base: Path, pr: Path) -> ApprovalDecision:
    return ApprovalDecision.IGNORE


def assess_service_registry_change(
    file_path: Path, base: Path, pr: Path
) -> ApprovalDecision:
    if not (pr / file_path).exists() and (base / file_path).exists():
        # Deleting T0 - 2. Ask for review.
        # TODO: Consider opening this up.
        pr_dict = safe_load((base / file_path).read_text())
        if pr_dict.get("tier") in {0, 1, 2}:
            return ApprovalDecision.DECLINE
        else:
            return ApprovalDecision.APPROVE

    if (pr / file_path).exists() and not (base / file_path).exists():
        return ApprovalDecision.APPROVE

    try:
        base_dict = safe_load((base / file_path).read_text())
        pr_dict = safe_load((pr / file_path).read_text())
    except YAMLError:
        # If a file is not valid json we play it safe and skip approving
        return ApprovalDecision.DECLINE

    pr_diff = diff(base_dict, pr_dict)
    for change in pr_diff:
        changed_filed = change[1]
        if changed_filed == "tier":
            # Cannot change tier without review
            return ApprovalDecision.DECLINE
        if changed_filed == "teams" and len(pr_dict.get("teams", [])) == 0:
            # Cannot abandon a service (no owner) without review
            return ApprovalDecision.DECLINE
        if (
            changed_filed == "slack_channels"
            and base_dict.get("slack_channel")
            and len(pr_dict.get("slack_channels", [])) == 0
        ):
            # Cannot remove the slack channel
            return ApprovalDecision.DECLINE

    return ApprovalDecision.APPROVE
