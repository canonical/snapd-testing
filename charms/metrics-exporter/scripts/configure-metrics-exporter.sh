#!/usr/bin/env bash
set -euo pipefail

# prometheus-pushgateway exposes custom metrics on port 9091, which
# grafana-agent can scrape and forward to the remote Grafana backend.

# install the prometheus-pushgateway snap which is used to collect the
# custom metrics generated in the machine in port 9091
if snap list prometheus-pushgateway >/dev/null 2>&1; then
	snap remove --purge prometheus-pushgateway
fi
snap install prometheus-pushgateway


