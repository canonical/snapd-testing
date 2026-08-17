# Metrics Exporter Charm

This machine charm performs the following workflow:

1. Installs `prometheus-pushgateway` snap.

## Build

```bash
cd ./charms/metrics-exporter
charmcraft pack
```

## Deploy

```bash
juju deploy ./custom-metrics-exporter_ubuntu-24.04-amd64.charm \
	--resource exporter-snap=/path/to/prometheus-pushgateway.snap
```

If the application is already deployed, attach or update the resource with:

```bash
juju attach-resource custom-metrics-exporter exporter-snap=/path/to/prometheus-pushgateway.snap
```

## Configure

To re-run the full workflow:

```bash
juju run custom-metrics-exporter/0 reconcile
```
