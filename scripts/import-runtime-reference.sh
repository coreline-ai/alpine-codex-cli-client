#!/bin/sh
set -eu

source_repo=${1:-/Volumes/ExternalSSD/projects_8/alpine-llm-gateway}
project_root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
manifest_file="$project_root/docs/reference-runtime-files.tsv"

if [ ! -d "$source_repo/.git" ]; then
    printf '%s\n' "reference repository is unavailable: $source_repo" >&2
    exit 1
fi

printf '%s\n' "source_path	destination_path	source_sha256	destination_sha256	status" > "$manifest_file"

for module_name in \
    alpine-runtime-api \
    alpine-runtime-android \
    alpine-runtime-host \
    alpine-runtime-background-android \
    alpine-runtime-ui-compose \
    alpine-runtime-pack-bundled \
    alpine-workspace-api \
    alpine-workspace-android
do
    find "$source_repo/$module_name" \
        \( -path "*/build" -o -path "*/.gradle" -o -path "*/.cxx" \) -prune -o \
        -type f -print | sort | while IFS= read -r source_file
    do
        relative_path=${source_file#"$source_repo"/}
        destination_file="$project_root/$relative_path"
        source_sha=$(shasum -a 256 "$source_file" | awk '{print $1}')

        if [ -e "$destination_file" ]; then
            destination_sha=$(shasum -a 256 "$destination_file" | awk '{print $1}')
            if [ "$source_sha" != "$destination_sha" ]; then
                printf '%s\n' "destination differs from reference: $relative_path" >&2
                exit 1
            fi
            status=verified_existing
        else
            mkdir -p "$(dirname "$destination_file")"
            cp -p "$source_file" "$destination_file"
            destination_sha=$(shasum -a 256 "$destination_file" | awk '{print $1}')
            status=imported
        fi

        printf '%s\t%s\t%s\t%s\t%s\n' \
            "$relative_path" "$relative_path" "$source_sha" "$destination_sha" "$status" \
            >> "$manifest_file"
    done
done

printf '%s\n' "runtime reference import: PASS"
