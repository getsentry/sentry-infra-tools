import random
import string
import click
from libsentrykube.git import Git
from libsentrykube.quickpatch import apply_patch, get_arguments
from libsentrykube.kube import render_templates
from typing import Sequence, Mapping

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
@click.pass_context
def quickpatch(
    ctx: click.core.Context,
    service: str,
    resource: str,
    patch: str,
    arguments: Sequence[str],
    no_pull: bool,
    no_pr: bool,
    force_branch_creation: bool,
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
    populated_arguments: Mapping[str, str] = {}
    apply_patch(service, resource, patch, populated_arguments)

    render_templates(
        ctx.obj.customer_name,
        service,
        ctx.obj.cluster_name,
        filters=[f"metadata.name={resource}"],
    )

    # Clean up the branch
    # TODO: At some point we should make this a context manager so cleanup
    # happens autoatically.
    if not no_pull:
        # Quickpatch could either modify existing files or add new ones. Both
        # cases are handled here.
        if git.get_unstaged_files():
            git.add(git.get_unstaged_files())
            git.commit(f"fix: Quickpatch for {service} {resource}")
        elif git.get_untracked_files():
            for file in git.get_untracked_files():
                if file.endswith(".managed.yaml"):
                    git.add(list(file))
            git.commit(f"fix: Quickpatch for {service} {resource}")

        # Rewind local setup to what it was before we started
        git.switch_to_branch(git.previous_branch)
        git.pop_stash()

    # TODO: Apply to prod
    # TODO: File PR
