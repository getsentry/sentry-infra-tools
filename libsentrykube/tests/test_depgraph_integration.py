from unittest.mock import MagicMock, patch

from libsentrykube.depgraph import (
    get_dependencies,
    reset_tracking,
    start_tracking,
    stop_tracking,
)
from libsentrykube.ext import ValuesOf


class TestValuesOfRecordsDependency:
    def setup_method(self):
        reset_tracking()

    def teardown_method(self):
        reset_tracking()

    @patch("libsentrykube.ext.render_service_values")
    def test_values_of_records_dependency_when_tracking(
        self,
        mock_render,
    ):
        mock_render.return_value = {"some": "values"}

        ValuesOf.install("values_of")
        ext = ValuesOf()
        context = MagicMock()
        context.parent = {"customer": {"id": "customer1"}}

        start_tracking("service_a")
        ext.run(context, "service_b")
        stop_tracking()

        deps = get_dependencies()
        assert deps == {"service_a": {"service_b"}}

    @patch("libsentrykube.ext.render_service_values")
    def test_values_of_no_record_without_tracking(
        self,
        mock_render,
    ):
        mock_render.return_value = {"some": "values"}

        ValuesOf.install("values_of")
        ext = ValuesOf()
        context = MagicMock()
        context.parent = {"customer": {"id": "customer1"}}

        ext.run(context, "service_b")

        deps = get_dependencies()
        assert deps == {}

    @patch("libsentrykube.ext.render_service_values")
    def test_values_of_records_external_dependency(
        self,
        mock_render,
    ):
        mock_render.return_value = {"some": "values"}

        ValuesOf.install("values_of")
        ext = ValuesOf()
        context = MagicMock()
        context.parent = {"customer": {"id": "customer1"}}

        start_tracking("service_a")
        ext.run(context, "k8s/services/external_svc", external=True)
        stop_tracking()

        deps = get_dependencies()
        assert deps == {"service_a": {"k8s/services/external_svc"}}


class TestRenderTemplatesTracking:
    def setup_method(self):
        reset_tracking()

    def teardown_method(self):
        reset_tracking()

    @patch("libsentrykube.kube.get_service_path")
    @patch("libsentrykube.kube.get_service_flags")
    @patch("libsentrykube.kube.get_service_template_files")
    @patch("libsentrykube.kube.get_service_data")
    @patch("libsentrykube.kube._consolidate_variables")
    @patch("libsentrykube.kube.load_macros")
    def test_render_templates_wraps_with_tracking(
        self,
        mock_macros,
        mock_consolidate,
        mock_service_data,
        mock_template_files,
        mock_flags,
        mock_path,
    ):
        """render_templates should call start_tracking/stop_tracking."""
        from pathlib import Path
        import tempfile
        import os

        from libsentrykube.depgraph import _current_service

        mock_macros.return_value = []
        mock_consolidate.return_value = {"key": "val"}
        mock_service_data.return_value = ({}, {"customer": {"id": "c1"}})
        mock_flags.return_value = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            svc_dir = Path(tmpdir) / "my_svc"
            os.makedirs(svc_dir)
            template = svc_dir / "deployment.yaml"
            template.write_text("kind: Deployment\n")

            mock_path.return_value = svc_dir
            mock_template_files.return_value = [template]

            was_tracking = []

            orig_render_inner = __import__(
                "libsentrykube.kube", fromlist=["_render_templates_inner"]
            )._render_templates_inner

            def spy_inner(*args, **kwargs):
                was_tracking.append(getattr(_current_service, "name", None))
                return orig_render_inner(*args, **kwargs)

            with patch(
                "libsentrykube.kube._render_templates_inner",
                side_effect=spy_inner,
            ):
                from libsentrykube.kube import render_templates

                render_templates("customer1", "my_svc", "default")

            assert was_tracking == ["my_svc"]
            assert getattr(_current_service, "name", None) is None
