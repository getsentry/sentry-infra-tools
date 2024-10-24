from random import randint

import click
from google.auth.transport import requests

from libsentrykube import httpx_client
from libsentrykube.utils import macos_notify


def create_ephemeral_bastion(
    *,
    bastion_spawner_endpoint,
    sa_oidc_credentials,
    project,
    region,
    zone,
    name,
    ttl_seconds,
    network="",
    subnetwork="",
    use_standard_ssh_port=False,
):
    ssh_port = 22 if use_standard_ssh_port else randint(6000, 7000)

    if not network:
        network = "global/networks/default"

    if not subnetwork:
        subnetwork = f"regions/{region}/subnetworks/default"

    click.echo(
        f"Submitting request ({bastion_spawner_endpoint}) to create {name} "
        f"in project {project}, region {region}, zone {zone}, network {network} "
        f"with ttl {ttl_seconds}s."
    )

    data = {
        "name": name,
        "project": project,
        "zone": f"{region}-{zone}",
        "ttl_seconds": ttl_seconds,
        "network": network,
        "subnetwork": subnetwork,
        "ssh_port": ssh_port,
    }

    sa_oidc_credentials.refresh(requests.Request())
    token = sa_oidc_credentials.token

    click.echo(
        "Waiting for a response from the bastion-spawner indefinitely. "
        "It should take about 10s to create a new instance, on bad days "
        "it's around a minute."
    )
    resp = httpx_client.post(
        bastion_spawner_endpoint,
        json=data,
        timeout=None,
        headers={"Authorization": f"Bearer {token}"},
    )

    if resp.status_code != 200:
        raise Exception(
            f"Got non-200 response code: {resp.status_code}.\nError: {resp.text}"
        )

    external_ip = resp.text.strip()
    return external_ip, ssh_port


def reset_death_timestamp(
    *,
    bastion_spawner_endpoint,
    sa_oidc_credentials,
    project,
    region,
    zone,
    name,
):
    click.echo(
        "Requesting to reset the bastion's death timestamp "
        "so that it can be reaped next time the reaper runs."
    )

    macos_notify("sentry-kube connect", "Bastion connection closed.")

    sa_oidc_credentials.refresh(requests.Request())
    token = sa_oidc_credentials.token

    data = {
        "name": name,
        "project": project,
        "zone": f"{region}-{zone}",
    }

    resp = httpx_client.post(
        f"{bastion_spawner_endpoint}/death-sentence",
        json=data,
        timeout=30,  # This usually takes a few seconds.
        headers={"Authorization": f"Bearer {token}"},
    )

    if resp.status_code not in (200, 404):
        click.echo(f"Got non-success response code: {resp.status_code}")
        click.echo("Error:")
        click.echo(resp.text)
