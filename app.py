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

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def harvest_data():
    if not API_KEY:
        log("!! ERROR: UNIFI_API_KEY environment variable is missing!")
        return

    # Use the X-API-KEY header as per documentation
    headers = {
        "X-API-KEY": API_KEY,
        "Accept": "application/json"
    }

    while True:
        try:
            log(">>> Starting UniFi v1 Stable Harvest...")
            
            # 1. Get Sites (for friendly Names and Health Status)
            sites_res = requests.get(f"{BASE_URL}/sites", headers=headers, timeout=30)
            sites_res.raise_for_status()
            sites_list = sites_res.json().get('data', [])

            # 2. Get Devices (to match site IDs to hardware models)
            dev_res = requests.get(f"{BASE_URL}/devices", headers=headers, timeout=30)
            dev_res.raise_for_status()
            devices_list = dev_res.json().get('data', [])

            # Create a lookup for Site ID -> Hardware Model
            site_models = {d.get('siteId'): d.get('productName') for d in devices_list if d.get('siteId')}

            final_output = []
            current_time = datetime.now(timezone.utc)

            for site in sites_list:
                site_id = site.get('id')
                name = site.get('name') or "Unnamed Site"
                model = site_models.get(site_id) or "UniFi Gateway"
                
                # Default status
                status = "Green"
                weight = 0
                issues = []
                
                # --- CONNECTIVITY & TIMING ---
                # Use reportedAt for accurate 'Last Seen' check
                last_seen_str = site.get('reportedAt')
                time_display = ""
                
                if last_seen_str:
                    try:
                        # Parse RFC3339 format
                        clean_ts = last_seen_str.replace("Z", "+00:00")
                        last_seen = datetime.fromisoformat(clean_ts)
                        diff_mins = (current_time - last_seen).total_seconds() / 60
                        
                        if diff_mins > 2880: 
                            time_display = f"{int(diff_mins / 1440)}d ago"
                        else:
                            h, m = divmod(int(diff_mins), 60)
                            time_display = f"{h}h {m}m ago" if h > 0 else f"{m}m ago"
                        
                        # If no check-in for 12 mins, force Red
                        if diff_mins > 12:
                            status = "Red"
                            weight = 10 if diff_mins < 1440 else 5 # Recent outages get highest weight
                            issues.append({"label": "🚨 OFFLINE", "time": time_display, "severity": "critical"})
                    except: pass

                # --- ALERT DETECTION (Yellow) ---
                # Check the statistics object provided in v1/sites
                alerts = site.get('statistics', {}).get('alerts', 0)
                if alerts > 0 and status != "Red":
                    status = "Yellow"
                    weight = 2
                    issues.append({"label": f"⚠️ {alerts} Active Issues", "time": "Check Site Manager", "severity": "warning"})

                final_output.append({
                    "DeviceName": name,
                    "Model": model,
                    "Status": status,
                    "IssuesCount": weight,
                    "IssuesList": issues
                })

            # Sort: Priority weight (highest first) then Alphabetical
            final_output.sort(key=lambda x: (-x['IssuesCount'], x['DeviceName']))
            
            payload = {
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "devices": final_output
            }
            
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4)
            log(f"*** HARVEST SUCCESS: {len(final_output)} sites processed ***")
            
        except Exception as e:
            log(f"!! ERROR: {str(e)}")
            traceback.print_exc()
        
        time.sleep(POLL_INTERVAL)

# Standard N-able style web server
class MyHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args): pass 
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Access-Control-Allow-Origin', '*')
        SimpleHTTPRequestHandler.end_headers(self)

if __name__ == "__main__":
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w") as f: json.dump({"timestamp": "N/A", "devices": []}, f)
    threading.Thread(target=harvest_data, daemon=True).start()
    HTTPServer(('0.0.0.0', 8080), MyHandler).serve_forever()
