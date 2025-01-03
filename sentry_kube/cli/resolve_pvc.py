import click

__all__ = ("resolve_pvc",)


def wide_renderer(rows):
    header = ("NAMESPACE", "CLAIM", "POD", "NODE")
    rows = [header] + [
        (pod.metadata.namespace, claim[1], pod.metadata.name, pod.spec.node_name)
        for pod, claim in rows
    ]
    longest = [0] * len(rows[0])
    for row in rows:
        for idx, col in enumerate(row):
            longest[idx] = max(longest[idx], len(col))

    def make_row(row):
        bits = []
        for idx, col in enumerate(row):
            bits.append(col.ljust(longest[idx] + 3))
        return "".join(bits)

    for row in rows:
        click.echo(make_row(row))


def json_renderer(rows):
    import json
    import sys

    out = []
    for pod, claim in rows:
        out.append(
            {
                "namespace": pod.metadata.namespace,
                "claim": claim[1],
                "pod": pod.metadata.name,
                "node": pod.spec.node_name,
            }
        )

    json.dump(out, sys.stdout, indent=4)
    sys.stdout.write("\n")


def yaml_renderer(rows):
    import sys

    import yaml

    out = []
    for pod, claim in rows:
        out.append(
            {
                "namespace": pod.metadata.namespace,
                "claim": claim[1],
                "pod": pod.metadata.name,
                "node": pod.spec.node_name,
            }
        )

    yaml.safe_dump(out, sys.stdout, indent=2)


@click.command()
@click.option(
    "-o", "--output", type=click.Choice(["wide", "json", "yaml"]), default="wide"
)
@click.argument("service", type=str, required=True)
@click.pass_context
def resolve_pvc(ctx, output, service):
    """
    Figure out which Pod and Node a PVC is bound to.
    """
    from libsentrykube.utils import die

    customer_name = ctx.obj.customer_name
    if customer_name == "saas":
        die("Sorry, this command is only supported on ST right now.")

    from kubernetes.client import CoreV1Api

    from libsentrykube.kube import collect_kube_resources
    from libsentrykube.utils import kube_get_client

    claims = set()
    namespaces = set()
    for resource in collect_kube_resources(customer_name, service):
        if resource.kind == "PersistentVolumeClaim":
            claims.add((resource.namespace, resource.name))
            namespaces.add(resource.namespace)
    if not claims:
        return

    findings = []
    client = kube_get_client()
    for pod in CoreV1Api(client).list_pod_for_all_namespaces(watch=False).items:
        if pod.metadata.namespace not in namespaces:
            continue
        for volume in pod.spec.volumes:
            if volume.persistent_volume_claim is not None:
                claim = (
                    pod.metadata.namespace,
                    volume.persistent_volume_claim.claim_name,
                )
                if claim in claims:
                    findings.append((pod, claim))

    {"wide": wide_renderer, "json": json_renderer, "yaml": yaml_renderer}[output](
        findings
    )
