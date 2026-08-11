#!/bin/sh
set -eu

source_repo=${1:-/Volumes/ExternalSSD/projects_8/alpine-llm-gateway}
project_root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
manifest_file="$project_root/docs/reference-runtime-files.tsv"
tab=$(printf '\t')

if [ ! -f "$manifest_file" ]; then
    printf '%s\n' "runtime manifest is unavailable: $manifest_file" >&2
    exit 1
fi

tail -n +2 "$manifest_file" | while IFS="$tab" read -r source_path destination_path source_sha destination_sha import_status
do
    actual_source_sha=$(shasum -a 256 "$source_repo/$source_path" | awk '{print $1}')
    actual_destination_sha=$(shasum -a 256 "$project_root/$destination_path" | awk '{print $1}')

    if [ "$actual_source_sha" != "$source_sha" ]; then
        printf '%s\n' "reference source hash mismatch: $source_path" >&2
        exit 1
    fi
    if [ "$actual_destination_sha" != "$destination_sha" ]; then
        printf '%s\n' "destination hash mismatch: $destination_path" >&2
        exit 1
    fi
done

printf '%s\n' "runtime reference manifest: PASS"
