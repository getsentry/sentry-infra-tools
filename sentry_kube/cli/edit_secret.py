import copy
import difflib
from base64 import b64decode
from base64 import b64encode

import click
from kubernetes.client import CoreV1Api
from kubernetes.client import V1Secret
from kubernetes.client.rest import ApiException
from libsentrykube.events import report_event_for_service
from libsentrykube.utils import kube_get_client

__all__ = ("edit_secret",)

BACKUP_SUFFIX = ".secretbackup"


@click.command()
@click.argument("secret-name", type=str, default="getsentry-secrets")
@click.argument("namespace", type=str, default="default")
@click.option("--no-backup", is_flag=True)
@click.pass_context
def edit_secret(ctx, secret_name, namespace, no_backup):
    """
    kubectl edit secret, but made convenient.
    """

    client = kube_get_client()
    api = CoreV1Api(client)

    secret = api.read_namespaced_secret(secret_name, namespace)
    data = secret.data or {}
    backup_data = copy.deepcopy(data)

    k = click.prompt(
        "What do you want to edit?", type=click.Choice(("NEW", *data.keys()))
    )
    if k == "NEW":
        k = click.prompt("Key name", type=str)
        data[k] = ""

    value = b64decode(data[k]).decode()
    edited_value = click.edit(text=value, require_save=False).strip()

    difflines = list(
        difflib.unified_diff(
            # Adding trailing newline for each line, otherwise difflib would
            # combine the diffs output in a single line like
            #   -http://10.138.15.227:15672//+http://10.138.15.227:15671//
            [f"{line}\n" for line in value.split("\n")],
            [f"{line}\n" for line in edited_value.split("\n")],
            fromfile=f"(live) secret_name={secret_name}, key={k}",
            tofile=f"(new) secret_name={secret_name}, key={k}",
        )
    )
    if not difflines:
        click.echo("No differences.")
        raise click.Abort()

    diff_out = "".join(difflines)

    click.confirm(
        f"""Are you sure you want to update key `{k}` with:

{diff_out}

""",
        abort=True,
    )

    if not no_backup:
        backup_secret_name = f"{secret_name}{BACKUP_SUFFIX}"

        click.echo(f"Backup current secret to {backup_secret_name}")

        try:
            api.patch_namespaced_secret(
                backup_secret_name, namespace, {"data": backup_data}
            )
        except ApiException as exc:
            if exc.status == 404:
                body = V1Secret()
                body.metadata = {"name": backup_secret_name}
                body.data = backup_data
                api.create_namespaced_secret(namespace, body)
            else:
                raise

    data[k] = b64encode(edited_value.encode()).decode()
    api.patch_namespaced_secret(secret_name, namespace, {"data": data})

    try:
        report_event_for_service(
            ctx.obj.customer_name,
            ctx.obj.cluster_name,
            operation="edit-secret",
            secret_name=secret_name,
            quiet=ctx.obj.quiet_mode,
        )
    except Exception as e:
        click.echo("!! Could not report an event to DataDog:")
        click.secho(e, bold=True)
