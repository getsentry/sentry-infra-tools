import click

from libsentrykube.ssh import build_ssh_command
from libsentrykube.utils import execvp

__all__ = ("ssh",)


@click.command()
@click.argument("host")
@click.option(
    "--project",
    "-p",
    required=False,
    help="GCP project for the instance, required if not in 'internal-sentry'",
)
@click.option("--user", "-u", required=False, help="SSH username.")
@click.option(
    "--ssh-key-file",
    "-k",
    envvar="SENTRY_KUBE_SSH_KEY_FILE",
    required=False,
    help="Points gcloud compute ssh to your keyfile",
)
@click.argument("ssh_args", nargs=-1)
@click.pass_context
def ssh(ctx, host, project, user, ssh_key_file, ssh_args):
    """
    SSH into a host.
    You can specify an IP, hostname, or a service name (ST only).
    """
    execvp(build_ssh_command(ctx, host, project, user, ssh_key_file, ssh_args))
