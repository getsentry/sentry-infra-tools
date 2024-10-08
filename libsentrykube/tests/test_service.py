from libsentrykube.context import init_cluster_context
from libsentrykube.service import (
    get_service_data,
    get_service_values,
    get_service_value_overrides,
)


expected_service_data = {
    "saas": {"customer": {"service1": {}, "service2": {"key3": "three"}}}
}

expected_service_values = {
    "saas": {
        "customer": {
            "service1": {
                "key1": "value1",
                "key2": {
                    "subkey2_1": "value2_1",
                    "subkey2_2": 2,
                    "subkey2_3": ["value2_3_1"],
                },
            },
            "service2": {},
        }
    }
}

expected_service_value_overrides = {
    "saas": {
        "customer": {
            "service1": {"key2": {"subkey2_3": ["value2_3_1_replaced"]}},
            "service2": {},
        }
    }
}


def test_get_service_values_not_external():
    region = "saas"
    cluster = "customer"
    for service in ["service1", "service2"]:
        init_cluster_context(region, cluster)
        returned = get_service_values(service_name=service, external=False)
        assert returned == expected_service_values[region][cluster][service]


def test_get_service_values_external():
    region = "saas"
    cluster = "customer"
    for service in ["service1", "service2"]:
        init_cluster_context(region, cluster)
        returned = get_service_values(
            service_name=f"k8s_root/services/{service}", external=True
        )
        assert returned == expected_service_values[region][cluster][service]


def test_get_service_value_overrides_present():
    region = "saas"
    cluster = "customer"
    for service in ["service1", "service2"]:
        init_cluster_context(region, cluster)
        returned = get_service_value_overrides(
            service_name=service,
            region_name=region,
            cluster_name=cluster,
            external=False,
        )
        assert returned == expected_service_value_overrides[region][cluster][service]


def test_get_service_value_overrides_missing():
    region = "saas"
    cluster = "customer"
    service = "service2"
    init_cluster_context(region, cluster)
    returned = get_service_value_overrides(
        service_name=service, region_name=region, cluster_name=cluster, external=False
    )
    assert returned == expected_service_value_overrides[region][cluster][service]


def test_get_service_data_present():
    region = "saas"
    cluster = "customer"
    service = "service2"
    init_cluster_context(region, cluster)
    service_data, _ = get_service_data(
        customer_name=region, service_name=service, cluster_name=cluster
    )
    assert service_data == expected_service_data[region][cluster][service]
