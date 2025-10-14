from .apply import diff
import click
from jinja2.exceptions import TemplateError
from libsentrykube.service import get_service_names
from libsentrykube.linear import drift_issue
from libsentrykube.events import report_event_to_datadog

__all__ = ("detect_drift",)


MAX_JIRA_DESCRIPTION_LENGTH = 32000


@click.command()
@click.option(
    "--issue",
    "-i",
    is_flag=True,
    default=False,
    help="Attempts to create/update an issue",
)
@click.pass_context
def detect_drift(ctx, issue):
    """
    This command runs a `sentry-kube diff` on all available services and reports
    all outstanding changes. If the issue flag is enabled, it will also
    attempt to create an issue per drifted service.
    """
    services = get_service_names()
    click.echo(services)

    for service in services:
        output = None
        try:
            output = ctx.invoke(
                diff,
                services=[service],
                important_diffs_only=True,
                exit_with_result=False,
            )
        except TemplateError as e:
            click.secho(e, fg="red")
            if issue:
                error_report = (
                    "```bash\n" + f"Jinja2 error for service {service}: {e}" + "\n```"
                )
                drift_issue(ctx.obj.customer_name, service, error_report)
        # _run_kubectl_diff raises kubectl errors as ClickExceptions
        except click.ClickException as e:
            click.secho(e, fg="red")
            if issue:
                error_report = (
                    "```bash\n" + f"kubectl error for service {service}: {e}" + "```"
                )
                drift_issue(ctx.obj.customer_name, service, error_report)

        if output:
            click.echo(f"service {service} drifted!")
            drift_report = (
                "```diff\n" + "\n".join(output)[:MAX_JIRA_DESCRIPTION_LENGTH] + "```"
            )
            if issue:
                drift_issue(ctx.obj.customer_name, service, drift_report)

            report_event_to_datadog(
                "[Drift Detection]",
                f"Drift on region: {ctx.obj.customer_name} on service: {service}",
                {
                    "service": service,
                    "region": ctx.obj.customer_name,
                    "source": "GHA-drift-detection",
                    "drift_detection": "drifted",
                },
            )

        else:
            report_event_to_datadog(
                "[Drift Detection]",
                f"Matches Configuration: {ctx.obj.customer_name} {service} is clean",
                {
                    "service": service,
                    "region": ctx.obj.customer_name,
                    "source": "GHA-drift-detection",
                    "drift_detection": "clean",
                },
            )


if __name__ == "__main__":
    detect_drift()
