from typing import Sequence, Tuple, Set, MutableSequence
import glob
from pathlib import Path
import os
import argparse
import re
import importlib
import sys
from pr_approver.gh import accept_pr, dismiss_acceptance
from pr_approver.rules import diff_approver, ApprovalDecision


config_parser = re.compile(r"^(?P<pattern>\S+)\s+(?P<module>.+)$")


def assess_file(
    base: Path,
    pr: Path,
    path: Path,
    rules_config: Sequence[Tuple[Set[Path], diff_approver]],
) -> ApprovalDecision:
    """
    Applies all the approval rules to a single file.
    """
    for eligible_paths, approver in rules_config:
        if path in eligible_paths:
            return approver(path, base, pr)

    return ApprovalDecision.DECLINE


def assess_pr(
    base: Path,
    pr: Path,
    paths: Sequence[Path],
    rules_config: Sequence[Tuple[str, diff_approver]],
) -> ApprovalDecision:
    """
    Evaluates a PR changeset to decide whether it can be auto approved or not.

    To do so it relies on a set of rules defined in the `rules` package.
    A rule assesses whether the diff of a file qualifies for auto approval,
    whether it does not (requiring review) or whether the file should be
    ignored.

    Rules are provided as a mapping between patterns and rule functions.
    Patterns are defined with the same format we use in CODEOWNERS.
    """
    processed_config: MutableSequence[Tuple[Set[Path], diff_approver]] = []

    def get_eligible_files(root: Path, pattern: str) -> Set[Path]:
        eligible_files = glob.glob(f"{root}/{pattern}", recursive=True)
        return {Path(f).relative_to(root) for f in eligible_files}

    for pattern, approver in rules_config:
        eligible_files = get_eligible_files(base, pattern) | get_eligible_files(
            pr, pattern
        )
        processed_config.append((eligible_files, approver))

    decisions = []
    for p in paths:
        decision = assess_file(base, pr, p, processed_config)
        print(f"Reviewed {p} - decision {decision.value}", file=sys.stderr)
        decisions.append(decision)
    return ApprovalDecision.combine(decisions)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="""
            Inspects the changeset of a PR to find out if this can be auto-approved
            and either approves the PR, or dismiss a previous approval if present
            or ignores it if no previous approval is there.

            This script uses a set of rules, each is specific to a file type and
            is able to inspect diff of a single file to tell if we need a review.
            There is a configuration file that maps file patterns to rules.

            The changeset is provided on the stdin one file per line.
        """
    )

    parser.add_argument(
        "-c",
        "--config",
        action="store",
        help="""
            The path to the config file with the mapping between patterns and
            rules. This looks like the CODEOWNERS file, except that for each pattern
            we provide the module.function that implements the rule rather than
            the owner.
            Rules are applied in sequence. The first rule that applies wins.
        """,
    )

    parser.add_argument(
        "-b",
        "--base",
        action="store",
        help="""
            The path where the base git repo is cloned
        """,
    )

    parser.add_argument(
        "-p",
        "--pr",
        action="store",
        help="""
            The path where the PR git repo is cloned
        """,
    )

    parser.add_argument(
        "-n",
        "--number",
        action="store",
        help="""
            The PR number
        """,
    )

    parser.add_argument(
        "-l",
        "--login",
        action="store",
        help="""
            THe github user login
        """,
    )

    args = parser.parse_args(argv)
    config_path = args.config
    assert Path(config_path).exists() and Path(config_path).is_file(), (
        f"Invalid path to a config file {config_path}. File does not exist"
    )

    config: MutableSequence[Tuple[str, diff_approver]] = []
    with open(config_path, "r") as f:
        for line in f.readlines():
            if line and not line.startswith("#"):
                match = config_parser.search(line)
                assert match is not None
                pattern = match.group("pattern")
                approver_name = match.group("module")

                module_name, function_name = approver_name.rsplit(".", 1)
                module = importlib.import_module(f"pr_approver.{module_name}")
                approver = getattr(module, function_name)

                config.append((pattern, approver))

    base_path = Path(args.base)
    pr_path = Path(args.pr)

    changeset = [Path(p.strip()) for p in sys.stdin if p.strip()]
    result = assess_pr(base_path, pr_path, changeset, config)

    pr_number = args.number

    if result == ApprovalDecision.APPROVE:
        accept_pr(pr_number, "Automatically accepted", os.environ["GH_TOKEN"])
        print("PR Accepted", file=sys.stderr)
    else:
        dismissed_ids = dismiss_acceptance(
            pr_number,
            "Additional changes cannot be auto accepted.",
            args.login,
            os.environ["GH_TOKEN"],
        )
        print(f"Reviews dismissed {dismissed_ids}", file=sys.stderr)


if __name__ == "__main__":
    main()
