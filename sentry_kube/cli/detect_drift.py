from .apply import diff
import click
import os
from jinja2.exceptions import TemplateError
from libsentrykube.service import get_service_names
from libsentrykube.jira import JiraConfig, drift_jira_issue
from libsentrykube.events import report_event_to_datadog

__all__ = ("detect_drift",)


MAX_JIRA_DESCRIPTION_LENGTH = 32000


@click.command()
@click.option(
    "--jira", "-j", is_flag=True, default=False, help="Attempts to create a jira ticket"
)
@click.pass_context
def detect_drift(ctx, jira):
    """
    This command runs a `sentry-kube diff` on all available services and reports
    all outstanding changes. If the Jira flag is enabled, it will also
    attempt to create a ticket per drifted service.
    """
    services = get_service_names()
    click.echo(services)

    url = os.getenv("JIRA_URL")
    project_key = os.getenv("JIRA_PROJECT_KEY")
    user_email = os.getenv("JIRA_USER_EMAIL")
    api_token = os.getenv("JIRA_API_TOKEN")
    jiraConf = JiraConfig(url, project_key, user_email, api_token)

    for service in services:
        output = None
        try:
            output = ctx.invoke(diff, services=[service], important_diffs_only=True)
        except TemplateError as e:
            click.secho(e, fg="red")
            if jira:
                error_report = (
                    "{code}\n" + f"Jinja2 error for service {service}: {e}" + "\n{code}"
                )
                drift_jira_issue(jiraConf, ctx.obj.customer_name, service, error_report)
        # _run_kubectl_diff raises kubectl errors as ClickExceptions
        except click.ClickException as e:
            click.secho(e, fg="red")
            if jira:
                error_report = (
                    "{code}\n"
                    + f"kubectl error for service {service}: {e}"
                    + "\n{code}"
                )
                drift_jira_issue(jiraConf, ctx.obj.customer_name, service, error_report)

        if output:
            click.echo(f"service {service} drifted!")
            drift_report = (
                "{code}\n"
                + "\n".join(output)[:MAX_JIRA_DESCRIPTION_LENGTH]
                + "\n{code}"
            )
            if jira:
                drift_jira_issue(jiraConf, ctx.obj.customer_name, service, drift_report)

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
