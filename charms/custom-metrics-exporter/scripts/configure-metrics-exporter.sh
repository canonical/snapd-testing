#!/usr/bin/env bash
set -euo pipefail

# prometheus-pushgateway exposes custom metrics on port 9091, which
# grafana-agent can scrape and forward to the remote Grafana backend.

# validate snap is present (it is installed from a Juju resource by the charm)
if ! snap list prometheus-pushgateway >/dev/null 2>&1; then
	echo "Snap 'prometheus-pushgateway' is not installed" >&2
	exit 1
fi

snap start prometheus-pushgateway --enable


