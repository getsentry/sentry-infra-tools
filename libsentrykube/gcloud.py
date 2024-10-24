import json
import os
import subprocess
from pathlib import Path
from typing import Any, Mapping, List

import googleapiclient.discovery

import click
from libsentrykube.utils import die


def extract_bastion_user_sa_info() -> Mapping[str, Any]:
    # If GOOGLE_APPLICATION_CREDENTIALS is specified, read the service account
    # credentials from that file path.
    sa_creds_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if sa_creds_file:
        return json.loads(Path(sa_creds_file).read_text())

    import sqlite3

    conn = sqlite3.connect(Path("~/.config/gcloud/credentials.db").expanduser())
    c = conn.cursor()
    item = c.execute(
        'SELECT value FROM "credentials" WHERE account_id LIKE '
        '"bastion-user-%@internal-sentry.iam.gserviceaccount.com"'
    ).fetchone()
    if not item:
        raise Exception(
            "Failed to extract credentials for your bastion-user "
            "Service Account from gcloud. "
            "See https://github.com/getsentry/ops/blob/master/docs/gcloud.md"
        )
    return json.loads(item[0])


def lookup_zone(host, project, region):
    out = subprocess.check_output(
        [
            "gcloud",
            "compute",
            "instances",
            "list",
            "--project",
            project,
            "--filter",
            f"name={host}",
            "--format",
            "get(zone)",
        ],
    )

    if out:
        zone = out.decode("utf-8").strip()[-1]
        click.echo(
            f"Looked up zone for `{host}` in project `{project}` and region `{region}`: {zone=}"
        )
        return zone
    else:
        click.echo(
            f"Unable to look up zone for `{host}` in project `{project}` and region `{region}`"
        )
        out = subprocess.check_output(
            [
                "gcloud",
                "compute",
                "instances",
                "list",
                "--project",
                project,
                "--filter",
                f"zone:{region}",
                "--format",
                "get(name)",
            ],
        )

        hosts = sorted(out.decode("utf-8").split())
        filtered_hosts = [h for h in hosts if not h.startswith("gke-")]
        newline = "\n"
        click.echo(f"Did you mean to use one of: \n\n{newline.join(filtered_hosts)}")
        die()


def get_all_gke_clusters(project: str) -> List[Any]:
    container_resource = googleapiclient.discovery.build("container", "v1")
    clusters_resource = container_resource.projects().locations().clusters()
    request = clusters_resource.list(parent=f"projects/{project}/locations/-")
    return request.execute()["clusters"]


def get_channel_versions(project: str, zone: str, channel: str) -> List[str]:
    container_resource = googleapiclient.discovery.build("container", "v1")
    server_config_request = (
        container_resource.projects()
        .zones()
        .getServerconfig(projectId=project, zone=zone)
    )
    response = server_config_request.execute()
    try:
        channel_obj = [ch for ch in response["channels"] if ch["channel"] == channel][0]
        return channel_obj["validVersions"]
    except KeyError:
        raise Exception("Bad channel API response")
