from __future__ import annotations

import re
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


DATA_DISK_SIZE_LINE = re.compile(r"^(?P<indent>\s*)data_disk_size\s*=\s*(?P<value>\d+)\s*$")


def assess_data_disk_size_increase(
    file_path: Path, base: Path, pr: Path
) -> ApprovalDecision:
    """
    Approve terragrunt HCL changes that consist exclusively of strict
    increases to ``data_disk_size`` values. Any other modification,
    file creation, deletion, or a decrease in disk size requires
    manual review.
    """
    base_file = base / file_path
    pr_file = pr / file_path
    if not base_file.exists() or not pr_file.exists():
        return ApprovalDecision.DECLINE

    base_lines = base_file.read_text().splitlines()
    pr_lines = pr_file.read_text().splitlines()
    if len(base_lines) != len(pr_lines):
        return ApprovalDecision.DECLINE

    saw_increase = False
    for base_line, pr_line in zip(base_lines, pr_lines):
        if base_line == pr_line:
            continue

        base_match = DATA_DISK_SIZE_LINE.match(base_line)
        pr_match = DATA_DISK_SIZE_LINE.match(pr_line)
        if not base_match or not pr_match:
            return ApprovalDecision.DECLINE
        if base_match.group("indent") != pr_match.group("indent"):
            return ApprovalDecision.DECLINE
        if int(pr_match.group("value")) <= int(base_match.group("value")):
            return ApprovalDecision.DECLINE

        saw_increase = True

    return ApprovalDecision.APPROVE if saw_increase else ApprovalDecision.IGNORE
