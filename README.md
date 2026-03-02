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
