from typing import Sequence

from setuptools import find_packages
from setuptools import setup

macros = [
    "iap_service=sentry_kube.ext:IAPService",
    "pgbouncer_sidecar=sentry_kube.ext:PGBouncerSidecar",
    "xds_configmap_from=sentry_kube.ext:XDSConfigMapFrom",
    "raise=libsentrykube.ext:RaiseExtension",
    "json_file=libsentrykube.ext:JsonFile",
    "values_of=libsentrykube.ext:ValuesOf",
    "deployment_image=libsentrykube.ext:DeploymentImage",
    "statefulset_image=libsentrykube.ext:StatefulSetImage",
    "machine_info=libsentrykube.ext:MachineType",
    "md5file=libsentrykube.ext:Md5File",
    "md5template=libsentrykube.ext:Md5Template",
    "ha_pod_affinity=libsentrykube.ext:HAPodAffinity",
    "internal_service=libsentrykube.ext:InternalService",
    "envoy_sidecar=libsentrykube.ext:EnvoySidecar",
    "dogstatsd_port_forward_initcontainer=libsentrykube.ext:DogstatsdPortForwardingInitContainer",  # noqa: E501
    "geoip_volume=libsentrykube.ext:GeoIPVolume",
    "geoip_volumemount=libsentrykube.ext:GeoIPVolumeMount",
    "geoip_initcontainer=libsentrykube.ext:GeoIPInitContainer",
    "serviceaccount=libsentrykube.ext:ServiceAccount",
    "deep_merge=libsentrykube.ext:DeepMerge",
    "sysctl_initcontainer=libsentrykube.ext:SysctlInitContainer",
    "xds_eds_cluster_config=libsentrykube.ext:XDSEDSClusterConfig",
    "xds_proxy_sidecar=libsentrykube.ext:XDSProxySidecar",
    "xds_proxy_initcontainer=libsentrykube.ext:XDSProxyInitContainer",
    "xds_proxy_volume=libsentrykube.ext:XDSProxyVolume",
    "service_registry_annotations=libsentrykube.ext:ServiceRegistryAnnotations",
    "service_registry_labels=libsentrykube.ext:ServiceRegistryLabels",
    "get_var=libsentrykube.ext:GetVar",
]


def get_requirements() -> Sequence[str]:
    with open("requirements.txt") as f:
        return [
            x.strip() for x in f.read().split("\n") if not x.startswith(("#", "--"))
        ]


setup(
    name="sentry-infra-tools",
    version="1.8.1",
    author="Sentry",
    author_email="oss@sentry.io",
    packages=find_packages(where=".", exclude="tests"),
    package_data={
        "": ["py.typed"],
    },
    license="FSL-1.0-Apache-2.0",
    description="Infrastructure tools used at Sentry",
    install_requires=get_requirements(),
    zip_safe=False,
    include_package_data=True,
    entry_points={
        "console_scripts": [
            "sentry-kube=sentry_kube.cli:main",
            "materialize-config=config_builder.materialize_all:main",
            "pr-docs=assistant.prdocs:main",
            "pr-approver=pr_approver.approver:main",
        ],
        "libsentrykube.macros": macros,
    },
    scripts=["sentry_kube/bin/sentry-kube-pop", "sentry_kube/bin/important-diffs-only"],
    python_requires=">=3.11",
)
