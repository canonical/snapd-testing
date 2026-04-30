#!/usr/bin/env bash
set -euo pipefail

TEMPLATE_PATH="${CLOUDS_YAML_TEMPLATE_PATH:-}"
OUTPUT_PATH="${CLOUDS_YAML_OUTPUT_PATH:-/etc/openstack/clouds.yaml}"
OPENSTACK_ENV_FILE="${EXPORTER_OPENSTACK_ENV_FILE:-}"
CONFIG_ENV_FILE="${EXPORTER_CONFIG_ENV_FILE:-}"

if [[ -z "$TEMPLATE_PATH" || ! -f "$TEMPLATE_PATH" ]]; then
    echo "clouds.yaml template not found: $TEMPLATE_PATH" >&2
    exit 1
fi

if [[ -z "$OPENSTACK_ENV_FILE" || ! -f "$OPENSTACK_ENV_FILE" ]]; then
    echo "exporter-openstack-env file not found: $OPENSTACK_ENV_FILE" >&2
    exit 1
fi

if [[ -z "$CONFIG_ENV_FILE" || ! -f "$CONFIG_ENV_FILE" ]]; then
    echo "exporter-config-env file not found: $CONFIG_ENV_FILE" >&2
    exit 1
fi

declare -A VARS

read_kv_file() {
    local env_file="$1"
    while IFS='=' read -r key value || [[ -n "${key:-}" ]]; do
        if [[ -z "${key:-}" || "${key:0:1}" == "#" ]]; then
            continue
        fi
        value="${value%$'\r'}"
        VARS["$key"]="$value"
    done < "$env_file"
}

read_kv_file "$OPENSTACK_ENV_FILE"
read_kv_file "$CONFIG_ENV_FILE"

if [[ -z "${VARS[PROJECT]:-}" ]]; then
    echo "PROJECT is missing in exporter-config-env file" >&2
    exit 1
fi

tmp_output="$(mktemp)"
cp "$TEMPLATE_PATH" "$tmp_output"

for key in "${!VARS[@]}"; do
    value="${VARS[$key]}"
    value="${value//\\/\\\\}"
    value="${value//&/\\&}"
    value="${value//|/\\|}"
    sed -i -e "s|<$key>|$value|g" -e "s|#$key#|$value|g" "$tmp_output"
done

mkdir -p "$(dirname "$OUTPUT_PATH")"
install -m 0644 "$tmp_output" "$OUTPUT_PATH"
rm -f "$tmp_output"
