import json
import time
from datetime import datetime

import click
from libsentrykube.events import report_event_for_service
from libsentrykube.kube import render_templates
from libsentrykube.utils import die

__all__ = ("run_pod",)

cmd_help = """

This command will help you test, troubleshoot or execute commands from inside
of a Pod with definition as close as possible to the original deployment.

It works by extracting the Pod spec from the deployment manifest template.
After minor changes to labels, probes and the actual command, it will run the
Pod for you to e.g. exec in.

Since the local files are rendered to get the Pod manifest, this command can be
used to test changes to sidecars or init containers.

Examples:

By default the getsentry worker deployment and sentry container is used.

\b
# Execute getsentry command
$ sentry-kube run-pod --exec -- envdir++ getsentry killswitches list

\b
# Or jump into getsentry shell using specified version as you'd do on garbage
$ sentry-kube run-pod --version a2dc0147c8ae235162215ee905c01ed00107e950  --exec -it -- envdir++ getsentry shell

\b
# Enter shell of a relay container in relay-pop deployment
$ sentry-kube -c pop-beta run-pod --service relay-pop --deployment relay-pop --container relay --exec -it -- bash

\b
# Shell inside snuba container snuba-api deployment
$ sentry-kube run-pod --service snuba --deployment snuba-api-production --container api \
        -it --command '["sh"]' --args '["-c", "while true; do sleep 1; echo sleeping; id; done"]' --exec -- bash


\b
# Clean Pods created using run-pod
$ sentry-kube run-pod --only-delete


*NOTE*: The run a container as defined in deployement and optionally to also
make it part of service so it receives traffic use combination of
`--no-clear-labels` and `--keep-args` flags.

"""


_DEFAULT_STR = "__default__"


@click.command(
    context_settings=dict(allow_extra_args=True),
    help=cmd_help,
)
@click.pass_context
@click.option(
    "-s",
    "--service",
    default=_DEFAULT_STR,
    help="sentry-kube service that contains deployment to be used for Pod template. Default `getsentry`",
)
@click.option(
    "-d",
    "--deployment",
    default=_DEFAULT_STR,
    help="Deployment, StatefulSet or DaemonSet to use for Pod template. Default `getsentry-worker-default-production`",
)
@click.option(
    "-c",
    "--container",
    default="sentry",
    help="Which container to as main. Default `sentry`",
)
@click.option(
    "-n",
    "--namespace",
    default="default",
    help="Kubernetes namespace where to create Pod. Default `default`",
)
@click.option(
    "--version",
    help="Override for the image version to use for the selected container.",
)
@click.option(
    "--command",
    help=(
        "Override the `command` value of container spec. "
        "Can be specified as json list of strings. "
        "Setting `command` will prevent use of Dockerfile's entrypoint."
    ),
)
@click.option(
    "--args",
    help=(
        "Override the `args` value of container spec. "
        "Can be specified as json list of strings. "
        "Defaults to sleep loop. "
    ),
    default='["sh", "-c", "while true; do sleep 10; echo ..running..; done"]',
)
@click.option(
    "--keep-args",
    is_flag=True,
    default=False,
    help="Keep the original `args` configuration of container spec. ",
)
@click.option(
    "--clear-labels/--no-clear-labels",
    is_flag=True,
    default=True,
    help="Clear the labels to prevent routing traffic via service. Default `True`",
)
@click.option(
    "--delete/--no-delete",
    is_flag=True,
    default=True,
    help="Run delete command for previously spawned pods. Default `True`",
)
@click.option(
    "--only-delete",
    is_flag=True,
    default=False,
    help="If set, only run delete command for previously spawned pods and exit. Default `False`",
)
@click.option(
    "--selective-delete/--no-selective-delete",
    is_flag=True,
    default=False,
    help="If set, prompt to delete each found pod, rather than as a block",
)
@click.option(
    "--exec",
    "exec_",
    is_flag=True,
    default=False,
    help="If set, jump into the Pods container shell. Default `False`",
)
@click.option(
    "-i",
    "--interactive",
    is_flag=True,
    default=False,
    help="Used with `--exec` to open interactive shell. Keep stdin open. Default `False`",
)
@click.option(
    "-t",
    "--tty",
    is_flag=True,
    default=False,
    help="Used with `--exec` to allocate TTY for containers. Default `False`",
)
@click.option(
    "--no-security-context",
    is_flag=True,
    default=False,
    help="Remove any securityContext options.",
)
@click.option(
    "--root",
    is_flag=True,
    default=False,
    help="Run as user root(id=0) by setting securityContext on the container.",
)
@click.option(
    "--safe-to-evict",
    is_flag=True,
    default=False,
    help="By default remove the `safe-to-evict` annotation.",
)
@click.option(
    "-m",
    "--memory",
    type=int,
    help="Override the default pod memory allocation in GiB.",
)
def run_pod(
    ctx,
    service,
    deployment,
    container,
    namespace,
    version,
    command,
    args,
    keep_args,
    clear_labels,
    delete,
    only_delete,
    selective_delete,
    exec_,
    interactive,
    tty,
    no_security_context,
    root,
    safe_to_evict,
    memory,
):
    customer_name = ctx.obj.customer_name

    if customer_name == "saas" or customer_name == "de" or customer_name == "us":
        default_service, default_deployment = (
            "getsentry",
            "getsentry-web-default-common-production",
        )
    else:
        default_service, default_deployment = (
            "getsentry",
            "web",
        )

    if service == _DEFAULT_STR:
        service = default_service
    if deployment == _DEFAULT_STR:
        deployment = default_deployment

    if ctx.args and not exec_:
        die("Unknown arguments. Did you forget `--exec`?")

    cluster_name = ctx.obj.cluster_name

    import getpass

    from kubernetes import client as k8s_client
    from yaml import safe_load_all

    from libsentrykube.kube import kube_get_client

    api = k8s_client.CoreV1Api(kube_get_client())

    user = getpass.getuser().replace("_", "-")
    run_pod_labels = {
        "sk-service": "sentry-kube-run-pod",
        "sk-user": user,
    }
    if delete or only_delete:
        label_selector = ",".join(f"{k}={v}" for k, v in run_pod_labels.items())
        resp = api.list_namespaced_pod(
            namespace=namespace, label_selector=label_selector
        )
        if resp.items:
            click.secho(f"Found PodS for {label_selector}", fg="red")
            pod_names = [pod.metadata.name for pod in resp.items]
            if selective_delete:
                to_delete = []
                for name in pod_names:
                    if click.confirm(f"Delete pod {name}?"):
                        to_delete.append(name)
            else:
                to_delete = pod_names
            for name in to_delete:
                click.echo(f"- {name}")
            if click.confirm("Do you want to delete them?"):
                for name in to_delete:
                    try:
                        api.delete_namespaced_pod(namespace=namespace, name=name)
                    except k8s_client.rest.ApiException as exc:
                        if exc.status == 404:
                            # already deleted
                            pass
                        else:
                            raise
        else:
            click.secho("No Pods found for deletion.", fg="green")
        if only_delete:
            return

    # renames from args names just for better readibility
    name = deployment
    container_name = container

    for doc in safe_load_all(render_templates(customer_name, service, cluster_name)):
        if (
            doc
            and doc["kind"] in ("Deployment", "StatefulSet", "DaemonSet")
            and doc["metadata"]["name"] == name
        ):
            pod_template = doc["spec"]["template"]
            break
    else:
        click.echo(f"Can't find pod template for service:{service} and name:{name}")
        return

    for container in pod_template["spec"]["containers"]:
        if container["name"] == container_name:
            break
    else:
        click.echo(
            f"Can't find container:{container_name} in service:{service} and name:{name}"
        )
        click.echo(
            f"Available containers: {','.join(c['name'] for c in pod_template['spec']['containers'])}"
        )
        return

    # Clear labels to prevent service association and traffic routing
    if clear_labels:
        pod_template["metadata"]["labels"] = run_pod_labels
    else:
        pod_template["metadata"]["labels"].update(run_pod_labels)

    _evict_annotation = "cluster-autoscaler.kubernetes.io/safe-to-evict"
    pod_template["metadata"].setdefault("annotations", {})[_evict_annotation] = str(
        safe_to_evict
    ).lower()

    if memory is not None:
        if memory > 16 and not click.confirm("Really allocate more than 16GiB memory?"):
            return
        container.setdefault("resources", {}).setdefault("limits", {})["memory"] = (
            str(memory) + "Gi"
        )
        container.setdefault("resources", {}).setdefault("requests", {})["memory"] = (
            str(memory) + "Gi"
        )

    if command:
        try:
            command = json.loads(command)
        except Exception:
            pass
        container["command"] = command

    if args and not keep_args:
        try:
            args = json.loads(args)
        except Exception:
            pass
        container["args"] = args

    # Clear the probes since we are not running the original command
    container.pop("livenessProbe", None)
    container.pop("readinessProbe", None)
    container.pop("startupProbe", None)

    if no_security_context:
        container.pop("securityContext", None)

    if root:
        container.setdefault("securityContext", {})["runAsUser"] = 0

    if version:
        container["image"] = f"{container['image'].split(':', maxsplit=1)[0]}:{version}"

    # Prevent restart
    pod_template["spec"]["restartPolicy"] = "Never"

    # Add name to something identifiable "sentry-kube-run-pod-{name}"
    pod_name = pod_template["metadata"]["name"] = (
        f"sk-run-pod-{user}-{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}"[:63]
    )

    pod_manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        **pod_template,
    }

    resp = api.create_namespaced_pod(body=pod_manifest, namespace=namespace)
    click.secho(f"Pod created: {pod_name}", fg="green", bold=True)

    try:
        report_event_for_service(
            ctx.obj.customer_name,
            ctx.obj.cluster_name,
            operation="run-pod",
            service_name=service,
            quiet=ctx.obj.quiet_mode,
        )
    except Exception as e:
        click.echo("!! Could not report an event to DataDog:")
        click.secho(e, bold=True)

    if exec_:
        click.secho("Waiting for Pod containers to be running")

        while True:
            resp = api.read_namespaced_pod(namespace=namespace, name=pod_name)
            click.echo(f"Pod state - {resp.status.phase}")
            if resp.status.phase == "Pending":
                for container_status in resp.status.container_statuses or []:
                    for state in ("running", "terminated", "waiting"):
                        full_state = getattr(container_status.state, state)
                        if full_state:
                            msg = (
                                f"-- container: {container_status.name}, state: {state}"
                            )
                            if state != "running":
                                msg += f", reason: {full_state.reason}"
                            click.echo(msg)
                            break
                time.sleep(3)
                continue
            elif resp.status.phase == "Running":
                break
            else:
                click.secho(
                    f"Unable to exec into the Pod in state {resp.status.phase}",
                    fg="red",
                )
                raise click.Abort()

        from click import Context

        from sentry_kube.cli.kubectl import kubectl

        exec_ctx = Context(
            kubectl,
            parent=ctx,
            info_name="exec to pod",
            obj=ctx.obj,
            allow_extra_args=True,
        )
        exec_args = ["-n", namespace, "exec"]
        if interactive:
            exec_args.append("-i")
        if tty:
            exec_args.append("-t")
        exec_args.extend(
            [
                pod_name,
                "-c",
                container_name,
                "--",
            ]
        )
        exec_args.extend(ctx.args if ctx.args else ["sh"])
        exec_ctx.args = exec_args

        exec_ctx.invoke(kubectl.callback, False, False)
