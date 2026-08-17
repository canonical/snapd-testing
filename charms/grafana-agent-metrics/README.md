# Grafana Agent Metrics Charm

This machine charm performs the following workflow:

1. Installs `grafana-agent` snap from an attached Juju file resource.
2. Applies grafana-agent config and starts/restarts the service.

## Build

```bash
cd ./charms/grafana-agent
charmcraft pack
```

## Deploy

```bash
juju deploy ./grafana-agent-metrics_ubuntu-24.04-amd64.charm
```

Example with all parameters:

```bash
juju deploy ./grafana-agent-metrics_ubuntu-24.04-amd64.charm \
	--resource grafana-agent-snap=/path/to/grafana-agent.snap \
	--config grafana-agent-project=snapd \
	--config grafana-agent-name=my-agent \
	--config grafana-agent-endpoint=http://<domain>/<prometheus>/api/v1/write \
	--config grafana-agent-target=127.0.0.1 \
	--config grafana-agent-port=9180 \
	--config grafana-agent-scrape-interval=1m \
	--config grafana-agent-scrape-timeout=10s
```

If the application is already deployed, attach or update the resource with:

```bash
juju attach-resource grafana-agent-metrics grafana-agent-snap=/path/to/grafana-agent.snap
```

## Configure

Example:

```bash
juju config grafana-agent-metrics grafana-agent-project=my-project
juju config grafana-agent-metrics grafana-agent-name=my-agent
juju config grafana-agent-metrics grafana-agent-endpoint=https://example/api/v1/write
```

To re-run the full workflow:

```bash
juju run grafana-agent-metrics/0 reconcile
```
