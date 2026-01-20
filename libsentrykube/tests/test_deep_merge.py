from libsentrykube.utils import deep_merge_dict, remove_none_values


def test_basic_merge() -> None:
    into = {
        "foo": "bar",
    }

    other = {
        "foo": "baz",
    }

    deep_merge_dict(into=into, other=other)
    assert into == {
        "foo": "baz",
    }


def test_extra_keys_into() -> None:
    into = {
        "foo": "bar",
        "baz": "qux",
    }

    other = {
        "foo": "baz",
    }

    deep_merge_dict(into=into, other=other)
    assert into == {
        "foo": "baz",
        "baz": "qux",
    }


def test_extra_keys_other() -> None:
    into = {
        "foo": "bar",
    }

    other = {
        "foo": "baz",
        "baz": "qux",
    }

    deep_merge_dict(into=into, other=other)
    assert into == {
        "foo": "baz",
        "baz": "qux",
    }


def test_basic_no_overwrite() -> None:
    into = {
        "foo": "bar",
    }

    other = {
        "foo": "baz",
    }

    deep_merge_dict(into=into, other=other, overwrite=False)
    assert into == {
        "foo": "bar",
    }


def test_deep_merge_double_dict_with_overwrite() -> None:
    into = {
        "foo": "bar",
        "baz": {
            "qux": "qat",
            "cix": 6,
        },
    }

    other = {
        "foo": "bla",
        "baz": {
            "qux": "quat",
            "ceven": 7,
        },
    }

    deep_merge_dict(into=into, other=other)
    assert into == {
        "foo": "bla",
        "baz": {
            "qux": "quat",
            "cix": 6,
            "ceven": 7,
        },
    }


def test_deep_merge_double_dict_no_overwrite() -> None:
    into = {
        "foo": "bar",
        "baz": {
            "qux": "qat",
            "cix": 6,
        },
    }

    other = {
        "foo": "bla",
        "baz": {
            "qux": "quat",
            "ceven": 7,
        },
    }

    deep_merge_dict(into=into, other=other, overwrite=False)
    assert into == {
        "foo": "bar",
        "baz": {
            "qux": "qat",
            "cix": 6,
            "ceven": 7,
        },
    }


def test_none_marks_key_for_deletion() -> None:
    """When a key has None value in other, it should be set to None in into."""
    into = {
        "foo": "bar",
        "baz": "qux",
    }

    other = {
        "foo": None,
    }

    deep_merge_dict(into=into, other=other)
    assert into == {
        "foo": None,
        "baz": "qux",
    }


def test_none_added_when_not_in_into() -> None:
    """When a key has None value in other and doesn't exist in into, it should be added as None."""
    into = {
        "baz": "qux",
    }

    other = {
        "foo": None,
    }

    deep_merge_dict(into=into, other=other)
    assert into == {
        "foo": None,
        "baz": "qux",
    }


def test_none_propagates_through_multiple_merges() -> None:
    """None values should propagate through multiple merge layers to enable deletion from base."""
    # This simulates the hierarchical merge scenario:
    # 1. Base service values
    base = {
        "feature": {"enabled": True, "config": "value"},
        "other": "keep",
    }

    # 2. Hierarchical override that wants to remove "feature"
    hierarchical = {
        "feature": None,
        "extra": "added",
    }

    # First merge: hierarchical into base
    deep_merge_dict(into=base, other=hierarchical)

    # feature should be None (marked for deletion), not removed yet
    assert base == {
        "feature": None,
        "other": "keep",
        "extra": "added",
    }

    # After cleanup, feature should be removed
    remove_none_values(base)
    assert base == {
        "other": "keep",
        "extra": "added",
    }


def test_remove_none_values_basic() -> None:
    """remove_none_values should remove all keys with None values."""
    d = {
        "foo": None,
        "bar": "keep",
        "baz": None,
    }

    remove_none_values(d)
    assert d == {"bar": "keep"}


def test_remove_none_values_nested() -> None:
    """remove_none_values should recursively remove None values in nested dicts."""
    d = {
        "top": None,
        "nested": {
            "inner": None,
            "keep": "value",
        },
        "keep_top": "value",
    }

    remove_none_values(d)
    assert d == {
        "nested": {
            "keep": "value",
        },
        "keep_top": "value",
    }


def test_remove_none_values_empty_dict() -> None:
    """remove_none_values should handle empty dicts."""
    d: dict = {}
    remove_none_values(d)
    assert d == {}


def test_multi_layer_merge_with_cleanup() -> None:
    """
    Simulates the real-world scenario where:
    1. Base service config has a feature
    2. Group _values.yaml sets it to None
    3. Region override also sets it to None
    4. Final cleanup removes it
    """
    # Base service _values.yaml
    service_values = {
        "gcp_secret_keys": {
            "objectstore": {"key": "value"},
        },
        "config": {"setting": True},
    }

    # Group _values.yaml (single-tenant/_values.yaml)
    group_values = {
        "gcp_secret_keys": None,
        "single_tenant": True,
    }

    # Region override (disney/default.yaml)
    region_values = {
        "gcp_secret_keys": None,
        "region_specific": "disney",
    }

    # Simulate hierarchical merge (what get_hierarchical_value_overrides does)
    hierarchical_base: dict[str, object] = {}
    deep_merge_dict(hierarchical_base, group_values)
    deep_merge_dict(hierarchical_base, region_values)

    # hierarchical_base should still have gcp_secret_keys: None (not removed)
    assert hierarchical_base == {
        "gcp_secret_keys": None,
        "single_tenant": True,
        "region_specific": "disney",
    }

    # Now merge hierarchical into service (what _consolidate_variables does)
    deep_merge_dict(service_values, hierarchical_base)

    # service_values should have gcp_secret_keys: None
    assert service_values == {
        "gcp_secret_keys": None,
        "config": {"setting": True},
        "single_tenant": True,
        "region_specific": "disney",
    }

    # Final cleanup
    remove_none_values(service_values)

    # gcp_secret_keys should be gone
    assert service_values == {
        "config": {"setting": True},
        "single_tenant": True,
        "region_specific": "disney",
    }
