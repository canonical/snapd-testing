# Spread Enterprise Store Charm

This is a minimal machine charm. It only installs the required snaps and opens network ports.

To properly set up the Enterprise Store, use the official documentation: https://ubuntu.com/enterprise-store/docs/

This machine charm performs the following workflow:

1. Ensures `enterprise-store` snap is installed.
2. Ensures `postgresql` snap is installed.
3. Opens TCP ports `80` (HTTP) and `443` (HTTPS).

## Build

```bash
cd ./charms/spread-enterprise-store
charmcraft pack
```

## Deploy

```bash
juju deploy ./spread-enterprise-store_ubuntu-24.04-amd64.charm
```

## Reconcile

To re-run the full workflow:

```bash
juju run spread-enterprise-store/0 reconcile
```

## Configuration

The charm supports the following configuration options for proxy settings:

- `http-proxy`: HTTP proxy URL (e.g., `http://proxy.example.com:8080`)
- `https-proxy`: HTTPS proxy URL (e.g., `http://proxy.example.com:8080`)
- `no-proxy`: Comma-separated list of hosts to bypass proxy (e.g., `localhost,127.0.0.1`)

### Example

To deploy with proxy settings:

```bash
juju deploy ./spread-enterprise-store_ubuntu-24.04-amd64.charm \
  --config http-proxy="http://egress.ps7.internal:3128" \
  --config https-proxy="http://egress.ps7.internal:3128" \
  --config no-proxy="127.0.0.1,127.0.0.53,localhost"
```

Or update existing deployment:

```bash
juju config spread-enterprise-store http-proxy="http://egress.ps7.internal:3128"
```
