import random
import string
import click
from libsentrykube.git import Git
from libsentrykube.quickpatch import apply_patch, get_arguments
from libsentrykube.kube import render_templates
from sentry_kube.cli.apply import _diff_kubectl
from typing import Sequence, MutableMapping

from libsentrykube.utils import workspace_root

__all__ = ("quickpatch",)


@click.command()
@click.option("--service", "-s", help="Sentry kube service name")
@click.option(
    "--resource", "-r", help="K8s resource to patch. This must match the k8s name."
)
@click.option(
    "--patch", "-p", help="The patch name. This correspond to the patch file."
)
@click.option(
    "--arguments", "-a", multiple=True, help="The arguments to populate the patch file."
)
@click.option("--no-pull", is_flag=True, help="Skip pulling master before patching.")
@click.option("--no-pr", is_flag=True, help="Skip the creation of the PR.")
@click.option(
    "--force-branch-creation",
    is_flag=True,
    default=False,
    help="Force the creation of the new branch.",
)
# We cannot make `quickpatch` a group and render/diff/apply sub-commands as we should
# because sentry-kube does not store a dictionary in click.ctx. It uses a data class
# which is the same for all commands, so we cannot easily customize it for a specific
# command.
@click.argument("action", type=click.Choice(["render", "diff", "apply"]))
@click.pass_context
def quickpatch(
    ctx: click.Context,
    service: str,
    resource: str,
    patch: str,
    arguments: Sequence[str],
    no_pull: bool,
    no_pr: bool,
    force_branch_creation: bool,
    action: str,
):
    """
    Applies pre-defined patches to the value files of our Kubernetes services.

    Valid patches are defined as files in the `quickpatches` directory in each
    service. This command applies the patch to the file locally, it renders and
    applies to production and sends a PR with the change.
    """

    # TODO: Validate parameters

    if not no_pull:
        git = Git(repo_path=str(workspace_root()))
        branch_name = "".join(
            random.choices(string.ascii_lowercase + string.digits, k=6)
        )
        branch_name = f"quickpatch-{branch_name}"
        git.create_branch(branch_name)
        git.stash(force=force_branch_creation)
        git.switch_to_branch(branch_name)
        git.set_upstream(branch_name)
        git.fetch_origin()
        git.merge_origin(git.default_branch)

    get_arguments(service, patch)

    # TODO: Validate all arguments are passed and prompt for the missing ones.
    populated_arguments: MutableMapping[str, str] = {}
    for arg in arguments:
        key, value = arg.split("=", 2)
        populated_arguments[key] = value
    apply_patch(
        service,
        ctx.obj.customer_name,
        resource,
        patch,
        populated_arguments,
        cluster=ctx.obj.cluster_name,
    )

    if action == "render":
        print(
            render_templates(
                ctx.obj.customer_name,
                service,
                ctx.obj.cluster_name,
                filters=[f"metadata.name={resource}"],
            )
        )
    elif action == "diff":
        definitions = "".join(
            render_templates(
                ctx.obj.customer_name,
                service,
                ctx.obj.cluster_name,
                filters=[f"metadata.name={resource}"],
            )
        ).encode("utf-8")
        return _diff_kubectl(
            ctx=ctx,
            definitions=definitions,
        )
    elif action == "apply":
        raise NotImplementedError("Apply is not implemented yet")
    else:
        raise ValueError(f"Invalid action {action}")

    # Clean up the branch
    # TODO: At some point we should make this a context manager so cleanup
    # happens autoatically.
    if not no_pull:
        # Quickpatch could either modify existing files or add new ones. Both
        # cases are handled here.
        unstaged_files = git.get_unstaged_files()
        untracked_files = git.get_untracked_files()
        if unstaged_files:
            files_to_add = [
                file for file in unstaged_files if file.endswith(".managed.yaml")
            ]
            git.add(files_to_add)
            git.commit(f"fix: Quickpatch for {service} {resource}")
        elif untracked_files:
            files_to_add = [
                file for file in untracked_files if file.endswith(".managed.yaml")
            ]
            git.add(files_to_add)
            git.commit(f"fix: Quickpatch for {service} {resource}")

        # Rewind local setup to what it was before we started
        git.switch_to_branch(git.previous_branch)
        git.pop_stash()

    # TODO: File PR
