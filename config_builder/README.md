# Materialization and code generation scripts

This contains a number of scripts we use to materialize the jsonnet
configurations.

There are two types of scripts: combiners and materializers.

## Combiners

Combiners take care of the generation of these unified files and apply the
overrides.

Each directory we want to combine mush have a `_config_generator.json` file
that instructs whether the files in such directory are overriding files from
another directory. Example: the regional topic files override the default
topics files that override the sentry-kafka-schemas files.

## Materializer

This script simply scans the entire config directory structure and manifest
each jsonnet files putting the result in the `_materialized_configs` directory
following the same directory structure as the source files.
