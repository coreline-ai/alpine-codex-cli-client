#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
source_repo=${1:-${ALPINE_REFERENCE_REPO:-$project_root/../../project_202607/alpine-llm-gateway}}
source_commit=${2:-${ALPINE_REFERENCE_COMMIT:-b81a7d8ee12af72ff95180bfeadabe68e5be950e}}
manifest_file="$project_root/docs/reference-runtime-files.tsv"
tab=$(printf '\t')

if ! git -C "$source_repo" cat-file -e "$source_commit^{commit}" 2>/dev/null; then
    printf '%s\n' "immutable reference commit is unavailable: $source_commit" >&2
    exit 1
fi

if [ ! -f "$manifest_file" ]; then
    printf '%s\n' "runtime manifest is unavailable: $manifest_file" >&2
    exit 1
fi

tail -n +2 "$manifest_file" | while IFS="$tab" read -r source_path destination_path source_sha destination_sha import_status
do
    actual_source_sha=$(git -C "$source_repo" show "$source_commit:$source_path" | shasum -a 256 | awk '{print $1}')
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
