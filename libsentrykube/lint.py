import subprocess
from typing import Generator, Sequence, TypedDict, cast, Set, Optional, Tuple
from json import loads
from libsentrykube.config import Config
from libsentrykube.utils import workspace_root
from pathlib import Path
import yaml
import click
import os


class KubeLinterException(Exception):
    def __init__(self, stderr: str) -> None:
        self.message = stderr
        super().__init__(self.message)


class Diagnostic(TypedDict):
    Message: str


class Metadata(TypedDict):
    FilePath: str


class K8sObject(TypedDict):
    Namespace: str
    Name: str


class KubeLinterObject(TypedDict):
    Metadata: Metadata
    K8sObject: K8sObject


class KubelintError(TypedDict):
    Diagnostic: Diagnostic
    Check: str
    Remediation: str
    Object: KubeLinterObject


DEFAULT_EXCLUSIONS = {
    "unset-cpu-requirements",
}

EXCLUSIONS_TO_CLEANUP = {
    "unset-memory-requirements",
    "dangling-service",
    "drop-net-raw-capability",
    "duplicate-env-var",
    "latest-tag",
    "liveness-port",
    "no-anti-affinity",
    "no-extensions-v1beta",
    "no-read-only-root-fs",
    "non-existent-service-account",
    "privileged-container",
    "privilege-escalation-container",
    "readiness-port",
    "run-as-non-root",
    "ssh-port",
    "startup-port",
    "unset-memory-requirements",
}


def serialize_error(err: KubelintError) -> str:
    return (
        f"(object: {err['Object']['K8sObject']['Namespace']}/{err['Object']['K8sObject']['Name']}) "
        f"[{err['Check']} - {err['Diagnostic']['Message']}] "
        f"Remediation: {err['Remediation']}"
    )


def get_kubelinter_config(
    customer: str, cluster: str, service: str
) -> Tuple[Set[str], Set[str]]:
    """
    Load the service specific kubelinter configuration. This config excludes and
    includes specific checks across a service for a cluster/customer.

    The config file is supposed to be in the form:
    ```
    checks:
        include:
            - check1
            - check2
        exclude:
            - check3
            - check4
    ```

    The file has a different placement depending whether the cluster directory
    structure is in the directory per region format (saas) or file per region
    format (st).

    Where we have a directory per region we expect a subdirectory in the
    region directory called `kubelinter`. This directory must contain a file
    per service we want to configure.
    Example: `k8s/clusters/us/kubelinter/snuba.yaml`

    Where we have a single file per region we expect a kubelinter directory in
    the root and a subdirectory per cluster plus a file per service in each:
    `k8s/customers/customer1/kubelinter/snuba.yaml`
    """
    config = Config().silo_regions[customer]
    k8s_config = config.k8s_config
    cluster_def_root = k8s_config.cluster_def_root
    if k8s_config.cluster_name is None:
        # saas style clusters. one directory per cluster
        kubelint_config_path = Path(cluster_def_root) / f"kubelinter/{service}.yaml"
    else:
        # st style clusters. one file per cluster
        kubelint_config_path = (
            Path(cluster_def_root)
            / f"{k8s_config.cluster_name}/kubelinter/{service}.yaml"
        )

    full_path = workspace_root() / k8s_config.root / kubelint_config_path

    if full_path.exists():
        with open(full_path, "r") as config_file:
            data = yaml.safe_load(config_file)
            checks = data.get("checks", {})
            return (
                set(checks.get("include", set())),
                set(checks.get("exclude", set())),
            )
    return (set(), set())


def kube_linter(
    rendered_manifest: str,
    exclusions: Optional[Set[str]] = None,
    inclusions: Optional[Set[str]] = None,
) -> Sequence[KubelintError]:
    """
    Execute kube-linter on a rendered manifest.
    The rendered manifest can include multiple resources.
    """

    exclusions = exclusions if exclusions is not None else set()
    inclusions = inclusions if inclusions is not None else set()
    cmd = ["kube-linter", "lint", "--format=json"]
    checks_to_exclude = (
        DEFAULT_EXCLUSIONS | EXCLUSIONS_TO_CLEANUP | exclusions
    ) - inclusions

    for exclude in checks_to_exclude:
        cmd.append(f"--exclude={exclude}")
    cmd.append("-")

    child_process = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    stdout, stderr = child_process.communicate(input=rendered_manifest)

    if not stdout and child_process.returncode:
        raise KubeLinterException(stderr)

    if child_process.returncode == 0:
        return []
    objects = loads(stdout)
    return [cast(KubelintError, err) for err in objects["Reports"]]


def lint_and_print(
    customer_name: str,
    cluster_name: str,
    service: str,
    rendered: Generator[str, None, None],
) -> int:
    """
    Put together the methods above and prints the result.

    This should only be used by the click scripts directly, not by other
    modules.
    """
    os.environ["KUBERNETES_OFFLINE"] = "1"

    include, exclude = get_kubelinter_config(customer_name, cluster_name, service)

    errors_count = 0
    for doc in rendered:
        lint_and_print_doc(doc, include, exclude)

    return errors_count


def lint_and_print_doc(doc: str, include: Set[str], exclude: Set[str]) -> int:
    errors = kube_linter(doc, exclusions=exclude, inclusions=include)

    for error in errors:
        click.echo(serialize_error(error))
        click.echo("\n")

    return len(errors)
