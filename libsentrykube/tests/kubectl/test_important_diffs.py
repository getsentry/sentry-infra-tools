#!/usr/bin/env python3
import io

import yaml
from libsentrykube.kubectl import important_diffs

TEST_DEPLOYMENT_DATA = """
apiVersion: apps/v1
kind: Deployment
metadata:
  generation: 984
  labels:
    cogs_category: misc
    component: test-deploy
    environment: test
    service: service3
  name: test-deploy
  namespace: default
spec:
  minReadySeconds: 30
  progressDeadlineSeconds: 600
  replicas: 1
  revisionHistoryLimit: 10
  selector:
    matchLabels:
      component: test-deploy
      environment: test
      service: service3
  strategy:
    rollingUpdate:
      maxSurge: 100%
      maxUnavailable: 0%
    type: RollingUpdate
  template:
    metadata:
      annotations:
        configVersion: 08c6789bc6b2834471aee7338894cd01
        kubectl.kubernetes.io/restartedAt: "2023-07-05T12:23:04-07:00"
      creationTimestamp: null
      labels:
        cogs_category: misc
        component: test-deploy
        environment: test
        service: service3
    spec:
      containers:
      - args:
        - test-deploy
        image: us.gcr.io/sentryio/service3:2ae670d8081905329750ce3730637aff1f13b73b
        imagePullPolicy: IfNotPresent
        name: transactions-subscriptions-scheduler
        resources:
          limits:
            cpu: 1500m
            memory: 1536Mi
          requests:
            cpu: "1"
            memory: 1536Mi
        terminationMessagePath: /dev/termination-log
        terminationMessagePolicy: File
      dnsPolicy: ClusterFirst
      nodeSelector:
        nodepool.sentry.io/name: n2-highcpu-64-01
      restartPolicy: Always
      schedulerName: default-scheduler
      securityContext: {}
      terminationGracePeriodSeconds: 30
"""


def test_process_data() -> None:
    input_stream = io.StringIO(TEST_DEPLOYMENT_DATA)
    output_stream = io.StringIO()

    apply_results = important_diffs.process_file(
        "/tmp/fake_path", input_stream, output_stream
    )
    data = yaml.safe_load(output_stream.getvalue())

    # Make sure original values are returned
    assert apply_results == [
        important_diffs.ApplyResult(
            jsonpath="metadata.generation",
            original_values=[984],
        ),
        important_diffs.ApplyResult(
            jsonpath="spec.template.metadata.annotations.configVersion",
            original_values=["08c6789bc6b2834471aee7338894cd01"],
        ),
        important_diffs.ApplyResult(
            jsonpath="spec.template.spec.containers[*].image",
            original_values=[
                "us.gcr.io/sentryio/service3:2ae670d8081905329750ce3730637aff1f13b73b"
            ],
        ),
    ]

    # Make sure fields are ignored
    assert "generation" not in data["metadata"], "metadata.generation should be ignored"
    assert (
        "configVersion" not in data["spec"]["template"]["metadata"]["annotations"]
    ), "configVersion should be ignored"
    assert all(
        "image" not in container
        for container in data["spec"]["template"]["spec"]["containers"]
    ), "image should be ignored"
