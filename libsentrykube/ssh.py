from libsentrykube.config import Config
from libsentrykube.customer import get_project


def build_ssh_command(ctx, host, project, user, ssh_key_file, ssh_args):
    customer_name = ctx.obj.customer_name
    cluster_name = ctx.obj.cluster_name

    config = Config()
    if not project:
        project = get_project(config, customer_name, cluster_name)

    if user:
        host = f"{user}@{host}"

    cmd = (
        "gcloud",
        "compute",
        "ssh",
        host,
        "--tunnel-through-iap",
        "--project",
        project,
        "--ssh-key-expire-after=1d",
    )
    if ssh_key_file:
        cmd += ("--ssh-key-file", ssh_key_file)

    if ssh_args:
        cmd += ("--",) + ssh_args

    return cmd
