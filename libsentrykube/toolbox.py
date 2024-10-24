from time import time

from libsentrykube.utils import ensure_kubectl

TOOLBOX_TAG = "20200810-0"


def get_toolbox_cmd(
    context, user, clean, clean_all, *, namespace=None, nodepool="default"
):
    cmd = [
        f"{ensure_kubectl()}",
        "--context",
        context,
    ]

    if namespace:
        cmd += [
            "--namespace",
            namespace,
        ]

    if clean:
        cmd += [
            "delete",
            "pods",
            f"--selector=service=toolbox,user={user}",
        ]

    elif clean_all:
        cmd += [
            "delete",
            "pods",
            "--selector=service=toolbox",
        ]

    else:
        cmd += [
            "run",
            "-i",
            "--tty",
            "--rm",
            "--restart=Never",
            f"--labels=service=toolbox,user={user}",
            f"--image=us.gcr.io/sentryio/toolbox:{TOOLBOX_TAG}",
            f'--overrides={{"spec":{{"nodeSelector":{{"nodepool.sentry.io/name":"{nodepool}"}}}}}}',  # noqa: E501
            f"toolbox-{user}-{int(time())}",
        ]

    return cmd
