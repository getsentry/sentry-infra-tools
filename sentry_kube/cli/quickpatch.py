import click
from libsentrykube.git import go_to_main, pull_main, create_branch
from libsentrykube.quickpatch import apply_patch, get_arguments
from libsentrykube.kube import render_templates
from typing import MutableMapping, Sequence

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
@click.pass_context
def quickpatch(
    ctx: click.core.Context,
    service: str,
    resource: str,
    patch: str,
    arguments: Sequence[str],
    no_pull: bool,
    no_pr: bool,
):
    """
    Applies pre-defined patches to the value files of our Kubernetes services.

    Valid patches are defined as files in the `quickpatches` directory in each
    service. This command applies the patch to the file locally, it renders and
    applies to production and sends a PR with the change.
    """

    # TODO: Validate parameters

    if not no_pull:
        go_to_main()
        pull_main()
        create_branch()

    get_arguments(service, patch)
    # TODO: Validate all arguments are passed and prompt for the missing ones.
    populated_arguments: MutableMapping[str, str] = {}
    apply_patch(
        service,
        ctx.obj.customer_name,
        resource,
        patch,
        populated_arguments,
        cluster=ctx.obj.cluster_name,
    )

    render_templates(
        ctx.obj.customer_name,
        service,
        ctx.obj.cluster_name,
        filters=[f"metadata.name={resource}"],
    )
    # TODO: Apply to prod
    # TODO: File PR
