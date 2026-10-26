import threading

from libsentrykube.depgraph import (
    DependencyGraph,
    get_dependencies,
    get_reverse_dependencies,
    record_dependency,
    reset_tracking,
    start_tracking,
    stop_tracking,
)


class TestDependencyTracking:
    def setup_method(self):
        reset_tracking()

    def teardown_method(self):
        reset_tracking()

    def test_no_tracking_by_default(self):
        record_dependency("other_service")
        assert get_dependencies() == {}

    def test_start_and_stop_tracking(self):
        start_tracking("service_a")
        stop_tracking()
        assert get_dependencies() == {}

    def test_record_single_dependency(self):
        start_tracking("service_a")
        record_dependency("service_b")
        stop_tracking()

        deps = get_dependencies()
        assert deps == {"service_a": {"service_b"}}

    def test_record_multiple_dependencies(self):
        start_tracking("service_a")
        record_dependency("service_b")
        record_dependency("service_c")
        stop_tracking()

        deps = get_dependencies()
        assert deps == {"service_a": {"service_b", "service_c"}}

    def test_record_across_multiple_services(self):
        start_tracking("service_a")
        record_dependency("service_b")
        stop_tracking()

        start_tracking("service_c")
        record_dependency("service_b")
        record_dependency("service_d")
        stop_tracking()

        deps = get_dependencies()
        assert deps == {
            "service_a": {"service_b"},
            "service_c": {"service_b", "service_d"},
        }

    def test_duplicate_dependency_deduplicated(self):
        start_tracking("service_a")
        record_dependency("service_b")
        record_dependency("service_b")
        stop_tracking()

        deps = get_dependencies()
        assert deps == {"service_a": {"service_b"}}

    def test_self_dependency_ignored(self):
        start_tracking("service_a")
        record_dependency("service_a")
        stop_tracking()

        assert get_dependencies() == {}

    def test_reset_clears_all(self):
        start_tracking("service_a")
        record_dependency("service_b")
        stop_tracking()

        reset_tracking()
        assert get_dependencies() == {}

    def test_stop_without_start_is_noop(self):
        stop_tracking()
        assert get_dependencies() == {}


class TestReverseDepedencies:
    def setup_method(self):
        reset_tracking()

    def teardown_method(self):
        reset_tracking()

    def test_empty_graph(self):
        assert get_reverse_dependencies() == {}

    def test_single_edge(self):
        start_tracking("service_a")
        record_dependency("service_b")
        stop_tracking()

        rev = get_reverse_dependencies()
        assert rev == {"service_b": {"service_a"}}

    def test_multiple_dependents(self):
        start_tracking("service_a")
        record_dependency("service_x")
        stop_tracking()

        start_tracking("service_b")
        record_dependency("service_x")
        stop_tracking()

        rev = get_reverse_dependencies()
        assert rev == {"service_x": {"service_a", "service_b"}}

    def test_complex_graph(self):
        start_tracking("service_a")
        record_dependency("service_b")
        record_dependency("service_c")
        stop_tracking()

        start_tracking("service_d")
        record_dependency("service_b")
        stop_tracking()

        rev = get_reverse_dependencies()
        assert rev == {
            "service_b": {"service_a", "service_d"},
            "service_c": {"service_a"},
        }


class TestDependencyGraph:
    def test_from_edges(self):
        edges = {
            "service_a": {"service_b", "service_c"},
            "service_d": {"service_b"},
        }
        graph = DependencyGraph(edges)

        assert graph.dependencies_of("service_a") == {
            "service_b",
            "service_c",
        }
        assert graph.dependencies_of("service_d") == {"service_b"}
        assert graph.dependencies_of("service_b") == set()

    def test_dependents_of(self):
        edges = {
            "service_a": {"service_b", "service_c"},
            "service_d": {"service_b"},
        }
        graph = DependencyGraph(edges)

        assert graph.dependents_of("service_b") == {
            "service_a",
            "service_d",
        }
        assert graph.dependents_of("service_c") == {"service_a"}
        assert graph.dependents_of("service_a") == set()

    def test_to_dict(self):
        edges = {
            "service_a": {"service_b"},
        }
        graph = DependencyGraph(edges)
        d = graph.to_dict()

        assert d == {
            "dependencies": {"service_a": ["service_b"]},
            "reverse_dependencies": {"service_b": ["service_a"]},
        }

    def test_to_dict_sorted_values(self):
        edges = {
            "service_a": {"service_c", "service_b"},
        }
        graph = DependencyGraph(edges)
        d = graph.to_dict()

        assert d["dependencies"]["service_a"] == [
            "service_b",
            "service_c",
        ]

    def test_from_dict(self):
        data = {
            "dependencies": {
                "service_a": ["service_b", "service_c"],
                "service_d": ["service_b"],
            },
            "reverse_dependencies": {
                "service_b": ["service_a", "service_d"],
                "service_c": ["service_a"],
            },
        }
        graph = DependencyGraph.from_dict(data)

        assert graph.dependencies_of("service_a") == {
            "service_b",
            "service_c",
        }
        assert graph.dependents_of("service_b") == {
            "service_a",
            "service_d",
        }

    def test_roundtrip_dict(self):
        edges = {
            "service_a": {"service_b", "service_c"},
            "service_d": {"service_b"},
        }
        original = DependencyGraph(edges)
        restored = DependencyGraph.from_dict(original.to_dict())

        assert restored.dependencies_of("service_a") == original.dependencies_of(
            "service_a"
        )
        assert restored.dependents_of("service_b") == original.dependents_of(
            "service_b"
        )


class TestThreadSafety:
    def setup_method(self):
        reset_tracking()

    def teardown_method(self):
        reset_tracking()

    def test_concurrent_tracking_isolated(self):
        """Each thread should track its own current service."""
        results = {}
        barrier = threading.Barrier(2)

        def track_service(name, dep):
            start_tracking(name)
            barrier.wait()
            record_dependency(dep)
            barrier.wait()
            stop_tracking()
            results[name] = True

        t1 = threading.Thread(target=track_service, args=("svc_a", "dep_1"))
        t2 = threading.Thread(target=track_service, args=("svc_b", "dep_2"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        deps = get_dependencies()
        assert deps["svc_a"] == {"dep_1"}
        assert deps["svc_b"] == {"dep_2"}
