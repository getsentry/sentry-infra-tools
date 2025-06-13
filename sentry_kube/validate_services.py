from pathlib import Path
import subprocess
from typing import Sequence
from libsentrykube.reversemap import build_index
from libsentrykube.config import Config
from libsentrykube.context import init_cluster_context
from libsentrykube.kube import render_services
from libsentrykube.lint import lint_and_print
from libsentrykube.utils import workspace_root
from libsentrykube.service import get_service_path

import click


@click.command()
@click.argument("filename", nargs=-1)
@click.option("--skip-region", multiple=True)
def test_services(filename: Sequence[str], skip_region: Sequence[str]) -> None:
    """
    Identifies the sentry-kube k8s services that may have been modified
    by the PR based on the changeset and lints/tests each of them.

    We lint/test only the relevant services in order to avoid having to render
    every single service for all clusters as that would take several
    minutes.
    """
    # reversemap.build_index builds a Trie like data structure that
    # keeps a reverse mapping between files on disk and services
    # impacted by changes to those files.
    # We use it to identify services from the changeset.
    index = build_index()
    resources_to_render = set()

    for file in filename:
        path = Path(file)
        resources_to_render.update(index.get_resources_for_path(path))

    lint_errors_count = 0

    for resource in resources_to_render:
        # Skip specified regions
        if resource.customer_name in skip_region:
            click.echo(f"Skipping {resource.customer_name} {resource.cluster_name}")
            continue

        click.echo(
            f"Validating {resource.customer_name} {resource.cluster_name}", err=True
        )
        init_cluster_context(resource.customer_name, resource.cluster_name)

        if resource.service_name is not None:
            service_path = get_service_path(resource.service_name)
            config_root = Config().silo_regions[resource.customer_name].k8s_config.root
            root_config = workspace_root() / config_root
            policies_paths = [
                Path(root_config) / "policy",
                Path(service_path) / "policy",
            ]

            rendered_lint = render_services(
                resource.customer_name, resource.cluster_name, [resource.service_name]
            )
            click.echo(f"Linting resource {resource.service_name}")
            lint_errors_count += lint_and_print(
                resource.customer_name,
                resource.cluster_name,
                resource.service_name,
                rendered_lint,
            )

            rendered_validate = render_services(
                resource.customer_name, resource.cluster_name, [resource.service_name]
            )
            click.echo(f"Testing resource {resource.service_name}")
            for path in policies_paths:
                if path.exists() and path.is_dir():
                    for doc in rendered_validate:
                        click.echo(f"Evaluating policies in {path}")
                        cmd = ["conftest", "test", "--policy", path, "-"]
                        child_process = subprocess.Popen(
                            cmd,  # type: ignore[arg-type]
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            text=True,
                        )
                        stdout, _ = child_process.communicate(input=doc)
                        click.echo(stdout)

                        if not stdout or child_process.returncode != 0:
                            raise click.ClickException("Validation failed")

    if lint_errors_count > 0:
        raise click.ClickException(f"{lint_errors_count} Lint violations")


if __name__ == "__main__":
    test_services()
