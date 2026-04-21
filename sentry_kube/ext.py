import json
from pathlib import Path
from typing import Any, List, Literal, Mapping, Optional

from jinja2 import FileSystemLoader, pass_context
from yaml import safe_dump_all, safe_load_all

from libsentrykube.ext import SimpleExtension

FormatType = Literal["json", "env"]


class IAPService(SimpleExtension):
    """
    An entire Service + BackendConfig + ManagedCertificate for
    a service intended to be run behind Google's IAP.
    """

    def run(
        self,
        service_name: str,
        domain: str,
        port: int,
        selector: Mapping[str, str],
        health_check_path: str = "/",
        namespace: str = "default",
    ) -> str:
        service = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": service_name,
                "namespace": namespace,
                "labels": {"service": service_name},
                "annotations": {
                    "cloud.google.com/backend-config": json.dumps(
                        {"default": service_name}, separators=(",", ":")
                    ),
                    "cloud.google.com/neg": '{"ingress": true}',
                },
            },
            "spec": {
                "type": "ClusterIP",
                "selector": selector,
                "ports": [{"port": 80, "targetPort": port}],
            },
        }

        backend_config = {
            "apiVersion": "cloud.google.com/v1",
            "kind": "BackendConfig",
            "metadata": {"name": service_name, "labels": {"service": service_name}},
            "spec": {
                "iap": {
                    "enabled": True,
                },
                "healthCheck": {
                    "type": "HTTP",
                    "requestPath": health_check_path,
                },
            },
        }

        managed_certificate = {
            "apiVersion": "networking.gke.io/v1",
            "kind": "ManagedCertificate",
            "metadata": {
                "name": f"cert-{domain.replace('.', '-')}",
                "labels": {"service": service_name},
            },
            "spec": {"domains": [domain]},
        }

        return safe_dump_all([service, backend_config, managed_certificate])


class XDSConfigMapFrom(SimpleExtension):
    @pass_context
    def run(self, context, path: str, files: Optional[List[str]]) -> str:
        listeners: list[Any] = []
        clusters: list[Any] = []
        assignments: dict[str, Any] = {"by-cluster": {}, "by-node-id": {}}
        if not files:
            return safe_dump_all(
                [
                    {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {
                            "name": "xds",
                            "namespace": "sentry-system",
                        },
                        "data": {
                            "listeners": safe_dump_all([listeners]),
                            "clusters": safe_dump_all([clusters]),
                            "assignments": safe_dump_all([assignments]),
                        },
                    }
                ]
            )

        types = "listeners", "clusters"
        env_loader = self.environment.loader
        assert isinstance(env_loader, FileSystemLoader)
        for searchpath in env_loader.searchpath:
            prefix = len(searchpath) + 1
            for f in files:
                for p in Path(searchpath).glob(f"{path}/{f}.yaml"):
                    for doc in safe_load_all(
                        self.environment.get_template(str(p)[prefix:])
                        .render(context)
                        .encode("utf-8")
                    ):
                        listeners.extend(doc.get("listeners", []))
                        clusters.extend(doc.get("clusters", []))
                        for by in assignments.keys():
                            for key, values in (
                                doc.get("assignments", {}).get(by, {}).items()
                            ):
                                assignments[by].setdefault(key, {})
                                for type in types:
                                    assignments[by][key].setdefault(type, [])
                                    assignments[by][key][type].extend(
                                        values.get(type, [])
                                    )

        for by in assignments.keys():
            for key in assignments[by].keys():
                for type in types:
                    assignments[by][key][type] = sorted(assignments[by][key][type])

        def by_name(x):
            return x["name"]

        return safe_dump_all(
            [
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {
                        "name": "xds",
                        "namespace": "sentry-system",
                    },
                    "data": {
                        "listeners": safe_dump_all([sorted(listeners, key=by_name)]),
                        "clusters": safe_dump_all([sorted(clusters, key=by_name)]),
                        "assignments": safe_dump_all([assignments]),
                    },
                }
            ]
        )
