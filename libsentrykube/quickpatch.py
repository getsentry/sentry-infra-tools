from typing import Sequence, Mapping


def get_arguments(service: str, patch: str) -> Sequence[str]:
    raise NotImplementedError


def apply_patch(
    service: str, resource: str, patch: str, arguments: Mapping[str, str]
) -> None:
    """
    Finds the patch file, the resource and applies the patch
    TODO: Add parameters
    """
    raise NotImplementedError
