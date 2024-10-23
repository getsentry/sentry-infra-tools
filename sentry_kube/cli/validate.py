import click
import subprocess
from pathlib import Path
from libsentrykube.config import Config
from libsentrykube.service import (
    get_service_path,
)
from libsentrykube.lint import lint_and_print_doc, get_kubelinter_config
from libsentrykube.utils import workspace_root
from libsentrykube.kube import render_services

__all__ = ("validate",)


@click.command()
@click.pass_context
@click.argument("service", type=str)
@click.option(
    "--skip-lint",
    is_flag=True,
    default=False,
    help="Only run the linter. Default `False`",
)
@click.option(
    "--skip-unit-tests",
    is_flag=True,
    default=False,
    help="Only run the unit tests. Default `False`",
)
def validate(
    ctx: click.core.Context, service: str, skip_lint: bool, skip_unit_tests: bool
):
    """
    Renders the specified service and then runs linter and unit tests.
    """
    customer_name = ctx.obj.customer_name
    cluster_name = ctx.obj.cluster_name

    k8s_config = Config().silo_regions[customer_name].k8s_config

    rendered = render_services(customer_name, cluster_name, [service])
    service_path = get_service_path(service)

    root_config = workspace_root() / k8s_config.root
    policies_paths = [
        Path(root_config) / "policy",
        Path(service_path) / "policy",
    ]

    lint_errors = 0
    for doc in rendered:
        if not skip_lint:
            click.echo(f"Linting {service}")
            include, exclude = get_kubelinter_config(
                customer_name, cluster_name, service
            )

            lint_errors += lint_and_print_doc(doc, include, exclude)

            if lint_errors > 0:
                raise click.ClickException(f"{lint_errors} Lint violations")

        if not skip_unit_tests:
            click.echo(f"Testing {service}")
            for path in policies_paths:
                if path.exists() and path.is_dir():
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
