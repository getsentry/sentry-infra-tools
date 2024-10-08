from libsentrykube.kube import _consolidate_variables

expected_consolidated_values = {
    "saas": {
        "customer": {
            "service1": {
                "key1": "value1",
                "key2": {
                    "subkey2_1": "value2_1",
                    "subkey2_2": 2,
                    "subkey2_3": ["value2_3_1_replaced"],
                },
            },
            "service2": {
                "key3": "three",
            },
        }
    }
}


def test_consolidate_variables_not_external():
    region = "saas"
    cluster = "customer"
    for service in ["service1", "service2"]:
        returned = _consolidate_variables(
            customer_name=region,
            service_name=service,
            cluster_name=cluster,
            external=False,
        )
        assert returned == expected_consolidated_values[region][cluster][service]
