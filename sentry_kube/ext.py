import json
from pathlib import Path
from typing import Any
from typing import List
from typing import Literal
from typing import Mapping
from typing import Optional

from jinja2 import FileSystemLoader
from jinja2 import pass_context
from libsentrykube.ext import SimpleExtension
from yaml import safe_dump_all
from yaml import safe_load_all

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
                    "cloud.google.com/load-balancer-type": "Internal",
                    # TODO: Remove beta from annotation
                    # https://cloud.google.com/kubernetes-engine/docs/how-to/ingress-configuration#associating_backendconfig_with_your_ingress
                    "beta.cloud.google.com/backend-config": json.dumps(
                        {"default": service_name}, separators=(",", ":")
                    ),
                },
            },
            "spec": {
                "type": "LoadBalancer",
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
                    "oauthclientCredentials": {"secretName": f"oauth-{service_name}"},
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


class PGBouncerSidecar(SimpleExtension):
    def run(
        self,
        databases: List[str],
        repository: str = "us.gcr.io/sentryio",
        preStopWait: int = 1,
        checkInterval: int = 1,
        maxClientConn: int = 100,
        defaultPoolSize: int = 25,
        serverLifetime: int = 300,
        version: str = "1.24.1-alpine3.22",
        application_name: Optional[str] = None,
        livenessProbe: Optional[dict] = None,
        resources: Optional[dict] = None,
        custom_pre_stop_command: Optional[str] = None,
    ):
        if application_name:
            # Prepend supplied application_name to the pgbouncer options
            # the application_name name will appear in e.g. pg_stat_activity
            #
            # application_name is prepended in case the connection already
            # specifies application_name, the last one in connection string
            # will be used
            databases = [
                " ".join(
                    (
                        dbname.strip(),
                        "=",
                        f"application_name={application_name}",
                        opts.strip(),
                    )
                )
                for dbname, opts in map(lambda d: d.split("=", 1), databases)
            ]
        databases_str = "\n".join(databases)

        pre_stop_command = f"sleep {preStopWait} && killall -INT pgbouncer"
        if custom_pre_stop_command:
            pre_stop_command = custom_pre_stop_command

        image = f"{repository}/pgbouncer:{version}"
        if ".pkg.dev/" in repository:
            image = f"{repository}/pgbouncer/image:{version}"

        res: dict[str, Any] = {
            "image": image,
            "name": "pgbouncer",
            "args": [
                "/bin/sh",
                "-ec",
                f"""cat << EOF > /etc/pgbouncer/pgbouncer.ini
[databases]
{databases_str}
[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
unix_socket_dir =
auth_type = scram-sha-256
auth_file = /etc/pgbouncer/userlist.txt
admin_users = pgbouncer
stats_users = datadog
pool_mode = transaction
server_reset_query = DISCARD ALL
ignore_startup_parameters = extra_float_digits
server_check_query = select 1
dns_max_ttl = {checkInterval}
server_check_delay = {checkInterval}
server_lifetime = {serverLifetime}
max_client_conn = {maxClientConn}
default_pool_size = {defaultPoolSize}
log_connections = 1
log_disconnections = 1
log_pooler_errors = 1
server_round_robin = 1
tcp_keepalive = 1
EOF
pgbouncer /etc/pgbouncer/pgbouncer.ini""",
            ],
            "lifecycle": {
                "preStop": {
                    "exec": {
                        "command": [
                            "/bin/sh",
                            "-c",
                            pre_stop_command,
                        ]
                    }
                }
            },
            "securityContext": {
                "allowPrivilegeEscalation": False,
                "readOnlyRootFilesystem": True,
                "runAsNonRoot": True,
                "runAsUser": 1000,
                "runAsGroup": 1000,
            },
            "volumeMounts": [
                {
                    "name": "pgbouncer-secrets",
                    "subPath": "userlist",
                    "mountPath": "/etc/pgbouncer/userlist.txt",
                    "readOnly": True,
                },
                {
                    "name": "etc-pgbouncer",
                    "mountPath": "/etc/pgbouncer",
                },
            ],
        }
        _resources = {
            "requests": {"cpu": "50m", "memory": "25Mi"},
            "limits": {"memory": "25Mi"},
        }
        _resources.update(resources or {})
        res["resources"] = _resources

        if livenessProbe:
            res["livenessProbe"] = livenessProbe
        return json.dumps(res)


class PGBouncerInitSidecar(SimpleExtension):
    def run(
        self,
        databases: List[str],
        repository: str = "us.gcr.io/sentryio",
        checkInterval: int = 1,
        maxClientConn: int = 100,
        defaultPoolSize: int = 25,
        serverLifetime: int = 300,
        version: str = "1.24.1-alpine3.22",
        application_name: Optional[str] = None,
        livenessProbe: Optional[dict] = None,
        resources: Optional[dict] = None,
    ):
        if application_name:
            # Prepend supplied application_name to the pgbouncer options
            # the application_name name will appear in e.g. pg_stat_activity
            #
            # application_name is prepended in case the connection already
            # specifies application_name, the last one in connection string
            # will be used
            databases = [
                " ".join(
                    (
                        dbname.strip(),
                        "=",
                        f"application_name={application_name}",
                        opts.strip(),
                    )
                )
                for dbname, opts in map(lambda d: d.split("=", 1), databases)
            ]
        databases_str = "\n".join(databases)

        image = f"{repository}/pgbouncer:{version}"
        if ".pkg.dev/" in repository:
            image = f"{repository}/pgbouncer/image:{version}"

        res: dict[str, Any] = {
            "image": image,
            "name": "pgbouncer",
            "restartPolicy": "Always", # sidecar init container
            "args": [
                "/bin/sh",
                "-ec",
                f"""cat << EOF > /etc/pgbouncer/pgbouncer.ini
[databases]
{databases_str}
[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
unix_socket_dir =
auth_type = scram-sha-256
auth_file = /etc/pgbouncer/userlist.txt
admin_users = pgbouncer
stats_users = datadog
pool_mode = transaction
server_reset_query = DISCARD ALL
ignore_startup_parameters = extra_float_digits
server_check_query = select 1
dns_max_ttl = {checkInterval}
server_check_delay = {checkInterval}
server_lifetime = {serverLifetime}
max_client_conn = {maxClientConn}
default_pool_size = {defaultPoolSize}
log_connections = 1
log_disconnections = 1
log_pooler_errors = 1
server_round_robin = 1
tcp_keepalive = 1
EOF
pgbouncer /etc/pgbouncer/pgbouncer.ini""",
            ],
            "securityContext": {
                "allowPrivilegeEscalation": False,
                "readOnlyRootFilesystem": True,
                "runAsNonRoot": True,
                "runAsUser": 1000,
                "runAsGroup": 1000,
            },
            "volumeMounts": [
                {
                    "name": "pgbouncer-secrets",
                    "subPath": "userlist",
                    "mountPath": "/etc/pgbouncer/userlist.txt",
                    "readOnly": True,
                },
                {
                    "name": "etc-pgbouncer",
                    "mountPath": "/etc/pgbouncer",
                },
            ],
        }
        _resources = {
            "requests": {"cpu": "50m", "memory": "25Mi"},
            "limits": {"memory": "25Mi"},
        }
        _resources.update(resources or {})
        res["resources"] = _resources

        if livenessProbe:
            res["livenessProbe"] = livenessProbe
        return json.dumps(res)



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
