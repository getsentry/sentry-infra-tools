from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from click.testing import CliRunner
from jinja2.exceptions import UndefinedError

from libsentrykube.reversemap import ResourceReference
from sentry_kube.render_services import _render_multithreaded, render_services


def _make_resource(customer, cluster, service=None):
    return ResourceReference(
        customer_name=customer,
        cluster_name=cluster,
        service_name=service,
    )


class TestRenderMultithreaded:
    """Tests for _render_multithreaded.

    ProcessPoolExecutor is replaced with ThreadPoolExecutor so that
    mocked callables don't need to survive pickling across processes.
    """

    @patch("sentry_kube.render_services.ProcessPoolExecutor", ThreadPoolExecutor)
    @patch("sentry_kube.render_services.init_cluster_context")
    @patch("sentry_kube.render_services._materialize_service_worker")
    def test_all_succeed(self, mock_worker, mock_init_ctx):
        mock_worker.side_effect = [
            ("cust1", "cluster-a", "svc-a", False),
            ("cust1", "cluster-a", "svc-b", True),
        ]

        resources = [
            _make_resource("cust1", "cluster-a", "svc-a"),
            _make_resource("cust1", "cluster-a", "svc-b"),
        ]

        changes_made, errors = _render_multithreaded(
            resources, split_by_kind=False, workers=1
        )

        assert errors == []
        assert changes_made is True

    @patch("sentry_kube.render_services.ProcessPoolExecutor", ThreadPoolExecutor)
    @patch("sentry_kube.render_services.init_cluster_context")
    @patch("sentry_kube.render_services._materialize_service_worker")
    def test_no_changes(self, mock_worker, mock_init_ctx):
        mock_worker.return_value = ("cust1", "cluster-a", "svc-a", False)

        resources = [_make_resource("cust1", "cluster-a", "svc-a")]

        changes_made, errors = _render_multithreaded(
            resources, split_by_kind=False, workers=1
        )

        assert errors == []
        assert changes_made is False

    @patch("sentry_kube.render_services.ProcessPoolExecutor", ThreadPoolExecutor)
    @patch("sentry_kube.render_services.init_cluster_context")
    @patch("sentry_kube.render_services._materialize_service_worker")
    def test_partial_failure_collects_errors(self, mock_worker, mock_init_ctx):
        error = UndefinedError("'dict object' has no attribute 'missing_var'")
        mock_worker.side_effect = [
            ("cust1", "cluster-a", "svc-ok", False),
            error,
        ]

        resources = [
            _make_resource("cust1", "cluster-a", "svc-ok"),
            _make_resource("cust1", "cluster-a", "svc-bad"),
        ]

        changes_made, errors = _render_multithreaded(
            resources, split_by_kind=False, workers=1
        )

        assert len(errors) == 1
        _, _, svc, exc = errors[0]
        assert svc == "svc-bad"
        assert isinstance(exc, UndefinedError)
        assert changes_made is False

    @patch("sentry_kube.render_services.ProcessPoolExecutor", ThreadPoolExecutor)
    @patch("sentry_kube.render_services.init_cluster_context")
    @patch("sentry_kube.render_services._materialize_service_worker")
    def test_all_fail(self, mock_worker, mock_init_ctx):
        mock_worker.side_effect = [
            RuntimeError("boom1"),
            RuntimeError("boom2"),
        ]

        resources = [
            _make_resource("cust1", "cluster-a", "svc-a"),
            _make_resource("cust2", "cluster-b", "svc-b"),
        ]

        changes_made, errors = _render_multithreaded(
            resources, split_by_kind=False, workers=1
        )

        assert len(errors) == 2
        assert changes_made is False

    @patch("sentry_kube.render_services.ProcessPoolExecutor", ThreadPoolExecutor)
    @patch("sentry_kube.render_services.init_cluster_context")
    @patch("sentry_kube.render_services._materialize_service_worker")
    def test_error_summary_output(self, mock_worker, mock_init_ctx, capsys):
        mock_worker.side_effect = [
            UndefinedError("missing_var"),
            ("cust1", "cluster-a", "svc-ok", False),
        ]

        resources = [
            _make_resource("cust1", "cluster-a", "svc-bad"),
            _make_resource("cust1", "cluster-a", "svc-ok"),
        ]

        _render_multithreaded(resources, split_by_kind=False, workers=1)

        captured = capsys.readouterr()
        assert "1 service(s) failed to render:" in captured.err
        assert "svc-bad" in captured.err
        assert "Service FAILED:" in captured.err


class TestRenderServicesCLI:
    @patch("sentry_kube.render_services.extract_clusters")
    @patch("sentry_kube.render_services.build_index")
    @patch("sentry_kube.render_services.init_cluster_context")
    @patch("sentry_kube.render_services.materialize")
    def test_single_threaded_success_exits_zero(
        self, mock_materialize, mock_init_ctx, mock_build_index, mock_extract
    ):
        mock_materialize.return_value = False
        mock_extract.return_value = [
            _make_resource("cust1", "cluster-a", "svc-a"),
        ]

        runner = CliRunner()
        result = runner.invoke(render_services, ["some-file"])

        assert result.exit_code == 0

    @patch("sentry_kube.render_services.extract_clusters")
    @patch("sentry_kube.render_services.build_index")
    @patch("sentry_kube.render_services.init_cluster_context")
    @patch("sentry_kube.render_services.materialize")
    def test_single_threaded_failure_exits_nonzero(
        self, mock_materialize, mock_init_ctx, mock_build_index, mock_extract
    ):
        mock_materialize.side_effect = UndefinedError("missing_var")
        mock_extract.return_value = [
            _make_resource("cust1", "cluster-a", "svc-a"),
        ]

        runner = CliRunner()
        result = runner.invoke(render_services, ["some-file"])

        assert result.exit_code == 1
        assert "1 service(s) failed to render:" in result.output
        assert "svc-a" in result.output

    @patch("sentry_kube.render_services.extract_clusters")
    @patch("sentry_kube.render_services.build_index")
    @patch("sentry_kube.render_services.init_cluster_context")
    @patch("sentry_kube.render_services.materialize")
    def test_single_threaded_partial_failure_processes_all(
        self, mock_materialize, mock_init_ctx, mock_build_index, mock_extract
    ):
        mock_materialize.side_effect = [
            False,
            UndefinedError("missing_var"),
            False,
        ]
        mock_extract.return_value = [
            _make_resource("cust1", "cluster-a", "svc-ok1"),
            _make_resource("cust1", "cluster-a", "svc-bad"),
            _make_resource("cust1", "cluster-a", "svc-ok2"),
        ]

        runner = CliRunner()
        result = runner.invoke(render_services, ["some-file"])

        assert result.exit_code == 1
        assert "Service unchanged: cust1 : cluster-a : svc-ok1" in result.output
        assert "Service unchanged: cust1 : cluster-a : svc-ok2" in result.output
        assert "1 service(s) failed to render:" in result.output

    @patch("sentry_kube.render_services._render_multithreaded")
    @patch("sentry_kube.render_services.extract_clusters")
    @patch("sentry_kube.render_services.build_index")
    def test_multithreaded_failure_exits_nonzero(
        self, mock_build_index, mock_extract, mock_render_mt
    ):
        mock_extract.return_value = [
            _make_resource("cust1", "cluster-a", "svc-a"),
        ]
        error = UndefinedError("missing_var")
        mock_render_mt.return_value = (False, [("cust1", "cluster-a", "svc-a", error)])

        runner = CliRunner()
        result = runner.invoke(render_services, ["--multithreaded", "some-file"])

        assert result.exit_code == 1

    @patch("sentry_kube.render_services._render_multithreaded")
    @patch("sentry_kube.render_services.extract_clusters")
    @patch("sentry_kube.render_services.build_index")
    def test_multithreaded_success_exits_zero(
        self, mock_build_index, mock_extract, mock_render_mt
    ):
        mock_extract.return_value = [
            _make_resource("cust1", "cluster-a", "svc-a"),
        ]
        mock_render_mt.return_value = (False, [])

        runner = CliRunner()
        result = runner.invoke(render_services, ["--multithreaded", "some-file"])

        assert result.exit_code == 0
