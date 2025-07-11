import copy
import hashlib
import importlib
import importlib.resources
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import warnings
import click
import httpx
from functools import cache
from pathlib import Path
from typing import IO, Any, Iterable, Iterator, List, Tuple

import kubernetes
from yaml import SafeDumper, safe_dump_all, safe_load_all

# Run `sentry-kube kubectl version --short` to view the client and cluster version.
# According to https://kubernetes.io/releases/version-skew-policy/#kubectl
# kubectl is supported within one minor version (older or newer) of kube-apiserver.
# Also, you'll want to upgrade the python kubernetes client version accordingly.
KUBECTL_BINARY = os.environ.get("SENTRY_KUBE_KUBECTL_BINARY", "kubectl")
KUBECTL_VERSION = os.environ.get("SENTRY_KUBE_KUBECTL_VERSION", "1.31.10")
ENABLE_NOTIFICATIONS = os.environ.get("SENTRY_KUBE_ENABLE_NOTIFICATIONS", False)

_kube_client = None
_kube_client_context = None


SafeDumper.add_representer(
    str,
    lambda dumper, data: dumper.represent_scalar(
        "tag:yaml.org,2002:str", data, style="|" if "\n" in data else None
    ),
)


def die(msg: str = "") -> None:
    click.echo(msg, err=True)
    raise click.Abort()


def execvp(cmd: list[str], verbose: bool = True) -> None:
    import os

    if verbose:
        from shlex import quote

        click.echo(f"+ {' '.join(map(quote, cmd))}", err=True)
    os.execvp(cmd[0], cmd)


def ensure_gcloud_reauthed() -> None:
    """
    Could be better, but this makes sure gcloud is reauthenticated by
    interactively reauthing with yubikey if necessary.
    Otherwise, prints nothing and just returns.
    """
    from subprocess import PIPE, Popen

    proc = Popen(["gcloud", "config", "config-helper"], stdout=PIPE)
    # err is gonna always be None since only stdout is PIPE.
    # The reauthentication required prompt is printed on stderr.
    # stdout just contains a large amount of configuration information, so ignore.
    out, err = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"Failed to ensure gcloud reauthentication.\n\nstdout:\n{str(out)}"
        )


def poke_sudo() -> None:
    import subprocess

    while True:
        p = subprocess.run(("sudo", "true"))
        try:
            p.check_returncode()
            return
        except subprocess.CalledProcessError:
            pass


def should_run_with_empty_context() -> bool:
    """
    In certain cases (e.g. when running inside the cluster) we don't care about
    having a working kube context, and we just want to use other sentry-kube features.
    """
    return bool(os.environ.get("SENTRY_KUBE_NO_CONTEXT"))


# This creates and globally sets a kubernetes client tied to a context name,
# and can only be done once because it is expected that one invocation
# (of, say, sentry-kube) uses the same context throughout.
def kube_set_context(context_name: str, kubeconfig: str) -> None:
    if should_run_with_empty_context():
        return

    global _kube_client, _kube_client_context
    if _kube_client is not None:
        if context_name == _kube_client_context:
            return  # noop
        raise RuntimeError("Changing the kubernetes context is not allowed.")

    # Needed, otherwise new_client_from_config stalls without any output
    # if gcloud needs to be yubikey reauthed,
    # and I can't find an easy way to get the interactivity visible.
    ensure_gcloud_reauthed()

    from kubernetes.config import new_client_from_config
    from kubernetes.config.config_exception import ConfigException

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            _kube_client = new_client_from_config(
                config_file=kubeconfig, context=context_name
            )
        except ConfigException as e:
            # example: gke_internal-sentry_us-central1-b_zdpwkxst
            _, project, region, cluster = context_name.split("_")
            die(
                f"{e}\n\n"
                "Failed to create k8s client from config. You might need to run:\n"
                f"gcloud container clusters get-credentials {cluster} --region {region} --project {project}"  # noqa: E501
            )
    _kube_client_context = context_name


def kube_get_client() -> kubernetes.client.api_client.ApiClient:
    global _kube_client
    if _kube_client is None:
        raise RuntimeError(
            "No kubernetes client; "
            "kube_set_context must be called first in order to create the client."
        )
    return _kube_client


def pretty(data: Any) -> str:
    filtered_data: Iterable[Any] = filter(None, safe_load_all(data))
    return safe_dump_all(list(filtered_data))


def kube_extract_namespace(name: str) -> list[str]:
    if "/" in name:
        return name.split("/", 1)
    return ["default", name]


# This is mostly extracted somewhere out of kubernetes python code
# but modified slightly for our case.
def kube_classes_for_data(data: Any) -> Tuple[Any, Any]:
    import kubernetes

    group, _, version = data["apiVersion"].partition("/")
    if version == "":
        version = group
        group = "core"
    # Take care for the case e.g. api_type is "apiextensions.k8s.io"
    # Only replace the last instance
    group = "".join(group.rsplit(".k8s.io", 1))
    # convert group name from DNS subdomain format to
    # python class name convention
    group = "".join(word.capitalize() for word in group.split("."))
    try:
        api = getattr(kubernetes.client, f"{group}{version.capitalize()}Api")
        kind = getattr(
            kubernetes.client.models, f"{version.capitalize()}{data['kind']}"
        )
    except AttributeError as e:
        raise RuntimeError(
            f"""{e}

Workarounds:
- sentry-kube -q render SERVICE | sentry-kube kubectl apply -f -
- For "diff" command: use "sentry-kube diff-serverside"
"""
        )
    return api, kind


def kube_convert_kind_to_func(kind: str) -> str:
    return re.sub(
        r"([a-z0-9])([A-Z])", r"\1_\2", re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", kind)
    ).lower()


_workspace_root = None
_cwd = Path.cwd()


def workspace_root() -> Path:
    """
    Finds the root directory of the workspace and caches it.

    Use set_workspace_root_start() if you want to set the root directory manually, e.g.
    if you want to run a command outside a git repository.
    """
    global _workspace_root
    if _workspace_root is not None:
        return _workspace_root

    if os.environ.get("SENTRY_KUBE_ROOT"):
        _workspace_root = Path(os.environ["SENTRY_KUBE_ROOT"])
        return _workspace_root

    workspace_root, root = _cwd, Path("/")
    while (
        ".terragrunt-cache" in workspace_root.parts
        or not (workspace_root / ".git").is_dir()
    ):
        workspace_root = (workspace_root / "..").resolve()
        if workspace_root == root:
            raise RuntimeError("failed to locate a git root directory")

    _workspace_root = workspace_root
    return _workspace_root


def set_workspace_root_start(path: str) -> None:
    global _workspace_root
    _workspace_root = Path(path)


def md5_fileobj(fileobj: IO[Any]) -> str:
    md5 = hashlib.md5()
    for chunk in iter(lambda: fileobj.read(1024), b""):
        md5.update(chunk)
    return md5.hexdigest()


def block_until_sshd_ready(*, host: str, port: int = 22) -> None:
    from paramiko import Transport  # type: ignore[import]

    click.echo(f"Waiting until sshd on {host}:{port} is ready", nl=False)
    pokes = 0
    while True:
        if pokes >= 60:
            die("\nTimed out.")
        pokes += 1
        click.echo(".", nl=False)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        t = None
        try:
            # Attempt to actually negotiate an SSH session
            s.connect((host, port))
            t = Transport(
                s, gss_kex=False, gss_deleg_creds=True, disabled_algorithms=None
            )
            # start_client will block until either the handshake
            # is completed or the timeout occurs. No exception is
            # raised in the event of a timeout, so a check to
            # is_active() is required as a follow up to make
            # sure it actually worked. An exception _is_ raised
            # though if the handshake actually fails for some reason.
            t.start_client(timeout=0.5)
            if t.is_active():
                break
        except Exception:
            time.sleep(0.1)
        finally:
            if t is not None:
                t.close()
            s.close()

    click.echo()


@cache
def which(cmd: str) -> str | None:
    """
    Looks for programs specifically on the python path
    """
    path = os.environ["PATH"]
    if sys.path[0]:
        path = f"{sys.path[0]}:{path}"
    return shutil.which(cmd, path=path)


@cache
def ensure_libsentrykube_folder() -> Path:
    path = Path.home() / ".libsentrykube"
    path.mkdir(exist_ok=True)
    return path


@cache
def ensure_kubectl(
    binary: str = KUBECTL_BINARY, version: str = KUBECTL_VERSION
) -> Path:
    base = ensure_libsentrykube_folder() / "kubectl" / f"v{version}"
    path = base / binary
    if path.is_file():
        return path

    if binary != "kubectl":
        raise RuntimeError(
            f"Unsupported binary '{binary}', please install it manually or update SENTRY_KUBE_KUBECTL_BINARY."
        )

    base.mkdir(parents=True, exist_ok=True)

    click.echo(f"> kubectl v{version} is missing, so downloading")

    arch = platform.machine()
    arch = "amd64" if arch in ["x86_64", "amd64", "aarch64"] else arch

    # lol windows
    system = "darwin" if platform.system() == "Darwin" else "linux"
    url = f"https://dl.k8s.io/release/v{version}/bin/{system}/{arch}/kubectl"

    resp = httpx.get(f"{url}.sha256", follow_redirects=True)
    resp.raise_for_status()
    checksum = resp.text.strip()

    click.echo(f">> downloading {url}")

    sha256_hash = hashlib.sha256()
    tmp_path = base / ".download"
    with tmp_path.open("wb") as file:
        with httpx.stream("GET", url, follow_redirects=True) as r:
            for data in r.iter_bytes():
                file.write(data)
                sha256_hash.update(data)

    dl_checksum = sha256_hash.hexdigest()
    if dl_checksum != checksum:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        click.secho("!! Checksums do not match", fg="red", bold=True)
        click.secho(f"    checksum: {repr(checksum)}", fg="red", bold=True)
        click.secho(f"    download: {repr(dl_checksum)}", fg="red", bold=True)
        raise click.Abort()

    tmp_path.rename(path)
    path.chmod(0o755)

    return path


def chunked(lst: List[Any], n: int) -> Iterator[List[Any]]:
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def deep_merge_dict(
    into: dict[Any, Any], other: dict[Any, Any], overwrite: bool = True
) -> None:
    """
    Merges `other` dict into the `into` dict. Will perform recursive merges if those dicts contain other dicts.

    overwrite: By default, if a key exists in both dicts, the value from `other` will overwrite the value in `into`.
    You can set `overwrite=False` if you want to retain the existing value in `into`.
    """

    for k, v in other.items():
        if v is None:
            if k in into:
                into.pop(k)
        elif k in into and isinstance(v, dict) and isinstance(into[k], dict):
            deep_merge_dict(into=into[k], other=v, overwrite=overwrite)
        elif k in into:
            if overwrite:
                into[k] = copy.deepcopy(v)
        else:
            into[k] = copy.deepcopy(v)


def macos_notify(title: str, text: str) -> None:
    if ENABLE_NOTIFICATIONS and platform.system() == "Darwin":
        cmd = """
    on run argv
    display notification (item 2 of argv) with title (item 1 of argv)
    end run
    """
        subprocess.call(["osascript", "-e", cmd, title, text])


def get_pubkey() -> Path:
    if os.getenv("SSH_PUBLIC_KEY_PATH"):
        return Path(str(os.getenv("SSH_PUBLIC_KEY_PATH")))
    raise Exception(
        "Failed to find a pubkey."
        "Please ensure you've exported SSH_PUBLIC_KEY_PATH to point your "
        "SSH public key file.\n"
        "Example: export SSH_PUBLIC_KEY_PATH=/Users/rgibert/.ssh/id_ed25519.pub"
    )


def get_service_registry_data(service_registry_id: str) -> dict:
    filepath = get_service_registry_filepath()
    return json.loads(filepath.read_text())[service_registry_id]


def get_service_registry_filepath() -> Path:
    service_registry_pkg_name = "sentry_service_registry"
    try:
        importlib.import_module(service_registry_pkg_name)
        path = str(importlib.resources.files(service_registry_pkg_name).joinpath(""))
        return Path(path) / "config" / "combined" / "service_registry.json"
    except ImportError:
        root = workspace_root()
        return Path(
            f"{root}/shared_config/_materialized_configs/service_registry/combined/service_registry.json"
        )
