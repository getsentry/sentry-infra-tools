#!/usr/bin/env python3
import abc
import dataclasses
import json
import os
import shlex
import shutil
import tempfile
from typing import Any
from typing import Dict
from typing import Generator
from typing import IO
from typing import List
from typing import Mapping

import jsonpatch
import jsonpath_ng
import yaml


DEFAULT_DIFF_COMMAND = "diff -u -N"


@dataclasses.dataclass(frozen=True, eq=True)
class ApplyResult:
    original_values: Any
    jsonpath: str


class Rule(abc.ABC):
    """
    interface for the rule of processing non-important fields
    """

    @abc.abstractmethod
    def should_apply(self, data: Mapping[str, Any]) -> bool:
        pass

    @abc.abstractmethod
    def apply(self, data: Mapping[str, Any]) -> ApplyResult:
        pass


class JsonPatchRemoveRule(Rule):
    """
    Use JsonPatch (RFC 6902) to patch k8s objects
    """

    _jsonpath: str
    _jsonpatch_path: str

    _match_patch: jsonpatch.JsonPatch
    _apply_patch: jsonpatch.JsonPatch

    def __init__(self, jsonpatch_path: str, jsonpath: str):
        self._jsonpatch_path = jsonpatch_path
        self._jsonpath = jsonpath

        # Define a custom object to match any value for jsonpatch test operation
        class AnyObject:
            def __eq__(self, other: Any) -> bool:
                return True

        self._match_patch: jsonpatch.JsonPatch = jsonpatch.JsonPatch(
            [{"op": "test", "path": self._jsonpatch_path, "value": AnyObject()}]
        )
        self._apply_patch: jsonpatch.JsonPatch = jsonpatch.JsonPatch(
            [{"op": "remove", "path": self._jsonpatch_path}]
        )
        self._jsonpath_expr = jsonpath_ng.parse(self._jsonpath)

    def should_apply(self, data: Mapping[str, Any]) -> bool:
        try:
            self._match_patch.apply(data)
        except jsonpatch.JsonPatchTestFailed:
            return False

        return True

    def apply(self, data: Mapping[str, Any]) -> ApplyResult:
        original_values = [match.value for match in self._jsonpath_expr.find(data)]
        self._apply_patch.apply(data, True)
        return ApplyResult(original_values=original_values, jsonpath=self._jsonpath)


class ImageRule(Rule):
    """
    JSON Pointer can only locate specific array element by index, like \1 or \2,
    which won't allow me to remove image from every element in a containers array.

    To workaround this, I need to use CONTAINERS_JSONPATH to locate to
    containers array, and remove image for each element manually.
    """

    def __init__(
        self,
        kind: str,
        image_value_jsonpath: str,
        containers_jsonpatch_path: str,
        containers_jsonpath: str,
    ) -> None:
        self.KIND = kind
        self.IMAGE_VALUE_JSONPATH = image_value_jsonpath

        # To locate and patch containers array
        self.CONTAINERS_JSONPATCH_PATH = containers_jsonpatch_path
        self.CONTAINERS_JSONPATH = containers_jsonpath
        self._image_jsonpath_expr = jsonpath_ng.parse(self.IMAGE_VALUE_JSONPATH)
        self._container_jsonpath_expr = jsonpath_ng.parse(self.CONTAINERS_JSONPATH)

    def should_apply(self, data: Mapping[str, Any]) -> bool:
        try:
            return data["kind"] == self.KIND and bool(
                list(self._image_jsonpath_expr.find(data))
            )
        except jsonpatch.JsonPatchTestFailed:
            return False

    def apply(self, data: Mapping[str, Any]) -> ApplyResult:
        original_values = [
            match.value for match in self._image_jsonpath_expr.find(data)
        ]
        containers = [
            match.value for match in self._container_jsonpath_expr.find(data)
        ][0]
        for container in containers:
            container.pop("image")

        patch = jsonpatch.JsonPatch(
            [
                {
                    "op": "replace",
                    "path": self.CONTAINERS_JSONPATCH_PATH,
                    "value": containers,
                }
            ]
        )
        patch.apply(data, in_place=True)
        return ApplyResult(
            original_values=original_values, jsonpath=self.IMAGE_VALUE_JSONPATH
        )


class DeploymentImagesRule(ImageRule):
    def __init__(self) -> None:
        kind = "Deployment"
        image_value_jsonpath = "spec.template.spec.containers[*].image"
        containers_jsonpatch_path = "/spec/template/spec/containers"
        containers_jsonpath = "spec.template.spec.containers"
        super().__init__(
            kind=kind,
            image_value_jsonpath=image_value_jsonpath,
            containers_jsonpatch_path=containers_jsonpatch_path,
            containers_jsonpath=containers_jsonpath,
        )


class CronJobImagesRule(ImageRule):
    def __init__(self) -> None:
        kind = "CronJob"
        image_value_jsonpath = "spec.jobTemplate.spec.template.spec.containers[*].image"
        containers_jsonpatch_path = "/spec/jobTemplate/spec/template/spec/containers"
        containers_jsonpath = "spec.jobTemplate.spec.template.spec.containers"
        super().__init__(
            kind=kind,
            image_value_jsonpath=image_value_jsonpath,
            containers_jsonpatch_path=containers_jsonpatch_path,
            containers_jsonpath=containers_jsonpath,
        )


RULES = [
    JsonPatchRemoveRule(
        jsonpatch_path="/metadata/generation", jsonpath="metadata.generation"
    ),
    JsonPatchRemoveRule(
        jsonpatch_path="/spec/template/metadata/annotations/configVersion",
        jsonpath="spec.template.metadata.annotations.configVersion",
    ),
    DeploymentImagesRule(),
    CronJobImagesRule(),
]


def process_file(
    file_path: str, input_stream: IO[Any], output_stream: IO[Any]
) -> List[ApplyResult]:
    """
    Process a file and return applied result for the file
    """
    raw_data = input_stream.read()
    raw_data.strip()

    if not raw_data:
        return []

    try:
        data = yaml.safe_load(raw_data)
        if not data:
            data = json.loads(raw_data)
    except Exception as e:
        raise Exception(
            f"file {file_path} doesn't seem to have valid JSON/YAML: {raw_data}"
        ) from e

    apply_results = [rule.apply(data) for rule in RULES if rule.should_apply(data)]
    yaml.safe_dump(data, output_stream)

    return apply_results


def process_folder(folder: str) -> Dict[str, List[ApplyResult]]:
    """
    Process a folder and return applied changes for each file
    """
    file_apply_results: Dict[str, List[ApplyResult]] = {}

    for dirpath, dirnames, filenames in os.walk(folder):
        for filename in filenames:
            full_path = os.path.join(dirpath, filename)

            with (
                open(full_path) as input_file,
                tempfile.NamedTemporaryFile(mode="w", delete=False) as output_stream,
            ):
                file_apply_results[filename] = process_file(
                    full_path, input_file, output_stream
                )
                shutil.move(output_stream.name, full_path)

    return file_apply_results


def perform_diff(from_dir: str, to_dir: str) -> int:
    kubectl_diff_cmd = os.environ.get(
        "ORIG_KUBECTL_EXTERNAL_DIFF", DEFAULT_DIFF_COMMAND
    )
    cmd = shlex.split(kubectl_diff_cmd)
    cmd += [from_dir, to_dir]

    return os.execvp(cmd[0], cmd)


def warn_user_for_changes(
    from_dir_apply_results: Dict[str, List[ApplyResult]],
    to_dir_apply_results: Dict[str, List[ApplyResult]],
) -> Generator[str, None, None]:
    """
    Tell user if any change is ignored, but they actually have differences
    """

    def _convert_apply_results_to_dict(
        _dir_apply_results: Dict[str, List[ApplyResult]],
    ) -> Dict[str, Dict[str, Any]]:
        # Converting to dict to make later comparisons easier
        # {filename: {jsonpath: original_values}}
        dir_changes: Dict[str, Dict[str, Any]] = {}
        for filename, apply_results in _dir_apply_results.items():
            file_changes: Dict[str, Any] = {
                apply_result.jsonpath: apply_result.original_values
                for apply_result in apply_results
            }
            dir_changes[filename] = file_changes

        return dir_changes

    from_original_values = _convert_apply_results_to_dict(from_dir_apply_results)
    to_original_values = _convert_apply_results_to_dict(to_dir_apply_results)

    for basename, from_file_changes in from_original_values.items():
        to_file_changes = to_original_values.get(basename, {})
        changes_jsonpaths = {
            jsonpath
            for jsonpath, from_value in from_file_changes.items()
            if from_value != to_file_changes.get(jsonpath)
        }
        if changes_jsonpaths:
            yield f"{basename} has ignore {changes_jsonpaths} that will be applied."
