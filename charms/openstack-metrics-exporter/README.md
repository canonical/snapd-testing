# OpenStack Exporter Installer Charm

This machine charm performs the following workflow:

1. Installs `golang-openstack-exporter` snap from an attached Juju file resource.
2. Runs a bash script to configure `golang-openstack-exporter` snap settings.

## Build

```bash
cd /home/sergio/workspace/others/openstack-metrics-exporter/charm
charmcraft pack
```

## Deploy

Deploy from Charmhub (no local charm file):

```bash
juju deploy openstack-metrics-exporter --channel latest/edge
```

Example with snap resource and OpenStack secret:

```bash
# Get the secret ID first
SECRET_ID=$(juju show-secret openstack-metrics-exporter-credentials --format=json | jq -r '.[] | .id')

juju deploy openstack-metrics-exporter --channel latest/edge \
  --resource exporter-snap=/path/to/golang-openstack-exporter.snap \
  --config exporter-snap-name=golang-openstack-exporter \
  --config secret-id=$SECRET_ID \
  --config project=stg-snapd-spread-amd64
```

Deploy from a local charm artifact:

```bash
juju deploy ./openstack-metrics-exporter_ubuntu-24.04-amd64.charm
```

Example with snap resource and OpenStack secret:

```bash
# Get the secret ID first
SECRET_ID=$(juju show-secret openstack-metrics-exporter-credentials --format=json | jq -r '.[] | .id')

juju deploy ./openstack-metrics-exporter_ubuntu-24.04-amd64.charm \
  --resource exporter-snap=/path/to/golang-openstack-exporter.snap \
  --config exporter-snap-name=golang-openstack-exporter \
  --config secret-id=$SECRET_ID \
  --config project=stg-snapd-spread-amd64 \
  --config http-proxy="http://egress.ps7.internal:3128" \
  --config https-proxy="http://egress.ps7.internal:3128" \
  --config no-proxy="127.0.0.1,127.0.0.53,localhost"
```

If the application is already deployed, attach or update the resource with:

```bash
juju attach-resource openstack-metrics-exporter exporter-snap=/path/to/golang-openstack-exporter.snap
```

If your snap installs under a different snap name than the charm default, set it explicitly:

```bash
juju config openstack-metrics-exporter exporter-snap-name=golang-openstack-exporter
```

Example with proxy settings:

```bash
juju deploy ./openstack-metrics-exporter_ubuntu-24.04-amd64.charm \
  --config http-proxy="http://egress.ps7.internal:3128" \
  --config https-proxy="http://egress.ps7.internal:3128" \
  --config no-proxy="127.0.0.1,127.0.0.53,localhost"
```

## Configure

The charm requires OpenStack credentials to be stored in a Juju secret. The secret ID is used for credentials lookup, and `project` is configured separately as the project identifier for both `clouds.yaml` and the exporter snap. Here's how to set it up:

The charm also supports the following proxy configuration options:

- `http-proxy`: HTTP proxy URL (e.g., `http://proxy.example.com:8080`)
- `https-proxy`: HTTPS proxy URL (e.g., `http://proxy.example.com:8080`)
- `no-proxy`: Comma-separated list of hosts to bypass proxy (e.g., `localhost,127.0.0.1`)

### 1. Create a Juju Secret with OpenStack Credentials

```bash
juju add-secret openstack-metrics-exporter-credentials \
  os-auth-url=https://keystone.stg.snapd.canonical.com:5000/v3 \
  os-project-name=stg-snapd-spread-amd64 \
  os-username=exporter \
  os-password=exporter-password \
  os-user-domain-name=Default \
  os-project-domain-name=Default \
  os-region-name=RegionOne \
  os-tenant-id=4bae056cf1b74f569e341977ee0f5f33
```

**Required fields:**
- `os-auth-url`: Keystone authentication URL
- `os-project-name`: OpenStack project name
- `os-username`: OpenStack username
- `os-password`: OpenStack password
- `os-user-domain-name`: User domain (typically "Default")
- `os-project-domain-name`: Project domain (typically "Default")
- `os-region-name`: OpenStack region
- `os-tenant-id`: OpenStack tenant/project ID

The charm normalizes these Juju-compatible secret keys into the `OS_*` variable names it uses internally.

### 2. Grant Access to the Charm

```bash
juju grant-secret openstack-metrics-exporter-credentials openstack-metrics-exporter
```

### 3. Configure the Charm with the Secret ID and Project

Juju user secrets can only be accessed by ID from an observer charm, not by name. Get the ID first:

```bash
SECRET_ID=$(juju show-secret openstack-metrics-exporter-credentials --format=json | jq -r '.[] | .id')

juju config openstack-metrics-exporter secret-id=$SECRET_ID
juju config openstack-metrics-exporter project=stg-snapd-spread-amd64
juju config openstack-metrics-exporter exporter-snap-name=golang-openstack-exporter
```

### 4. (Optional) Rotate Credentials

To update the credentials, update the secret:

```bash
juju update-secret openstack-metrics-exporter-credentials \
  os-password=new-password
```

The charm will automatically reconcile when the secret changes.

## Reconcile

To re-run the full workflow:

```bash
juju run openstack-metrics-exporter/0 reconcile
```
