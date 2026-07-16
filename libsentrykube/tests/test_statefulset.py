import json
from unittest.mock import MagicMock, patch

import click
import pytest

from libsentrykube.statefulset import (
    ANNOTATION_DESIRED,
    ANNOTATION_STATUS,
    ANNOTATION_SKIP,
    PVCInfo,
    StatefulSetInfo,
    build_desired_annotation,
    format_timestamp,
    parse_storage_quantity,
    validate_storage_expansion,
)


class TestParseStorageQuantity:
    def test_gi(self):
        assert parse_storage_quantity("10Gi") == 10 * 1024**3

    def test_mi(self):
        assert parse_storage_quantity("500Mi") == 500 * 1024**2

    def test_ti(self):
        assert parse_storage_quantity("1Ti") == 1024**4

    def test_ki(self):
        assert parse_storage_quantity("100Ki") == 100 * 1024

    def test_decimal_g(self):
        assert parse_storage_quantity("10G") == 10 * 1000**3

    def test_plain_bytes(self):
        assert parse_storage_quantity("1073741824") == 1073741824

    def test_whitespace(self):
        assert parse_storage_quantity("  10Gi  ") == 10 * 1024**3


class TestValidateStorageExpansion:
    def test_valid_expansion(self):
        validate_storage_expansion("10Gi", "20Gi")

    def test_shrink_raises(self):
        with pytest.raises(click.BadParameter, match="smaller"):
            validate_storage_expansion("20Gi", "10Gi")

    def test_same_size_raises(self):
        with pytest.raises(click.BadParameter, match="same"):
            validate_storage_expansion("10Gi", "10Gi")


class TestBuildDesiredAnnotation:
    def test_storage_only(self):
        result = build_desired_annotation("data", storage="20Gi")
        assert result == {
            "version": 1,
            "claims": {"data": {"storage": "20Gi"}},
        }

    def test_vac_only(self):
        result = build_desired_annotation("data", vac="vac-fast")
        assert result == {
            "version": 1,
            "claims": {"data": {"volumeAttributesClassName": "vac-fast"}},
        }

    def test_both(self):
        result = build_desired_annotation("data", storage="20Gi", vac="vac-fast")
        assert result == {
            "version": 1,
            "claims": {
                "data": {
                    "storage": "20Gi",
                    "volumeAttributesClassName": "vac-fast",
                }
            },
        }

    def test_batch_size(self):
        result = build_desired_annotation("data", storage="20Gi", batch_size=2)
        assert result == {
            "version": 1,
            "claims": {"data": {"storage": "20Gi"}},
            "batchSize": 2,
        }

    def test_batch_size_zero_omitted(self):
        result = build_desired_annotation("data", storage="20Gi", batch_size=0)
        assert "batchSize" not in result


class TestFormatTimestamp:
    def test_none(self):
        assert format_timestamp(None) == "?"

    def test_invalid(self):
        assert format_timestamp("not-a-date") == "not-a-date"

    def test_recent(self):
        from datetime import datetime, timezone, timedelta

        ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        result = format_timestamp(ts)
        assert result.endswith("s ago")

    def test_minutes(self):
        from datetime import datetime, timezone, timedelta

        ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        result = format_timestamp(ts)
        assert result.endswith("m ago")


class TestStatefulSetInfo:
    def test_idle_state(self):
        info = StatefulSetInfo(
            name="broker",
            namespace="taskbroker",
            replicas=3,
            ready_replicas=3,
            volume_claim_templates=[
                {"name": "data", "storage": "10Gi", "storageClassName": "standard", "volumeAttributesClassName": ""},
            ],
            desired_annotation=None,
            status_annotation=None,
            skip_annotation=False,
        )
        assert info.replicas == 3
        assert info.desired_annotation is None

    def test_active_state(self):
        info = StatefulSetInfo(
            name="broker",
            namespace="taskbroker",
            replicas=3,
            ready_replicas=3,
            volume_claim_templates=[
                {"name": "data", "storage": "10Gi", "storageClassName": "standard", "volumeAttributesClassName": ""},
            ],
            desired_annotation={"version": 1, "claims": {"data": {"storage": "20Gi"}}},
            status_annotation={"version": 1, "state": "Patching", "pvcs": {}},
            skip_annotation=False,
        )
        assert info.desired_annotation is not None
        assert info.status_annotation["state"] == "Patching"
