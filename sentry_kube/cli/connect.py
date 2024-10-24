import atexit
import os
import subprocess
from secrets import token_hex

import click
from libsentrykube.bastions import create_ephemeral_bastion
from libsentrykube.bastions import reset_death_timestamp
from libsentrykube.config import Config
from libsentrykube.config import Site
from libsentrykube.customer import load_customer_data
from libsentrykube.customer import get_service_ip_mapping
from libsentrykube.gcloud import extract_bastion_user_sa_info
from libsentrykube.google_auth import derive_oidc_credentials
from libsentrykube.utils import block_until_sshd_ready
from libsentrykube.utils import die
from libsentrykube.utils import get_pubkey
from libsentrykube.utils import macos_notify
from libsentrykube.utils import poke_sudo
from libsentrykube.utils import which
from libsentrykube.vault import authenticate as vault_authenticate
from libsentrykube.vault import sign_pubkey as vault_sign_pubkey
from sentry_kube.cli import SESSION_FILE

__all__ = ("connect",)

TTL_MIN = 300
TTL_MAX = 14400
TTL_DEFAULT = 3600


@click.command(
    help=f"""

Deploys an ephemeral bastion for access to a customer.

The default TTL for created bastions is 3600 seconds.
You may specify up to {TTL_MAX} and as low as {TTL_MIN}.
"""
)
@click.argument("customer", default="saas")
@click.option("--ttl", type=int, required=False, default=TTL_DEFAULT)
@click.option("--site", "site_name", type=click.Choice(Site.names()), required=False)
@click.option("--zone", "zone_override", type=str, required=False, default=None)
@click.option("--use-standard-ssh-port", is_flag=True)
@click.pass_context
def connect(ctx, *, customer, ttl, site_name, zone_override, use_standard_ssh_port):
    sshuttle = which("sshuttle")
    if sshuttle is None:
        die("The executable `sshuttle` is required, but wasn't found.")

    if (ttl < TTL_MIN) or (ttl > TTL_MAX):
        die(f"TTL must be between {TTL_MIN} and {TTL_MAX}.")

    click.echo(f"Operating on customer '{customer}'")
    config = Config()

    customer_data = load_customer_data(config, customer, ctx.obj.cluster_name)
    customer_google_project = customer_data["project"]
    click.echo(f"Operating on google project '{customer_google_project}'")

    service_ips = []
    if customer != "saas" and customer != "test-control" and customer != "de":
        service_ips = [
            f"{ip}/32"
            for ip in get_service_ip_mapping(
                customer_google_project, region=customer_data["region"][:-2]
            )
        ]
    click.echo(f"Tunnels to {service_ips}")

    config = config.silo_regions[customer]
    if site_name:
        site = Site.get(site_name)
    else:
        site = config.bastion_site

    if zone_override:
        zone_override = f"{site.region}-{zone_override}"

    click.echo("sshuttle requires superuser - let's get that out of the way first.")
    poke_sudo()

    try:
        id_key_path = get_pubkey()
        bastion_user_sa_info = extract_bastion_user_sa_info()
    except Exception as e:
        die(e)

    BASTION_SPAWNER_ENDPOINT = os.environ.get(
        "BASTION_SPAWNER_ENDPOINT", config.bastion_spawner_endpoint
    )

    # XXX: Need to supplement with some additional keys, else from_service_account_info
    #      won't be happy.
    bastion_user_sa_info["token_uri"] = "https://oauth2.googleapis.com/token"
    sa_oidc_credentials = derive_oidc_credentials(
        bastion_user_sa_info,
        target_audience=BASTION_SPAWNER_ENDPOINT,
    )

    click.echo(f"Using service account {sa_oidc_credentials.signer_email}")
    name_from_sa_email = sa_oidc_credentials.signer_email.split("@")[0].split("-")[-1]
    bastion_name = f"ephemeral-bastion-{name_from_sa_email}-{token_hex(4)}"

    try:
        vault_token = vault_authenticate(bastion_user_sa_info)
        cert = vault_sign_pubkey(vault_token, id_key_path)
        bastion_ip, bastion_ssh_port = create_ephemeral_bastion(
            bastion_spawner_endpoint=BASTION_SPAWNER_ENDPOINT,
            sa_oidc_credentials=sa_oidc_credentials,
            project=customer_google_project,
            network=site.network,
            subnetwork=site.subnetwork,
            region=site.region,
            zone=zone_override or site.zone,
            name=bastion_name,
            ttl_seconds=ttl,
            use_standard_ssh_port=use_standard_ssh_port,
        )
    except Exception as e:
        die(e)

    click.echo(f"Great success. Bastion IP: {bastion_ip}")
    block_until_sshd_ready(host=bastion_ip, port=bastion_ssh_port)

    cmd = (
        sshuttle,
        "--ssh-cmd",
        # TODO: set up host key signing with Vault - these ssh options are just to
        # suppress the host identity verification for now.
        # Google seems to be reusing IPs more frequently.
        # https://www.vaultproject.io/docs/secrets/ssh/signed-ssh-certificates#host-key-signing
        f"ssh -i {cert} "
        f"-p {bastion_ssh_port} "
        "-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null ",
        "-r",
        f"bastion_user@{bastion_ip}",
        # The relevant destination subnets we're interested in
        # tunnelling through the bastion.
        # This is from matt's sshuttle alias, some parts might be out of date.
        "192.168.142.0/24",
        "192.168.208.0/20",
        "172.16.0.0/28",  # only this one is necessary for single tenant it seems
        "172.16.0.16/28",
        "172.16.16.0/28",
        "172.16.17.0/28",
        "172.16.18.0/28",
        "172.16.19.0/28",
        "172.16.26.0/28",
        "10.1.128.0/19",
        "10.1.64.0/18",
        "10.1.0.0/18",
        *service_ips,
    )

    click.echo(f"+ {' '.join(cmd)}")
    sshuttle_proc = subprocess.Popen(cmd)

    SESSION_FILE.write_text(customer)
    atexit.register(SESSION_FILE.unlink)

    click.echo(
        f"""
You now have a sshuttle tunnel (pid {sshuttle_proc.pid})!
In another shell, use `sentry-kube ssh` to connect to production hosts.
"""
    )

    macos_notify(
        "sentry-kube connect",
        f"You now have a sshuttle tunnel (pid {sshuttle_proc.pid})!",
    )

    def kill_sshuttle():
        click.echo(f"Sending SIGTERM to sshuttle (pid {sshuttle_proc.pid})")
        sshuttle_proc.terminate()
        click.echo(f"Waiting for sshuttle (pid {sshuttle_proc.pid}) to be done.")
        sshuttle_proc.wait()

    atexit.register(kill_sshuttle)
    atexit.register(
        reset_death_timestamp,
        bastion_spawner_endpoint=BASTION_SPAWNER_ENDPOINT,
        sa_oidc_credentials=sa_oidc_credentials,
        project=customer_google_project,
        region=site.region,
        zone=zone_override or site.zone,
        name=bastion_name,
    )

    click.echo(
        "Registered atexit hooks to kill sshuttle and reap the bastion on exit."
        "Sleeping forever."
    )
    sshuttle_proc.wait()
