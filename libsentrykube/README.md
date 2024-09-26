# libsentrykube

"kubectl scares me." - matt

Summarily, this is sentry's safer and opinionated kubernetes client routines.

To use libsentrykube in a cli client (IOW. you're writing a `sentry-kube` or `st-sentry`), you must first initialize the client by setting the kubernetes context. For example:

```python
    from libsentrykube.utils import kube_set_context
    kube_set_context("minikube")
```

## Running tests

From root of repo:

```
make venv-init
make tools-test
```
