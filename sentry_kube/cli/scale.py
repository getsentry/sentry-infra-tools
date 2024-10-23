import json
import time
from datetime import datetime

import click

from libsentrykube.kube import kube_get_client
from libsentrykube.utils import die, ensure_libsentrykube_folder

__all__ = ("scale",)


def datetime_from_session_file(f):
    from datetime import datetime

    return datetime.utcfromtimestamp(int(f.stem))


def tabulate(header, rows):
    from tabulate import tabulate as t

    click.echo(f"\n{t([header] + rows, headers='firstrow')}\n")


@click.command()
@click.option("--namespace", "-n", default="default", required=False)
@click.option("--selector", "-l", required=False)
@click.option("--revert", is_flag=True, help="Revert a session.")
@click.option("--pause", is_flag=True, help="Sets all replicas to zero.")
def scale(namespace, selector, revert, pause):
    if revert and pause:
        die("You can't pass both --revert and --pause.")

    start = int(time.time())
    start_dt = datetime.utcfromtimestamp(start)
    suffix = ".sentry-kube-session"

    tmp = ensure_libsentrykube_folder() / "sessions"
    tmp.mkdir(exist_ok=True)

    from kubernetes import client

    _client = kube_get_client()
    AppsV1Api = client.AppsV1Api(_client)
    AutoscalingV1Api = client.AutoscalingV1Api(_client)

    if revert:
        sessions = list(tmp.glob(f"*{suffix}"))
        if not sessions:
            die("No sessions to revert.")

        if len(sessions) > 1:
            click.echo("Multiple sessions found:\n")
            sessions = sorted(sessions, reverse=True)
            table = []
            for idx, file in enumerate(sessions):
                with file.open() as f:
                    state = json.load(f)
                table.append(
                    (
                        idx,
                        int(
                            (
                                start_dt - datetime_from_session_file(file)
                            ).total_seconds()
                        ),
                        state["query"]["namespace"],
                        state["query"]["selector"],
                    )
                )
            tabulate(("id", "age", "namespace", "selector"), table)
            click.echo()

            while True:
                idx = click.prompt("Which session to revert?", type=int, default=0)
                if idx < len(sessions):
                    break

                click.echo(f"Error: {idx} is not a valid choice")

            click.echo()
        else:
            idx = 0

        session = sessions[idx]
        with session.open() as f:
            state = json.load(f)

        diff = int((start_dt - datetime_from_session_file(session)).total_seconds())
        click.echo(
            "Resuming session from " + click.style(f"{diff} seconds ago", bold=True)
        )
        click.echo()
    else:
        state = {
            "ts": start,
            "query": {
                "namespace": namespace,
                "selector": selector,
            },
            "deployments": [
                {"name": i.metadata.name, "replicas": i.status.replicas}
                for i in AppsV1Api.list_namespaced_deployment(
                    namespace=namespace,
                    label_selector=selector,
                ).items
                if i.status.replicas is not None and i.status.replicas > 0
            ],
        }

    if not state["deployments"]:
        die("No deployments found.")

    table = []
    for deployment in state["deployments"]:
        name = f"{state['query']['namespace']}/{deployment['name']}"
        replicas = deployment["replicas"]
        table.append([name, replicas])

    table.sort(key=lambda i: i[1], reverse=True)

    if not pause:
        tabulate(("deployment", "replicas"), table)

    for entry in table:
        name, replicas = entry

        target = 0

        if not pause and not revert:
            click.echo(f"{name} is at {replicas} replicas.")
            target = click.prompt("Desired replicas", type=click.IntRange(0, 420))

        entry.append(target)

    tabulate(("deployment", "replicas", "target replicas"), table)

    namespace = state["query"]["namespace"]
    session_file = tmp / f"{state['ts']}{suffix}"

    # Gather all HPAs so later on we can see whether or not a Deployment
    # is referenced by one.
    # In our templating we have the same name for HPAs and Deployments,
    # but it isn't good to rely on that.
    hpas = AutoscalingV1Api.list_namespaced_horizontal_pod_autoscaler(
        namespace=namespace
    )
    deployments_hpas = {}
    for hpa in hpas.items:
        ref = hpa.spec.scale_target_ref
        if ref.kind != "Deployment":
            continue
        deployment_name = ref.name
        hpa_name = hpa.metadata.name
        if hpa_name != deployment_name:
            click.secho(
                f"INFO: HPA {hpa_name} references a Deployment that isn't of the "
                f"same name: {deployment_name}. You should correct this in the YAMLs."
            )
        deployments_hpas[deployment_name] = hpa_name

    if not revert:
        click.confirm("Do it?", abort=True)

        with session_file.open("w") as f:
            json.dump(state, f, indent=2)

        click.secho(
            "This may be reverted by running `sentry-kube scale --revert` later.",
            bold=True,
        )

        click.echo()
        for d in state["deployments"]:
            name = d["name"]
            current = (
                AppsV1Api.read_namespaced_deployment_scale(
                    namespace=namespace, name=name
                ).spec.replicas
                or 0
            )

            # If there is an autoscaler referencing the Deployment,
            # we need to set both the min and max replicas to target,
            # otherwise it'll just autoscale away from what we want.
            hpa_name = deployments_hpas.get(name)
            if hpa_name:
                click.echo(
                    f'Setting referent HPA "{namespace}/{hpa_name}" min/max to {target}'
                )
                AutoscalingV1Api.patch_namespaced_horizontal_pod_autoscaler(
                    namespace=namespace,
                    name=hpa_name,
                    # Even though the python kubernetes client says
                    # min_replicas, the PATCH needs to have minReplicas.
                    body={"spec": {"minReplicas": target, "maxReplicas": target}},
                )
            else:
                # Otherwise, just set the target replicas.
                click.echo(f'Scaling "{namespace}/{name}" {current} -> {target}')
                AppsV1Api.patch_namespaced_deployment_scale(
                    namespace=namespace, name=name, body={"spec": {"replicas": target}}
                )

        click.echo()

    if not click.confirm("Would you like to revert this?"):
        raise click.Abort()

    click.echo()
    for d in state["deployments"]:
        current = (
            AppsV1Api.read_namespaced_deployment_scale(
                namespace=namespace, name=d["name"]
            ).spec.replicas
            or 0
        )
        target = d["replicas"]

        # If there is an autoscaler referencing the Deployment,
        # we need to set both the min and max replicas to target,
        # otherwise it'll just autoscale away from what we want.
        hpa_name = deployments_hpas.get(name)
        if hpa_name:
            click.echo(
                f'Setting referent HPA "{namespace}/{hpa_name}" min/max to {target}'
            )
            AutoscalingV1Api.patch_namespaced_horizontal_pod_autoscaler(
                namespace=namespace,
                name=hpa_name,
                # Even though the python kubernetes client says
                # min_replicas, the PATCH needs to have minReplicas.
                body={"spec": {"minReplicas": target, "maxReplicas": target}},
            )
        else:
            # Otherwise, just set the target replicas.
            click.echo(f'Scaling "{namespace}/{d["name"]}" {current} -> {target}')
            AppsV1Api.patch_namespaced_deployment_scale(
                namespace=namespace, name=d["name"], body={"spec": {"replicas": target}}
            )

    try:
        session_file.unlink()
    except FileNotFoundError:
        pass
