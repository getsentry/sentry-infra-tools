from pathlib import Path

from config_builder.merger.libsonnet import LibsonnetMerger

EXPECTED = """// This is an auto generated file. Do not update by hand

{
  _123: import '../_123.libsonnet',
  ggg: import '../ggg.libsonnet',
  zzz: import '../zzz.libsonnet',
}
"""


def test_order() -> None:
    # Test the output is generated in the right order

    merger = LibsonnetMerger()
    merger.add_file(Path("zzz.libsonnet"))
    merger.add_file(Path("ggg.libsonnet"))
    merger.add_file(Path("_123.libsonnet"))
    assert merger.serialize_content() == EXPECTED
