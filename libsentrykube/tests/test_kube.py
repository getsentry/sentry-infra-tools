from libsentrykube.context import init_cluster_context
from libsentrykube.kube import _consolidate_variables

expected_consolidated_values = {
    "saas": {
        "customer": {
            "service1": {
                "key1": "value1",  # From the value file
                "key2": {
                    "subkey2_1": "value2_1",  # From the value file
                    "subkey2_2": 2,  # From the value file
                    "subkey2_3": ["value2_3_1_replaced"],  # From the region override
                    "subkey2_4": [
                        "value2_4_1_managed_replaced"
                    ],  # From the managed file
                    "subkey2_5": [
                        "value2_5_1_managed_replaced"
                    ],  # From the managed file
                },
            },
            "service2": {
                "key3": "three",  # From the cluster file
            },
            "service4": {
                "key1": "value4",  # Cluster file overrides everything
            },
        }
    }
}


def test_consolidate_variables_not_external():
    region = "saas"
    cluster = "customer"
    init_cluster_context(region, cluster)
    for service in ["service1", "service2", "service4"]:
        returned = _consolidate_variables(
            customer_name=region,
            service_name=service,
            cluster_name=cluster,
            external=False,
        )
        assert returned == expected_consolidated_values[region][cluster][service]
