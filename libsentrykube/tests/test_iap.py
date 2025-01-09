from libsentrykube.iap import _dns_endpoint_check


def test_dns_endpoint_detects_valid_host():
    use_dns_endpoint = _dns_endpoint_check(
        control_plane_host="gke-22df3be7a2d24d7eb1935c53b5cfaa2337ea-249720712700.us-east1.gke.goog",
        quiet=True,
    )
    assert use_dns_endpoint is True


def test_dns_endpoint_detects_invalid_host():
    use_dns_endpoint = _dns_endpoint_check(control_plane_host="172.16.0.13", quiet=True)
    assert use_dns_endpoint is False
