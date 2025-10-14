from setuptools import find_packages
from setuptools import setup

default_macros = [
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
    "get_var=libsentrykube.ext:GetVar",
]

setup(
    name="libsentrykube",
    version="0.0.0.dev0",
    author="Sentry",
    author_email="ops@sentry.io",
    packages=find_packages("."),
    zip_safe=False,
    include_package_data=True,
    classifiers=["DO NOT UPLOAD"],
    entry_points={"libsentrykube.macros": default_macros},
    python_requires=">=3.8",
)
