### Build arguments:
# SENTRY_KUBE_KUBECTL_VERSION -- kubectl version that sentry-kube will download
FROM python:3.12

RUN apt-get update && \
  apt-get install -y git curl wget jq --no-install-recommends && \
  rm -rf /var/lib/apt/lists/*

### Install a recent "yq"
RUN YQ_SHA256="9a54846e81720ae22814941905cd3b056ebdffb76bf09acffa30f5e90b22d615" \
  && YQ_TMP=/tmp/yq \
  && wget --quiet -O $YQ_TMP "https://github.com/mikefarah/yq/releases/download/v4.27.5/yq_linux_amd64" \
  && echo "$YQ_SHA256  $YQ_TMP" > yq.sha256 \
  && sha256sum -c yq.sha256 \
  && rm yq.sha256 \
  && mv $YQ_TMP /usr/local/bin/yq \
  && chmod +x /usr/local/bin/yq

### Install "helm"
RUN HELM_SHA256="2315941a13291c277dac9f65e75ead56386440d3907e0540bf157ae70f188347" \
  && HELM_TMP_DIR=/tmp/helm-install \
  && HELM_TMP=helm.tar.gz \
  && mkdir $HELM_TMP_DIR \
  && cd $HELM_TMP_DIR \
  && wget --quiet -O $HELM_TMP "https://get.helm.sh/helm-v3.10.2-linux-amd64.tar.gz" \
  && echo "$HELM_SHA256  $HELM_TMP" > helm.sha256 \
  && sha256sum -c helm.sha256 \
  && tar xf $HELM_TMP \
  && mv ./linux-amd64/helm /usr/local/bin/helm \
  && chmod +x /usr/local/bin/helm \
  && rm -r $HELM_TMP_DIR

### Install sentry-kube
ENV VIRTUAL_ENV=1
ENV SENTRY_KUBE_INSTALL_GIT_HOOKS=0
ENV SENTRY_KUBE_ROOT="/work"
ENV SENTRY_KUBE_CONFIG_FILE="/work/configuration.yaml"
ENV KUBECONFIG_PATH=$SENTRY_KUBE_ROOT/.kube/config
ENV SENTRY_KUBE_NO_CONTEXT="1"
ENV SENTRY_KUBE_CUSTOMER="us"

# Use kubectl version from the build argument
ARG SENTRY_KUBE_VERSION
ARG SENTRY_KUBE_KUBECTL_VERSION
# Persist the version as an environment variable so that subsequent "sentry-kube" calls in the
# live container would pick it up too.
ENV SENTRY_KUBE_KUBECTL_VERSION=${SENTRY_KUBE_KUBECTL_VERSION}
RUN pip install --index-url https://pypi.devinfra.sentry.io/simple sentry-infra-tools==${SENTRY_KUBE_VERSION} && rm -rf /root/.cache

### Prepare the working directory
WORKDIR /work
# Dummy context to make sentry-kube happy
RUN mkdir -p k8s/clusters && \
    mkdir -p $SENTRY_KUBE_ROOT/.kube && \
    echo '{"context": "_empty", "services": [], "iap_local_port": 22028}' > k8s/clusters/default.yaml && \
    echo "{sites: {saas_us: {name: us, region: us-central1, zone: b}}, silo_regions: {saas: {bastion: " >> configuration.yaml && \
    echo "{spawner_endpoint: 'https://test', site: saas_us}, k8s: {root: k8s, cluster_def_root: " >> configuration.yaml && \
    echo "clusters, materialized_manifests: materialized_manifests}}}}" >> configuration.yaml
