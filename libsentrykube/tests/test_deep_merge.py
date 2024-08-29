from libsentrykube.utils import deep_merge_dict


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
