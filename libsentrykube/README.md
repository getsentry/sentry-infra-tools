# libsentrykube

"kubectl scares me." - matt

Summarily, this is sentry's safer and opinionated kubernetes client routines.

To use libsentrykube in a cli client (IOW. you're writing a `sentry-kube` or `st-sentry`), you must first initialize the client by setting the kubernetes context. For example:

```python
    from libsentrykube.utils import kube_set_context
    kube_set_context("minikube")
```

## External Macros

External macros let you define reusable Jinja template helpers in any
installed Python package, without modifying `libsentrykube` itself. They are
loaded dynamically at render time via the `render_external` Jinja global.

### Defining a macro

Subclass `ExternalMacro` and implement two methods:

- `validate_context(context)` -- a static method that raises `ValueError` if
  the context dict is invalid.
- `run(self, context)` -- accepts the context dict and returns a dict that
  will be rendered into the template output.

```python
from typing import Any
from libsentrykube.ext import ExternalMacro


class MyMacro(ExternalMacro):
    @staticmethod
    def validate_context(context: dict[str, Any]) -> None:
        if "name" not in context:
            raise ValueError("Context must contain 'name'")

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "greeting": f"hello {context['name']}",
            "tags": context.get("tags", []),
        }
```

The class must be importable at runtime, so it needs to live in a package
that is installed in the same Python environment as `libsentrykube`.

### Calling a macro from a Jinja template

Use the `render_external` function with the fully-qualified class name and a
context dict:

```yaml
metadata:
  annotations: {{ render_external("mypackage.macros.MyMacro", {"name": "world", "tags": ["a", "b"]}) }}
```

`render_external` will:

1. Import the module and locate the class.
2. Verify the class is a subclass of `ExternalMacro`.
3. Call `validate_context()` on the provided context.
4. Instantiate the class and call `run()`, returning the resulting dict.

### Differences from built-in SimpleExtension macros

| | SimpleExtension | ExternalMacro |
|---|---|---|
| Registration | Entry point in `setup.py` | None -- loaded by fully-qualified name |
| Arguments | Individual keyword args | Single `context` dict |
| Return type | Typically a JSON string | `dict` |
| Package | Must live in `libsentrykube` | Can live in any installed package |

## Service Flags (`_sk_flags.yaml`)

Service flags let you configure per-service behavior for rendering and
applying Kubernetes manifests. Flags are defined in a `_sk_flags.yaml` file
placed in the service directory (alongside templates and `_values.yaml`).

### File format

```yaml
# k8s/services/my-service/_sk_flags.yaml
jinja_whitespace_easymode: false
server_side_apply: true
force_conflicts: true
```

If the file is absent or a flag is omitted, the default value is used.

### Available flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `jinja_whitespace_easymode` | bool | `true` | Enables `trim_blocks` and `lstrip_blocks` in the Jinja environment, making whitespace handling more forgiving. Set to `false` if you need precise control over whitespace in templates. |
| `server_side_apply` | bool | `false` | Use server-side apply (SSA) for `sentry-kube diff` and `sentry-kube apply`. SSA tracks field ownership on the server and avoids the `last-applied-configuration` annotation, which can exceed the 256 KB annotation size limit for large manifests. |
| `force_conflicts` | bool | `false` | When `server_side_apply` is enabled, also pass `--force-conflicts` to override field ownership conflicts. |

### How flags are resolved

1. Defaults are defined in `libsentrykube/kube.py` as `DEFAULT_FLAGS`.
2. Per-service overrides from `_sk_flags.yaml` are merged on top (service
   values win).
3. For `server_side_apply` and `force_conflicts`, CLI flags
   (`--server-side` / `--no-server-side`, `--force-conflicts` /
   `--no-force-conflicts`) take precedence when explicitly provided.
4. If multiple services are diffed or applied together and they have
   conflicting SSA settings (without a CLI override), an error is raised
   asking you to process them one at a time.

## Service Dependency Graph

Services can reference other services' values at render time using the
`values_of()` Jinja macro. For example, a getsentry template might call
`{{ values_of("pgbouncer") }}` to read pgbouncer's merged configuration.
This creates a hidden dependency: a change to pgbouncer's values can
affect getsentry's rendered output.

The dependency graph module (`libsentrykube/depgraph.py`) instruments
`values_of()` to automatically track these cross-service references during
rendering, then exposes the resulting graph for CI and tooling use.

### How it works

When a service's templates are rendered via `render_templates()`, the
rendering pipeline:

1. Calls `start_tracking(service_name)` before rendering begins.
2. Each `values_of("other_service")` call inside the templates triggers
   `record_dependency("other_service")`, which records the edge
   `service_name -> other_service`.
3. Calls `stop_tracking()` after rendering completes.

Tracking is thread-safe (each thread tracks its own "current service" via
thread-local storage, edges are collected in a global lock-protected set)
and is a no-op when no tracking session is active, so there is zero overhead
during normal `sentry-kube render` / `sentry-kube apply` workflows.

### CLI usage

The `sentry-kube depgraph` command renders all services across all
regions and clusters, collects the dependency edges, and outputs a JSON
graph:

```shell
sentry-kube depgraph
sentry-kube depgraph --stage production
```

Output format:

```json
{
  "dependencies": {
    "getsentry": ["pgbouncer", "seer"],
    "service-a": ["service-b"]
  },
  "reverse_dependencies": {
    "pgbouncer": ["getsentry"],
    "seer": ["getsentry"],
    "service-b": ["service-a"]
  }
}
```

- `dependencies`: for each service, lists the services it pulls values
  from (i.e., "getsentry depends on pgbouncer").
- `reverse_dependencies`: the inverse -- for each service, lists the
  services that would be affected if it changes (i.e., "if pgbouncer
  changes, getsentry is affected").

### Programmatic usage

```python
from libsentrykube.depgraph import DependencyGraph, build_dependency_graph

# Build the graph by rendering everything
graph = build_dependency_graph(stage="production")

# Query forward dependencies (what does getsentry depend on?)
graph.dependencies_of("getsentry")  # {"pgbouncer", "seer"}

# Query reverse dependencies (what is affected if pgbouncer changes?)
graph.dependents_of("pgbouncer")  # {"getsentry"}

# Serialize to JSON-friendly dict
data = graph.to_dict()

# Restore from serialized dict
restored = DependencyGraph.from_dict(data)
```

### Limitations

- Only `values_of()` dependencies are tracked. Other cross-service
  references via `json_file()` or `md5file()` (which take arbitrary
  workspace-relative paths) are not yet instrumented.
- Dependencies can vary per region/cluster due to conditional Jinja logic.
  The graph produced by `build_dependency_graph()` is the union across all
  rendered contexts, which is conservative (it may include edges that only
  apply to specific regions).
- The graph must be rebuilt when templates or cluster configurations
  change, since dependencies are discovered at render time.

## Running tests

From root of repo:

```
make venv-init
make tools-test
```
