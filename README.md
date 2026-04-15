# 🔵 UniFi Gateway Monitor

A lightweight, self-hosted wallboard that polls the **UniFi Site Manager API** (`api.ui.com`) and displays real-time device health across all your sites. Built with Python and plain HTML — no external dependencies beyond Docker.

---

## How It Works

A background Python thread authenticates with the UniFi API every **5 minutes**, fetches all devices associated with your Cloud Gateways, and flags any device that is offline, disconnected, or updating. 

Results are grouped by Site Name and sorted by severity weight, ensuring your most critical offline sites always appear at the top of the wallboard.

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/) installed
- A UniFi Cloud account (unifi.ui.com) with Admin access
- A **UniFi API Key** (generated from the Site Manager settings)

---

## Quick Start

### 1. Download docker-compose.yml

Save the `docker-compose.yml` file to a folder on your host. 

### 2. Set your API token

Open `docker-compose.yml` and replace the placeholder with your UniFi API Key:

```yaml
environment:
  - UNIFI_API_KEY=your_api_key_here
```

> ⚠️ **Never commit your API key to source control.**

### 3. Run

```bash
docker compose up -d --build
```

### 4. Open the wallboard

Navigate to [http://localhost:8081](http://localhost:8081) in your browser.

The wallboard auto-refreshes every **30 seconds**. The backend harvests fresh data every **5 minutes**.

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `UNIFI_API_KEY` | *(required)* | Your read-only UniFi Site Manager API Key |
| `PYTHONUNBUFFERED` | `1` | Ensures Python logs appear in real-time in Docker |
| `DISPLAY_TIMEZONE` | `TZ` or system local | Timezone used for "Last Harvest" display (DST-aware, e.g. `Europe/London`) |

The polling interval defaults to **5 minutes** and can be changed in `app.py` by modifying the `POLL_INTERVAL` variable before building the image.

---

## Severity Levels

Devices are classified into tiers based on their native UniFi state:

| Colour | Severity | UniFi State | Label Example |
|---|---|---|---|
| 🔴 Red | `critical` | `OFFLINE`, `DISCONNECTED`, `ADOPTION_FAILED` | 🚨 OFFLINE |
| 🟡 Yellow | `warning` | `UPDATING`, `PROVISIONING`, `PENDING` | ⏳ UPDATING |
| 🟢 Green | — | `ONLINE` | ✓ All systems active |

Sites are sorted by a weighted issue score so the most actionable problems appear at the top of the wallboard.

---

## Stopping the Monitor

```bash
docker compose down
```

---

## Troubleshooting

**Wallboard shows "Awaiting Data..." or "No data or check API Key..."** The first harvest runs immediately on startup. Check the container logs to see if the API call succeeded:
```bash
docker logs unifi-monitor
```

**HTTP 401 Unauthorized in logs** Your API key is missing, invalid, or expired. Generate a new one in the UniFi Site Manager, update your `docker-compose.yml`, and restart the container.

**GitHub Actions build failing** Check that both `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` secrets are set correctly in your repository settings.

---

## Project Structure

| File | Purpose |
|---|---|
| `app.py` | Data harvester + UniFi API integration + embedded HTTP server |
| `index.html` | Wallboard frontend (auto-refreshes every 30 seconds) |
| `Dockerfile` | Container image definition (Python 3.9 slim) |
| `docker-compose.yml` | Service orchestration |
| `.github/workflows/docker-build.yml` | GitHub Actions CI/CD pipeline |
