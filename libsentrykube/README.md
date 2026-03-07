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

## Running tests

From root of repo:

```
make venv-init
make tools-test
```
