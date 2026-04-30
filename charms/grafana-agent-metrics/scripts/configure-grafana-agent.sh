#!/usr/bin/env bash
set -euo pipefail

SNAP_NAME="grafana-agent"
CHANNEL="${GRAFANA_AGENT_CHANNEL:-stable}"
CONFIG_TEMPLATE="${GRAFANA_AGENT_CONFIG_TEMPLATE:-}"
PROJECT="${GRAFANA_AGENT_PROJECT:-}"
AGENT="${GRAFANA_AGENT_AGENT:-}"
ENDPOINT="${GRAFANA_AGENT_ENDPOINT:-}"
TARGET="${GRAFANA_AGENT_TARGET:-}"
PORT="${GRAFANA_AGENT_PORT:-}"
SCRAPE_INTERVAL="${GRAFANA_AGENT_SCRAPE_INTERVAL:-1m}"
SCRAPE_TIMEOUT="${GRAFANA_AGENT_SCRAPE_TIMEOUT:-10s}"
CFG_PATH="/etc/grafana-agent.yaml"

if snap list "$SNAP_NAME" >/dev/null 2>&1; then
    snap refresh "$SNAP_NAME" --channel "$CHANNEL"
else
    snap install "$SNAP_NAME" --channel "$CHANNEL"
fi

if [[ -n "$CONFIG_TEMPLATE" ]]; then
    if [[ ! -f "$CONFIG_TEMPLATE" ]]; then
        echo "grafana-agent template not found: $CONFIG_TEMPLATE" >&2
        exit 1
    fi

    if [[ -z "$PROJECT" || -z "$AGENT" || -z "$ENDPOINT" || -z "$TARGET" || -z "$PORT" ]]; then
        echo "Template rendering requires GRAFANA_AGENT_PROJECT, GRAFANA_AGENT_AGENT, GRAFANA_AGENT_ENDPOINT, GRAFANA_AGENT_TARGET, and GRAFANA_AGENT_PORT" >&2
        exit 1
    fi

    tmp_cfg="$(mktemp)"
    cp "$CONFIG_TEMPLATE" "$tmp_cfg"

    sed "s/#PROJECT#/${PROJECT}/g" -i "$tmp_cfg"
    sed "s/#AGENT#/${AGENT}/g" -i "$tmp_cfg"
    sed "s@#ENDPOINT#@${ENDPOINT}@g" -i "$tmp_cfg"
    sed "s/#TARGET#/${TARGET}/g" -i "$tmp_cfg"
    sed "s/#PORT#/${PORT}/g" -i "$tmp_cfg"
    sed "s/#SCRAPE_INTERVAL#/${SCRAPE_INTERVAL}/g" -i "$tmp_cfg"
    sed "s/#SCRAPE_TIMEOUT#/${SCRAPE_TIMEOUT}/g" -i "$tmp_cfg"

    cp "$tmp_cfg" "$CFG_PATH"
    rm -f "$tmp_cfg"
fi

snap start --enable "$SNAP_NAME"."$SNAP_NAME"
snap set "$SNAP_NAME" reporting-enabled=0
