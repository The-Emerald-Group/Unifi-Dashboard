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
            
            res = requests.get(f"{BASE_URL}/ea/devices", headers=headers, timeout=30)
            res.raise_for_status()
            devices_data = res.json().get('data', [])

            wallboard_data = {}
            current_time = datetime.now(timezone.utc)

            for dev in devices_data:
                name = dev.get("name") or dev.get("hostName") or dev.get("mac") or "Unnamed Gateway"
                model = dev.get("productName") or dev.get("hardwareName") or dev.get("hardwareId") or "UniFi Gateway"
                
                if name not in wallboard_data:
                    wallboard_data[name] = {
                        "DeviceName": name, 
                        "Model": model,
                        "Status": "Green", 
                        "IssuesCount": 0, 
                        "IssuesList": []
                    }
                
                state = str(dev.get("status", dev.get("state", "UNKNOWN"))).upper()
                
                # --- OFFLINE LOGIC & TIME TRACKING ---
                if state in ["OFFLINE", "DISCONNECTED", "ADOPTION_FAILED"]:
                    # Look for UniFi's last seen timestamp
                    last_seen_str = dev.get("lastSeenAt") or dev.get("lastReportedAt") or dev.get("lastSeen")
                    time_display = "Currently Offline"
                    issue_label = f"🚨 {state}"
                    severity = "critical"
                    weight = 2
                    
                    if last_seen_str:
                        try:
                            # Parse UniFi's ISO time format
                            clean_ts = last_seen_str[:19]
                            last_seen = datetime.strptime(clean_ts, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                            diff_mins = (current_time - last_seen).total_seconds() / 60
                            
                            # Format the time display
                            if diff_mins > 2880: # Over 48 hours
                                time_display = f"{int(diff_mins / 1440)}d ago"
                            else:
                                h = int(diff_mins // 60)
                                m = int(diff_mins % 60)
                                time_display = f"{h}h {m}m ago" if h > 0 else f"{m}m ago"
                            
                            # Apply Tiered Sorting Weights
                            if diff_mins > 20160: # 14+ Days
                                issue_label = "🕸️ HISTORICAL OFFLINE"
                                severity = "stale"
                                weight = 0.1
                            elif diff_mins > 2880: # 48 Hours to 7 Days
                                issue_label = "👻 LONG TERM OFFLINE"
                                severity = "stale"
                                weight = 0.5
                            else: # Under 48 hours
                                issue_label = "🚨 RECENTLY OFFLINE"
                                severity = "critical"
                                weight = 4 # HIGHEST weight ensures recent outages are #1
                                
                        except Exception:
                            pass # Fallback to defaults if time parsing fails
                            
                    wallboard_data[name]["Status"] = "Red"
                    wallboard_data[name]["IssuesCount"] += weight
                    wallboard_data[name]["IssuesList"].append({
                        "label": issue_label,
                        "time": time_display,
                        "severity": severity
                    })
                    
                # --- WARNING & ISSUE DETECTION LOGIC ---
                elif state in ["UPDATING", "PROVISIONING", "PENDING", "DEGRADED", "NEEDS_ATTENTION", "WARNING"]:
                    if wallboard_data[name]["Status"] != "Red":
                        wallboard_data[name]["Status"] = "Yellow"
                    
                    # Try to extract the exact reason from UniFi
                    specific_issue = dev.get("stateReason") or dev.get("statusReason") or dev.get("issue")
                    
                    if specific_issue:
                        issue_text = f"⚠️ Issue: {specific_issue}"
                    else:
                        issue_text = f"⚠️ {state}"
                        
                    wallboard_data[name]["IssuesCount"] += 1
                    wallboard_data[name]["IssuesList"].append({
                        "label": issue_text,
                        "time": "Active Warning",
                        "severity": "warning"
                    })

            # Sort mathematically by IssuesCount (highest weight first), then alphabetically
            final_output = sorted(wallboard_data.values(), key=lambda x: (-x['IssuesCount'], x['DeviceName']))
            
            payload = {
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "devices": final_output
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
            json.dump({"timestamp": "N/A", "devices": []}, f)
    
    threading.Thread(target=harvest_data, daemon=True).start()
    print("Web Server starting on port 8080...")
    HTTPServer(('0.0.0.0', 8080), MyHandler).serve_forever()
