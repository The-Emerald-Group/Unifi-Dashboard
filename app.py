import os
import requests
import json
import time
import threading
import traceback
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, HTTPServer

# --- CONFIGURATION ---
API_KEY = os.environ.get("UNIFI_API_KEY")
BASE_URL = "https://api.ui.com/v1"
DATA_FILE = "data.json"
POLL_INTERVAL = 300 
ALERT_WINDOW_MINS = 240  # Filter out anything older than 4 hours

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def harvest_data():
    if not API_KEY:
        log("!! ERROR: UNIFI_API_KEY is missing!")
        return

    headers = {"X-API-KEY": API_KEY, "Accept": "application/json"}

    while True:
        try:
            log(">>> Starting Final Unified UniFi Harvest...")
            
            # 1. Fetch Master Device List (Friendly Names + Inventory)
            dev_res = requests.get(f"{BASE_URL}/devices", headers=headers, timeout=30)
            dev_res.raise_for_status()
            devices_raw = dev_res.json().get('data', [])

            # 2. Fetch Health Statistics (Offline counts + ISP Issues)
            sites_res = requests.get(f"{BASE_URL}/sites", headers=headers, timeout=30)
            sites_res.raise_for_status()
            sites_raw = sites_res.json().get('data', [])

            # Map site health by hostId
            site_health_map = {s.get('hostId'): s for s in sites_raw if s.get('hostId')}

            final_cards = []
            current_time = datetime.now(timezone.utc)
            # Calculate current 5-minute bucket index
            current_bucket = int(current_time.timestamp() / 300)

            for host_group in devices_raw:
                host_id = host_group.get('hostId')
                # Grab hostName directly from your provided JSON structure
                name = host_group.get('hostName') or "Unnamed Site"
                devices_list = host_group.get('devices', [])
                
                stats = site_health_map.get(host_id, {}).get('statistics', {})
                counts = stats.get('counts', {})
                
                status = "Green"
                weight = 0
                issues = []
                inventory = []

                for d in devices_list:
                    dev_status = str(d.get('status', 'unknown')).lower()
                    inventory.append({
                        "name": d.get("name") or d.get("mac"),
                        "model": d.get("model") or "UniFi Device",
                        "status": dev_status.upper()
                    })

                    # 🔴 RED: Main Gateway/Console is Offline
                    if d.get('isConsole') and dev_status != "online":
                        status = "Red"
                        weight = 20
                        issues.append({"label": "🚨 GATEWAY OFFLINE", "time": "Critical", "severity": "critical"})

                # 🟡 WARNING: Check for sub-device outages or ISP issues within 4 hours
                if status != "Red":
                    # Check Internet Issues bucket index
                    internet_issues = stats.get('internetIssues', [])
                    active_isp = False
                    for iss in internet_issues:
                        idx = iss.get('index', 0)
                        # If index is within the last 4 hours (48 buckets)
                        if (current_bucket - idx) <= 48:
                            active_isp = True
                            break

                    offline_count = counts.get('offlineDevice', 0)
                    if offline_count > 0:
                        status = "Yellow"; weight = 10
                        issues.append({"label": f"⚠️ {offline_count} Device(s) Offline", "time": "Partial", "severity": "warning"})
                    
                    if active_isp:
                        status = "Yellow"; weight = 5
                        issues.append({"label": "📡 RECENT ISP ISSUE", "time": "< 4h ago", "severity": "warning"})

                final_cards.append({
                    "SiteName": name,
                    "Inventory": inventory,
                    "Status": status,
                    "IssuesCount": weight,
                    "IssuesList": issues
                })

            final_cards.sort(key=lambda x: (-x['IssuesCount'], x['SiteName']))
            payload = {"timestamp": datetime.now().strftime("%H:%M:%S"), "sites": final_cards}
            with open(DATA_FILE, "w", encoding="utf-8") as f: json.dump(payload, f, indent=4)
            log(f"*** HARVEST SUCCESS: Processed {len(final_cards)} sites ***")
            
        except Exception as e:
            log(f"!! ERROR: {str(e)}")
            traceback.print_exc()
        
        time.sleep(POLL_INTERVAL)

class MyHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args): pass 
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Access-Control-Allow-Origin', '*')
        SimpleHTTPRequestHandler.end_headers(self)

if __name__ == "__main__":
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w") as f: json.dump({"timestamp": "N/A", "sites": []}, f)
    threading.Thread(target=harvest_data, daemon=True).start()
    HTTPServer(('0.0.0.0', 8080), MyHandler).serve_forever()
