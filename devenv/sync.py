from __future__ import annotations

from devenv.lib import config, venv


def main(context: dict[str, str]) -> int:
    repo = context["repo"]
    reporoot = context["reporoot"]

    venv_dir, python_version, requirements, editable_paths, bins = venv.get(
        reporoot, repo
    )
    url, sha256 = config.get_python(reporoot, python_version)
    print(f"ensuring {repo} venv at {venv_dir}...")
    venv.ensure(venv_dir, python_version, url, sha256)

    print(f"syncing {repo} with {requirements}...")
    venv.sync(reporoot, venv_dir, requirements, editable_paths, bins)

    return 0
