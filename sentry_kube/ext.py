import json
from typing import Literal, Mapping

from yaml import safe_dump_all

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
