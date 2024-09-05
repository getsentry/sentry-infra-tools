from libsentrykube.config import Config
from libsentrykube.customer import get_project
from libsentrykube.customer import get_region
from libsentrykube.gcloud import lookup_zone


def build_ssh_command(ctx, host, project, user, ssh_key_file, region, zone, ssh_args):
    customer_name = ctx.obj.customer_name
    cluster_name = ctx.obj.cluster_name

    config = Config()
    if not project:
        project = get_project(config, customer_name, cluster_name)

    if not region:
        region = get_region(config, customer_name, cluster_name)

    if not zone:
        zone = lookup_zone(host, project, region)

    if user:
        host = f"{user}@{host}"

    cmd = (
        "gcloud",
        "compute",
        "ssh",
        host,
        "--tunnel-through-iap",
        "--zone",
        f"{region}-{zone}",
        "--project",
        project,
        "--ssh-key-expire-after=1d",
    )
    if ssh_key_file:
        cmd += ("--ssh-key-file", ssh_key_file)

    if ssh_args:
        cmd += ("--",) + ssh_args

    return cmd
