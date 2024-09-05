from typing import Sequence

from setuptools import find_packages
from setuptools import setup

macros = [
    "iap_service=sentry_kube.ext:IAPService",
    "pgbouncer_sidecar=sentry_kube.ext:PGBouncerSidecar",
    "xds_configmap_from=sentry_kube.ext:XDSConfigMapFrom",
]


def get_requirements() -> Sequence[str]:
    with open("requirements.txt") as f:
        return [x.strip() for x in f.read().split("\n") if not x.startswith(("#", "--"))]


setup(
    name="sentry-infra-tools",
    version="0.0.1",
    author="Sentry",
    author_email="oss@sentry.io",
    packages=find_packages("."),
    install_requires=get_requirements(),
    zip_safe=False,
    include_package_data=True,
    entry_points={
        "console_scripts": [
            "sentry-kube=sentry_kube.cli:main",
            "materialize-config=config_builder.materialize_all:main",
            "generate-raw-topic-data=config_builder.generate_topic_data:main",
            "pr-docs=assistant.prdocs:main",
            "pr-approver=pr_approver.approver:main",
        ],
        "libsentrykube.macros": macros,
    },
    scripts=["bin/sentry-kube-pop"],
    classifiers=["DO NOT UPLOAD"],
    python_requires=">=3.9",
)
