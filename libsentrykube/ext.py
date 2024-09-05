import io
import json
import os
from functools import cache
from typing import Any, Dict, List, Optional

import click
import jinja2
import yaml
from jinja2.ext import Extension
from jinja2.utils import pass_context
from kubernetes.client import AppsV1Api
from kubernetes.client.rest import ApiException

from libsentrykube.customer import get_machine_type_list
from libsentrykube.service import get_service_data, get_service_values
from libsentrykube.utils import (
    deep_merge_dict,
    kube_extract_namespace,
    kube_get_client,
    md5_fileobj,
    workspace_root,
)

ENVOY_ENTRYPOINT = """
cat << EOF > /etc/envoy/envoy.yaml

{% if custom_config -%}
{{ custom_config }}
{% else %}
static_resources:
  clusters:
  - name: xds_cluster
    type: LOGICAL_DNS
    dns_lookup_family: V4_ONLY
    connect_timeout: 5s
    load_assignment:
      cluster_name: xds_cluster
      endpoints:
      - lb_endpoints:
        - endpoint:
            address:
              socket_address:
                address: {{ xds_address }}
                port_value: 80

dynamic_resources:
  lds_config:
    api_config_source:
      api_type: REST
      cluster_names: [xds_cluster]
      refresh_delay: {{ lds_refresh_delay }}s
      request_timeout: 10s

  cds_config:
    api_config_source:
      api_type: REST
      cluster_names: [xds_cluster]
      refresh_delay: {{ cds_refresh_delay }}s
      request_timeout: 10s
{% if admin %}
admin:
  access_log_path: "/dev/null"
  address:
    socket_address:
      address: {{ admin.address }}
      port_value: {{ admin.port }}
{% endif -%}
{% if datadog %}
stats_sinks:
  - name: envoy.dog_statsd
    typed_config:
      "@type": type.googleapis.com/envoy.config.metrics.v2.DogStatsdSink
      prefix: envoy
      address:
        socket_address:
          address: {{ datadog.address }}
          port_value: {{ datadog.port }}
{% endif -%}
{% endif -%}
EOF

exec envoy -c /etc/envoy/envoy.yaml \
     --concurrency {{ concurrency }} \
    {% if draining -%}
     --drain-strategy {{ draining.strategy }} \
     --drain-time-s {{ draining.time }} \
    {% endif -%}
     --service-node $(hostname) \
     --service-cluster {{ cluster }}
"""  # noqa: E501

XDS_DEFAULT_ADDRESS = "xds.sentry-system.svc.cluster.local."

XDS_BASE_ARGS = f"""
-upstream-proxy {XDS_DEFAULT_ADDRESS} -bootstrap-data /data/ -service-node $(hostname) -service-cluster {{cluster}}
""".strip()  # noqa: E501

XDS_SIDECAR_ENTRYPOINT = f"""
xds -listen 127.0.0.1:49150 -mode proxy -concurrency {{concurrency}} {XDS_BASE_ARGS}
"""  # noqa: E501

XDS_BOOTSTRAP_ENTRYPOINT = f"""
xds -mode bootstrap {XDS_BASE_ARGS}
"""  # noqa: E501

IPTABLES_ENTRYPOINT = """
iptables -t nat -A OUTPUT -m addrtype --src-type LOCAL --dst-type LOCAL -p udp --dport 8126 -j DNAT --to-destination $HOST_IP:8126
iptables -t nat -C POSTROUTING -m addrtype --src-type LOCAL --dst-type UNICAST -j MASQUERADE 2>/dev/null >/dev/null || iptables -t nat -A POSTROUTING -m addrtype --src-type LOCAL --dst-type UNICAST -j MASQUERADE
"""  # noqa: E501


class SimpleExtension(Extension):
    key = None

    @classmethod
    def install(cls, key):
        cls.key = key

    def __init__(self, environment=None):
        assert self.key is not None
        if environment is not None:
            environment.globals[self.key] = self.run
        self.environment = environment


class DeploymentImage(SimpleExtension):
    """
    Query production Kubernetes cluster for Deployment image. If
    a Deployment and container name combo exists, use this value found.
    If not, fall back to default value. This should be paired with any
    Deployment that is pushed out through by GoCD to make sure the image
    tag is correct. To be used as the value to pod.spec.containers[*].image.
    """

    @cache
    def run(self, deployment_name: str, container: str, default: str):
        if "KUBERNETES_OFFLINE" in os.environ:
            return default

        namespace, name = kube_extract_namespace(deployment_name)
        client = kube_get_client()
        try:
            deployment = AppsV1Api(client).read_namespaced_deployment(
                name, namespace, _request_timeout=2
            )
        except ApiException as e:
            if e.status == 404:
                return default
            raise
        for c in deployment.spec.template.spec.containers:
            if c.name == container:
                return c.image
        return default


class StatefulSetImage(SimpleExtension):
    """
    Query production Kubernetes cluster for StatefulSet image. If
    a StatefulSet and container name combo exists, use this value found.
    If not, fall back to default value. This should be paired with any
    StatefulSet that is pushed out through by GoCD to make sure the image
    tag is correct. To be used as the value to pod.spec.containers[*].image.
    """

    def run(self, stateful_set_name: str, container: str, default: str):
        if os.getenv("KUBERNETES_OFFLINE"):
            return default

        namespace, name = kube_extract_namespace(stateful_set_name)
        client = kube_get_client()
        try:
            stateful_set = AppsV1Api(client).read_namespaced_stateful_set(
                name, namespace, _request_timeout=1
            )
        except ApiException as e:
            if e.status == 404:
                return default
            raise
        for c in stateful_set.spec.template.spec.containers:
            if c.name == container:
                return c.image
        return default


class JsonFile(SimpleExtension):
    """
    Just returns JSON.

    The filepath is relative to workspace_root.
    """

    def run(self, file: str) -> dict:
        filepath = workspace_root() / file
        try:
            return json.loads(filepath.read_text())
        except json.JSONDecodeError:
            raise click.ClickException(f"Failed to JSON decode '{file}'")
        except FileNotFoundError:
            raise click.ClickException(f"No such file or directory: '{file}'")


class Md5File(SimpleExtension):
    """
    Generate an md5 hash of a file on disk relative
    to the root of the .git repository you're within.
    """

    def run(self, file: str) -> str:
        # All paths in md5file are relative to the root
        # of the .git repository.
        filepath = workspace_root() / file
        try:
            with open(filepath, "rb") as fp:
                return md5_fileobj(fp)
        except FileNotFoundError:
            raise click.ClickException(f"No such file or directory: '{file}'")


class Md5Template(Md5File):
    """
    Generate an md5 hash of a rendered template. Path to the template is
    relative to the service directory that you are operating on.
    """

    @pass_context
    def run(self, context, template_path: str) -> str:  # type: ignore
        return md5_fileobj(
            io.BytesIO(self.environment.get_template(template_path).render(context).encode("utf-8"))
        )


class HAPodAffinity(SimpleExtension):
    """
    Returns an anti-affinity rule to ensure that no two Pods are
    deployed together on the same Node. This should be used with
    Deployments that are small and required to be HA. Important to
    note that the number of replicas in the Deployment will be the
    minimum size of the nodepool. To be used as the value to
    pod.spec.affinity
    """

    def run(self, service_name: str):
        return json.dumps(
            {
                "podAntiAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": [
                        {
                            "topologyKey": "kubernetes.io/hostname",
                            "labelSelector": {
                                "matchExpressions": [
                                    {
                                        "key": "service",
                                        "operator": "In",
                                        "values": [service_name],
                                    }
                                ]
                            },
                        }
                    ]
                }
            }
        )


class InternalService(SimpleExtension):
    """
    An entire Service definition which should be the default
    for any service which needs to have Endpoints exposed. This
    is required if your service is exposing a network service
    that other things need to talk to. Not applicable if you're
    headless. A Service exposes a single port. If your service needs
    multiple ports, each one should be a unique Kubernetes Service
    resource.
    """

    def run(self, service_name: str, port: int, namespace: str = "default"):
        return json.dumps(
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {
                    "name": service_name,
                    "namespace": namespace,
                    "labels": {"service": service_name},
                },
                "spec": {
                    "clusterIP": "None",
                    "selector": {"service": service_name},
                    "ports": [{"port": port}],
                },
            }
        )


class ValuesOf(SimpleExtension):
    """
    The values of a service.
    Useful for referencing values across services.

    Passing "external=True" means that the full service path is provided (useful when
    e.g. you want to reference a service that is not enabled for the current cluster).
    """

    @cache
    @pass_context
    def run(
        self,
        context,
        service_name: str,
        cluster_name: str | None = None,
        external: bool = False,
    ) -> dict:
        customer_name = context["customer"]["id"]

        # Handle multi-cluster scenarios where default defaults to None
        if cluster_name is None:
            cluster_name = "default"

        # Handle sentry4sentry id being in the s4s directory
        if customer_name == "sentry4sentry":
            customer_name = "s4s"

        service_data, _ = get_service_data(
            customer_name,
            service_name,
            cluster_name,
        )

        values = get_service_values(service_name, external)

        # Service data overrides values.
        deep_merge_dict(values, service_data)

        return values


class EnvoySidecar(SimpleExtension):
    """
    Creates a sidecar container using Envoy which is required
    for any outbound communincation between your Pod and another Pod.
    This requires an Envoy cluster name which is used to fetch the
    required data from the xDS service to construct listeners and clusters.
    An optional preStopWait argument is to control how long the Envoy
    container waits to shut down before exiting. This is needed to coordinate
    with other containers in the Pod for a graceful shutdown. Envoy should
    ideally shut down after everything else. To be used as a container within
    pod.spec.containers. See xDS for more information.
    """

    def run(
        self,
        cluster: str,
        concurrency: int = 1,
        preStopWait: int = 1,
        xds_address: str = XDS_DEFAULT_ADDRESS,
        admin: Optional[dict] = None,
        datadog: Optional[dict] = None,
        draining: Optional[dict] = None,
        resources: Optional[dict] = None,
        version: str = "1.16.0",
        custom_config: Optional[dict] = None,
        custom_pre_stop_command: Optional[str] = None,
        livenessProbe: Optional[dict] = None,
        readinessProbe: Optional[dict] = None,
        cds_refresh_delay: int = 3600,
        lds_refresh_delay: int = 3600,
    ):
        if draining:
            draining = {
                "strategy": "immediate",
                "time": 300,
                "drain_listeners": False,
                **draining,
            }
        else:
            draining = {}

        pre_stop_command = f"/bin/sleep {preStopWait}"
        if draining.get("drain_listeners"):
            if admin:
                pre_stop_command = (
                    "wget -q -O- --post-data '' "
                    f"http://127.0.0.1:{admin['port']}/drain_listeners?graceful ; "
                    f"{pre_stop_command}"
                )
            else:
                raise ValueError("'admin' configuration is required for draining to work")

        if custom_pre_stop_command:
            pre_stop_command = custom_pre_stop_command

        custom_config_str = yaml.dump(custom_config) if custom_config else None
        res = {
            "image": f"envoyproxy/envoy-alpine:v{version}",
            "name": "envoy",
            "args": [
                "/bin/sh",
                "-ec",
                jinja2.Template(ENVOY_ENTRYPOINT)
                .render(
                    concurrency=concurrency,
                    cluster=cluster,
                    xds_address=xds_address,
                    admin=admin,
                    datadog=datadog,
                    draining=draining,
                    custom_config=custom_config_str,
                    cds_refresh_delay=cds_refresh_delay,
                    lds_refresh_delay=lds_refresh_delay,
                )
                .strip(),
            ],
            # Starting from version 1.15.0, the default user inside the Envoy container
            # is "envoy", not "root". However, without "root" the entrypoint script can
            # only write to "/tmp", plus there might be issues with binding to unix
            # sockets. Setting ENVOY_UID to 0 reverts that change in behavior.
            "env": [{"name": "ENVOY_UID", "value": "0"}],
            "lifecycle": {"preStop": {"exec": {"command": ["/bin/sh", "-c", pre_stop_command]}}},
            "resources": {
                "requests": {"cpu": "15m", "memory": "20Mi"},
                "limits": {"memory": "50Mi"},
            },
        }

        if resources:
            res["resources"] = resources

        if livenessProbe:
            res["livenessProbe"] = livenessProbe

        if readinessProbe:
            res["readinessProbe"] = readinessProbe

        return json.dumps(res)


class DogstatsdPortForwardingInitContainer(SimpleExtension):
    """
    An initContainer for setting up port-forwarding from within the Pod
    out to the host's IP address. This exposes a dogstatsd agent UDP port
    inside the Pod over the loopback interface (127.0.0.1).
    To be used as a container within pod.spec.initContainers.
    """

    def run(self, version: str = "alpine3.20"):
        iptables_entrypoint = IPTABLES_ENTRYPOINT
        env = [
            {
                "name": "HOST_IP",
                "valueFrom": {"fieldRef": {"fieldPath": "status.hostIP"}},
            },
        ]

        return json.dumps(
            {
                "image": f"us.gcr.io/sentryio/iptables:{version}",
                "name": "init-port-forward",
                "args": ["/bin/sh", "-ec", iptables_entrypoint.strip()],
                "env": env,
                "securityContext": {"capabilities": {"add": ["NET_ADMIN"]}},
            }
        )


class GeoIPVolume(SimpleExtension):
    """
    Provide the GeoIP volume to the Pod for containers to use. Not required,
    but you most likely want geoip_volumemount to mount the volume inside
    a specific container, and geoip_initcontainer to ensure the data exists.
    To be used within pod.spec.volumes.
    """

    def run(self):
        return json.dumps(
            {
                "name": "geoip",
                "hostPath": {"path": "/mnt/stateful_partition/usr/local/share/GeoIP"},
            }
        )


class GeoIPVolumeMount(SimpleExtension):
    """
    Mount the GeoIP Volume inside of your container. Requires pairing
    with geoip_volume and probably geoip_initcontainer macros. Data is mounted
    into /usr/local/share/GeoIP within your container. To be used within
    pod.spec.containers[*].volumeMounts.
    """

    def run(self):
        return json.dumps(
            {"name": "geoip", "mountPath": "/usr/local/share/GeoIP", "readOnly": True}
        )


class GeoIPInitContainer(SimpleExtension):
    """
    An initContainer to ensuring that GeoIP data is in place before
    starting other Pods. If your services needs GeoIP, you should probably
    use this, otherwise there's no guarantee it'll be there. GeoIP data
    is provided by the geoipupdate DaemonSet and when provisioning a new
    Node, it's possible that the file hasn't downloaded yet before scheduling
    other Pods. See also geoip_volume and geoip_volumemount macros. To be
    used as a container inside of pod.spec.initContainers.
    """

    def run(self, image: str = "busybox:1.36"):
        return json.dumps(
            {
                "image": image,
                "name": "init-geoip",
                "args": [
                    "/bin/sh",
                    "-ec",
                    "while [ ! -f /usr/local/share/GeoIP/GeoIP2-City.mmdb ]; do sleep 1; done",  # noqa: E501
                ],
                "volumeMounts": [json.loads(GeoIPVolumeMount().run())],
            }
        )


class ServiceAccount(SimpleExtension):
    """
    Generate a simple ServiceAccount.
    """

    def run(self, service: str, namespace: str = "default"):
        return yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "ServiceAccount",
                "metadata": {
                    "name": f"service-{service}",
                    "namespace": namespace,
                    "labels": {"service": service},
                },
            }
        )


class XDSProxySidecar(SimpleExtension):
    def run(self, cluster: str, preStopWait: int = 1, concurrency: int = 1):
        return json.dumps(
            {
                "image": "us.gcr.io/sentryio/xds:20200921",
                "name": "xds-proxy",
                "command": [
                    "/bin/sh",
                    "-ec",
                    XDS_SIDECAR_ENTRYPOINT.format(cluster=cluster, concurrency=concurrency).strip(),
                ],
                "lifecycle": {
                    "preStop": {"exec": {"command": ["/bin/sh", "-c", f"/bin/sleep {preStopWait}"]}}
                },
                "resources": {
                    "requests": {"cpu": "15m", "memory": "20Mi"},
                    "limits": {"cpu": "1000m", "memory": "50Mi"},
                },
                "volumeMounts": [{"name": "envoy-bootstrap-data", "mountPath": "/data"}],
            }
        )


class DeepMerge(SimpleExtension):
    """
    Merges one dictionary into another.
    """

    def run(self, into: dict, other: dict, overwrite: bool = True):
        deep_merge_dict(into, other, overwrite)


class SysctlInitContainer(SimpleExtension):
    """
    An initContainer for setting per-pod kernel (sysctl) parameters.
    Keep in mind that this extension should be used only for namespaced sysctl,
    i.e. those that can be set independently for each pod. For non-namespaced
    (node-level sysctls) it is advised to use "sysctl-daemonset".
    Additionally, namespaced sysctls are grouped into "safe" and "unsafe". Be careful
    when setting unsafe sysctls, this might lead to unexpected behaviour.
    More details: https://kubernetes.io/docs/tasks/administer-cluster/sysctl-cluster/
    To be used as a container inside of pod.spec.initContainers.
    """

    def run(self, params: dict):
        assert params, "No sysctl values provided"
        command = " ".join(f"sysctl -w '{key}'='{value}';" for key, value in sorted(params.items()))
        return json.dumps(
            {
                "name": "init-sysctl",
                "image": "alpine:3.19",
                "securityContext": {"privileged": True},
                "command": ["sh", "-c", command],
            }
        )


class XDSProxyInitContainer(SimpleExtension):
    def run(self, cluster: str):
        return json.dumps(
            {
                "image": "us.gcr.io/sentryio/xds:20200921",
                "name": "bootstrap-xds-proxy",
                "command": [
                    "sh",
                    "-ec",
                    XDS_BOOTSTRAP_ENTRYPOINT.format(cluster=cluster).strip(),
                ],
                "volumeMounts": [{"name": "envoy-bootstrap-data", "mountPath": "/data"}],
            }
        )


class XDSProxyVolume(SimpleExtension):
    def run(self, json_encode=True):
        volume = {"name": "envoy-bootstrap-data", "emptyDir": {}}
        if json_encode:
            return json.dumps(volume)
        return volume


class XDSEDSClusterConfig(SimpleExtension):
    """
    Generate the XDS EDS config for use in an envoy cluster definition.
    """

    def run(self, service_name: str, refresh_delay: str = "1s") -> str:
        return json.dumps(
            {
                "service_name": service_name,
                "eds_config": {
                    "api_config_source": {
                        "api_type": "REST",
                        "cluster_names": ["xds_cluster"],
                        "refresh_delay": refresh_delay,
                    },
                },
            }
        )


class RaiseExtension(SimpleExtension):
    """
    Raise a custorm exception from Jinja template
    """

    def run(self, message):
        raise Exception(message)


class MachineType(SimpleExtension):
    _type_cache: Dict[str, Dict[str, str]] = {}
    _items_cache: List[Any] = []

    def run(self, project: str, zone: str, name: str) -> Dict[str, str]:
        return self._get_machine_type_by_name(project, zone, name)

    def _get_machine_type_by_name(self, project: str, zone: str, name: str) -> Dict[str, str]:
        if name in self._type_cache:
            return self._type_cache[name]
        if self._items_cache:
            return self._get_type_from_items_cache(name)
        return self._execute_get_machine_type(project, zone, name)

    def _execute_get_machine_type(self, project: str, zone: str, name: str) -> Dict[str, str]:
        self._items_cache = get_machine_type_list(project, zone)
        return self._get_type_from_items_cache(name)

    def _get_type_from_items_cache(self, name: str) -> Dict[str, str]:
        for item in self._items_cache:
            if item["name"] == name:
                self._type_cache[name] = {
                    "memory": item["memoryMb"],
                }
                return self._type_cache[name]
        return {}
