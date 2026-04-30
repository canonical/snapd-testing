#!/usr/bin/env bash
set -euo pipefail

SNAP_NAME="${EXPORTER_SNAP_NAME:-golang-openstack-metrics-exporter}"

if ! snap list "$SNAP_NAME" >/dev/null 2>&1; then
    echo "Snap '$SNAP_NAME' is not installed" >&2
    exit 1
fi

if [[ -z "${PROJECT:-}" ]]; then
    echo "PROJECT is required" >&2
    exit 1
fi

if [[ -z "${OS_TENANT_ID:-}" ]]; then
    echo "OS_TENANT_ID is required" >&2
    exit 1
fi

snap connect "$SNAP_NAME":etc-openstack
snap start "$SNAP_NAME".service --enable

# Apply exporter runtime options through snap configuration.
snap set "$SNAP_NAME" cloud="$PROJECT"
snap set "$SNAP_NAME" disable-service.image=true
snap set "$SNAP_NAME" log.level=warn
snap set "$SNAP_NAME" domain-id=default
snap set "$SNAP_NAME" cache=true

snap set "$SNAP_NAME" disable-deprecated-metrics=true
snap set "$SNAP_NAME" disable-service.placement=true
snap set "$SNAP_NAME" disable-service.orchestration=true
snap set "$SNAP_NAME" disable-service.load-balancer=true
snap set "$SNAP_NAME" disable-service.identity=true
snap set "$SNAP_NAME" disable-service.gnocchi=true
snap set "$SNAP_NAME" disable-service.dns=true
snap set "$SNAP_NAME" disable-service.database=true
snap set "$SNAP_NAME" disable-service.container-infra=true
snap set "$SNAP_NAME" disable-service.baremetal=true
snap set "$SNAP_NAME" disable-service.network=true

snap set "$SNAP_NAME" tenant-id="${OS_TENANT_ID}"
snap restart "$SNAP_NAME"
