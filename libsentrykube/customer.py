from functools import cache
from typing import Any, Dict, List, Optional, Sequence

import googleapiclient.discovery
from googleapiclient.errors import HttpError

from libsentrykube.cluster import load_cluster_configuration
from libsentrykube.config import Config, SiloRegion
from libsentrykube.helm import HelmData
from libsentrykube.utils import die

ALLOYDB_DISCOVERY_SERVICEURL = (
    "https://{api}.googleapis.com/$discovery/rest?version={apiVersion}"
)


@cache
def get_region_config(config: Config, region_name: str) -> tuple[str, SiloRegion]:
    region_config = None
    if region_name in config.silo_regions:
        region_config = config.silo_regions[region_name]
    else:
        # Check if we have any aliases that match our region
        for region in config.silo_regions:
            if region_name in config.silo_regions[region].aliases:
                region_config = config.silo_regions[region]
                region_name = region
                break

    if region_config is None:
        raise ValueError(f"Region '{region_name}' not found")

    return region_name, region_config


@cache
def load_customer_data(
    config: Config, customer_name: str, cluster_name: str
) -> Dict[str, Any]:
    try:
        _, region_config = get_region_config(config, customer_name)
    except ValueError:
        die(
            f"Region '{customer_name}' not found. Did you mean one of: \n\n"
            f"{config.get_regions()}"
        )

    k8s_config = region_config.k8s_config

    # If the customer has only one cluster, just use the value from config
    cluster_name = k8s_config.cluster_name or cluster_name

    cluster = load_cluster_configuration(k8s_config, cluster_name)
    return cluster.services_data


@cache
def load_region_helm_data(
    config: Config, region_name: str, cluster_name: str
) -> HelmData:
    try:
        _, region_config = get_region_config(config, region_name)
    except ValueError:
        die(
            f"Region '{region_name}' not found. Did you mean one of: \n\n"
            f"{config.get_regions()}"
        )

    k8s_config = region_config.k8s_config

    # If the region has only one cluster, just use the value from config
    cluster_name = k8s_config.cluster_name or cluster_name

    cluster = load_cluster_configuration(k8s_config, cluster_name)
    return cluster.helm_services


def get_compute_instance_ips(project: str) -> List[str]:
    compute = googleapiclient.discovery.build("compute", "v1")
    request = compute.instances().aggregatedList(project=project)
    instance_list = request.execute()
    available_instances = []
    for _, data in instance_list["items"].items():
        if "instances" in data:
            available_instances.extend(data["instances"])
    customer_instances = [
        i
        for i in available_instances
        if i["status"] == "RUNNING"
        and not i["name"].startswith("gke-primary-node-pool")
    ]
    return [i["networkInterfaces"][0]["networkIP"] for i in customer_instances]


# This is needed to connect bastion to Alloydb Clusters so Postgres Terraform Provider
# can interact with Alloydb Postgres Databases
def alloydb_instance_aggregated_list(
    project: str, region: Optional[str] = None
) -> List[Any]:
    instances = []
    try:
        alloydb = googleapiclient.discovery.build(
            "alloydb", "v1", discoveryServiceUrl=ALLOYDB_DISCOVERY_SERVICEURL
        )
        locations_api = alloydb.projects().locations()
        clusters_api = locations_api.clusters()
        instance_api = clusters_api.instances()

        locations_data = locations_api.list(name=f"projects/{project}").execute()
        for location in locations_data["locations"]:
            if region and not location["name"].endswith(region):
                continue
            clusters_data = clusters_api.list(parent=location["name"]).execute()
            if not clusters_data:
                continue
            for cluster in clusters_data["clusters"]:
                instance_data = instance_api.list(parent=cluster["name"]).execute()
                if instance_data:
                    instances.extend(instance_data["instances"])
    except HttpError as error:
        if error.resp.status == 403:
            print("Alloydb API not enabled", error)
        else:
            print(f"An HTTP error occurred: {error}")

    return instances


def get_alloydb_instance_ips(
    project: str, region: Optional[str] = None
) -> Sequence[str]:
    instances = alloydb_instance_aggregated_list(project, region)
    return [i["ipAddress"] for i in instances]


def get_service_ip_mapping(project: str, region: Optional[str] = None) -> List[str]:
    ips = []
    ips.extend(get_compute_instance_ips(project))
    ips.extend(get_alloydb_instance_ips(project, region))
    return ips


def get_region(config: Config, customer_name: str, cluster_name: str):
    region = load_customer_data(config, customer_name, cluster_name)["region"].rsplit(
        "-", 1
    )[0]
    assert isinstance(region, str)
    return region


def get_project(config: Config, customer_name: str, cluster_name) -> str:
    project = load_customer_data(config, customer_name, cluster_name)["project"]
    assert isinstance(project, str)
    return project


def get_machine_type_list(project: str, zone: str) -> List[Dict[str, Any]]:
    compute = googleapiclient.discovery.build("compute", "v1")
    request = compute.machineTypes().list(project=project, zone=zone)
    raw_data = request.execute()
    return raw_data["items"]
