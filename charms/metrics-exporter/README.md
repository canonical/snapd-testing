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
juju deploy ./metrics-exporter_ubuntu-24.04-amd64.charm
```

## Configure

To re-run the full workflow:

```bash
juju run metrics-exporter/0 reconcile
```
