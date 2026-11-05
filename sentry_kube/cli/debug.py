import subprocess

import click
import json
import tempfile

from libsentrykube.utils import ensure_kubectl, should_run_with_empty_context
from sentry_kube.cli.util import getsentry_debug_image


__all__ = ("debug",)

# Containers to exclude when auto-selecting a container for debugging
EXCLUDED_CONTAINERS = {"envoy", "pgbouncer"}


@click.command(
    context_settings=dict(
        allow_extra_args=True,
        allow_interspersed_args=False,
        help_option_names=["-h", "--help"],
        ignore_unknown_options=True,
    ),
)
@click.argument("pod_name", metavar="POD")
@click.option("--container", "-c", help="Target container. Auto-selected if omitted.")
@click.option(
    "--image",
    "-i",
    help="Debug image. Defaults to the target's image, or its -debug variant for getsentry.",
)
@click.option("--namespace", "-n", default="default", show_default=True)
@click.option(
    "--privileged", "-p", is_flag=True, help="Run the debug container as privileged."
)
@click.option("--quiet", "-q", is_flag=True, help="Don't print the kubectl command.")
@click.pass_context
def debug(ctx, pod_name, container, image, namespace, privileged, quiet):
    """
    Convenient wrapper for running "kubectl debug" with env and volume mounts

    Anything after POD is passed to "kubectl debug" as is. Without any extra
    arguments, a shell is started in the debug container.
    """
    args = ctx.args
    context = ctx.obj.context_name
    customer = ctx.obj.customer_name
    quiet = ctx.obj.quiet_mode or quiet

    cmd = [f"{ensure_kubectl()}"]
    if not should_run_with_empty_context():
        cmd += ["--context", context]

    from kubernetes import client as k8s_client
    from libsentrykube.kube import kube_get_client

    api = k8s_client.CoreV1Api(kube_get_client())
    api_client = k8s_client.ApiClient()

    # read spec of the target pod
    resp = api.read_namespaced_pod(namespace=namespace, name=pod_name)
    # convert to camelCase
    resp = api_client.sanitize_for_serialization(resp)

    # for writing the custom debug profile
    tmp = tempfile.NamedTemporaryFile(delete=True)

    if container:
        container_spec = next(
            (item for item in resp["spec"]["containers"] if item["name"] == container),
            None,
        )
    elif len(resp["spec"]["containers"]) == 1:
        container_spec = resp["spec"]["containers"][0]
        container = container_spec["name"]
    else:
        container_spec = next(
            (
                item
                for item in resp["spec"]["containers"]
                if item["name"] not in EXCLUDED_CONTAINERS
            ),
            None,
        )
        container = container_spec["name"]

    # use the target pod image by default
    if not image:
        image = getsentry_debug_image(container_spec["image"])

    cmd += [
        "debug",
        "-it",
        f"--namespace={namespace}",
        f"--image={image}",
        f"--target={container}",
        f"--custom={tmp.name}",
    ]
    cmd += [pod_name, *args]
    if not args:
        # no command given: start a shell instead of the image entrypoint,
        # which fails until the subPath files are linked in place
        cmd += ["--", "sh"]

    if not quiet:
        click.echo(
            "Running the following for customer "
            f"{click.style(customer, fg='yellow', bold=True)}:\n\n"
            f"+ {' '.join(cmd)}"
        )
        click.echo()

    volume_mounts = []
    mount_instructions = ""
    for vm in container_spec["volumeMounts"]:
        if "subPath" in vm:
            del vm["subPath"]
            original_mount_path = vm["mountPath"]
            original_mount_dir = "/".join(vm["mountPath"].split("/")[:-1])
            new_mount_path = f"/subPathMounts/{vm['name']}/{original_mount_dir}"
            vm["mountPath"] = new_mount_path
            mount_instructions += f"  mkdir -p {original_mount_dir}\n"
            mount_instructions += f"  ln -sf /subPathMounts/{vm['name']}/{original_mount_path} {original_mount_path}\n"
        # subPath mounts of the same volume into the same directory collapse
        # into one whole-volume mount; mountPaths must be unique
        if vm not in volume_mounts:
            volume_mounts.append(vm)

    if mount_instructions:
        click.echo("Subpath mounts are not allowed for ephemeral containers.")
        click.echo(
            "https://github.com/kubernetes-client/python/blob/master/kubernetes/docs/V1EphemeralContainer.md"
        )
        click.echo("However, you can achieve almost the same by running:")
        click.echo(mount_instructions)

    custom_debug_profile = {
        # set the same env and volumeMounts as of the original container
        "env": container_spec["env"] if "env" in container_spec else [],
        "volumeMounts": volume_mounts,
        # drop security features
        "securityContext": {
            "allowPrivilegeEscalation": True,
            "readOnlyRootFilesystem": False,
            "runAsNonRoot": False,
            "runAsUser": 0,
            "runAsGroup": 0,
            # access the target's filesystem via /proc/<pid>/root (e.g. chroot),
            # as the target usually runs as a different user
            "capabilities": {"add": ["SYS_PTRACE"]},
        },
    }

    if privileged:
        custom_debug_profile["securityContext"]["privileged"] = True
        custom_debug_profile["securityContext"]["capabilities"]["add"].append(
            "CAP_SYS_ADMIN"
        )

    # write the custom debug profile
    tmp.write(json.dumps(custom_debug_profile).encode("utf-8"))
    tmp.flush()

    subprocess.run(cmd, check=False)
