import click
from libsentrykube.utils import die
from libsentrykube.utils import execvp
from libsentrykube.utils import get_pubkey

__all__ = ("scp",)


@click.command()
@click.option(
    "-C",
    "compression",
    type=bool,
    is_flag=True,
    required=False,
    default=False,
    help="Use compression.",
)
@click.argument("source")
@click.argument("target")
@click.pass_context
def scp(ctx, *, compression, source, target):
    """
    Use SCP to transfer a file.
    You will also need an ephmeral bastion connection, otherwise this won't work.
    Use `connect` to get a short-lived sshuttle tunnel.
    """
    try:
        private_id_key_path = get_pubkey()
    except Exception as e:
        die(e)

    args = [source, target]
    for idx, location in enumerate(args):
        # A ':'' means a host:file split, and we are trying to find
        # a hostname.
        if ":" in location:
            host, file = location.split(":", 1)
            if "." not in host:
                args[idx] = f"{host}.i.getsentry.net:{file}"

    if compression:
        args = ["-C", *args]

    cmd = (
        "scp",
        "-i",
        f"{private_id_key_path}",
        *args,
    )
    execvp(cmd)
