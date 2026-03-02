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
BASE_URL = "https://api.ui.com"
DATA_FILE = "data.json"
POLL_INTERVAL = 300  # 5 minutes

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def harvest_data():
    if not API_KEY:
        log("!! ERROR: UNIFI_API_KEY environment variable is missing!")
        return

    headers = {
        "X-API-KEY": API_KEY,
        "Accept": "application/json"
    }

    while True:
        try:
            log(">>> Starting UniFi API Harvest...")
            
            # Fetch devices across all sites
            # Note: We use the /ea/devices endpoint as it usually aggregates status well.
            res = requests.get(f"{BASE_URL}/ea/devices", headers=headers, timeout=30)
            res.raise_for_status()
            devices_data = res.json().get('data', [])

            wallboard_data = {}

            for dev in devices_data:
                # Group by site name (fallback to 'Unknown Site' if missing)
                site_name = dev.get("site", {}).get("name") or dev.get("siteName") or "Unknown Site"
                
                if site_name not in wallboard_data:
                    wallboard_data[site_name] = {
                        "SiteName": site_name, 
                        "Status": "Green", 
                        "TotalDevices": 0, 
                        "IssuesCount": 0, 
                        "IssuesList": []
                    }
                
                wallboard_data[site_name]["TotalDevices"] += 1
                
                # Determine device state
                # UniFi often uses 'status' or 'state' fields. 
                state = str(dev.get("status", dev.get("state", "UNKNOWN"))).upper()
                name = dev.get("name", dev.get("mac", "Unnamed Device"))
                
                # Define logic for Red/Yellow/Green based on UniFi's native states
                if state in ["OFFLINE", "DISCONNECTED", "ADOPTION_FAILED"]:
                    wallboard_data[site_name]["Status"] = "Red"
                    wallboard_data[site_name]["IssuesCount"] += 2
                    wallboard_data[site_name]["IssuesList"].append({
                        "name": name,
                        "time": "Currently Offline", # UniFi doesn't always provide an exact 'last seen' in the main list
                        "label": f"🚨 {state}",
                        "severity": "critical"
                    })
                elif state in ["UPDATING", "PROVISIONING", "PENDING"]:
                    # If it's already Red, don't downgrade the whole site card to Yellow
                    if wallboard_data[site_name]["Status"] != "Red":
                        wallboard_data[site_name]["Status"] = "Yellow"
                    
                    wallboard_data[site_name]["IssuesCount"] += 1
                    wallboard_data[site_name]["IssuesList"].append({
                        "name": name,
                        "time": "In Progress",
                        "label": f"⏳ {state}",
                        "severity": "warning"
                    })

            # Sort: Sites with the most issues at the top, then alphabetically
            final_output = sorted(wallboard_data.values(), key=lambda x: (-x['IssuesCount'], x['SiteName']))
            
            payload = {
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "sites": final_output
            }
            
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4)
            log("*** HARVEST SUCCESS ***")
            
        except Exception as e:
            log(f"!! ERROR: {str(e)}")
            log(traceback.format_exc())
        
        time.sleep(POLL_INTERVAL)

class MyHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args): 
        pass 
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Access-Control-Allow-Origin', '*')
        SimpleHTTPRequestHandler.end_headers(self)

if __name__ == "__main__":
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w") as f: 
            json.dump({"timestamp": "N/A", "sites": []}, f)
    
    threading.Thread(target=harvest_data, daemon=True).start()
    print("Web Server starting on port 8080...")
    HTTPServer(('0.0.0.0', 8080), MyHandler).serve_forever()
