import os
import requests
import json
import time
import sys
import threading
import traceback
import urllib3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, HTTPServer

# Suppress insecure HTTPS warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- CONFIGURATION ---
API_KEY = os.environ.get("UNIFI_API_KEY")
MODERN_URL = "https://api.ui.com/v1"
CLASSIC_URL = os.environ.get("CLASSIC_URL")
CLASSIC_USER = os.environ.get("CLASSIC_USER")
CLASSIC_PASS = os.environ.get("CLASSIC_PASS")

# --- EXCLUSIONS ---
IGNORE_SITES_RAW = os.environ.get("IGNORE_SITES", "")
IGNORE_SITES = [s.strip().lower() for s in IGNORE_SITES_RAW.split(",") if s.strip()]

# --- SMTP EMAIL ---
SMTP_SERVER = os.environ.get("SMTP_SERVER")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 25))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
EMAIL_FROM = os.environ.get("EMAIL_FROM")
EMAIL_TO = os.environ.get("EMAIL_TO")
ALERT_THRESHOLD_SECONDS = 8 * 3600 

# --- DATA STORAGE ---
DATA_FILE = "data.json"
TEMP_DATA_FILE = "data.tmp.json" 
DATA_DIR = "/unifi_data"
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = f"{DATA_DIR}/alerts_v2.json" 
TEMP_STATE_FILE = f"{DATA_DIR}/alerts_v2.tmp.json"

POLL_INTERVAL = 300 
HISTORICAL_OFFLINE_SECONDS = 2592000 # 30 Days

# --- THRESHOLDS ---
GW_GRACE_PERIOD_SECONDS = 600      # 10 Mins: Buffer for console visual bugs
DEVICE_GRACE_PERIOD_SECONDS = 180  # 3 Mins: Fast trigger for Switch/AP hardware

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def send_consolidated_offline_alert(site_name, devices):
    count = len(devices)
    s_plural = "s" if count > 1 else ""
    is_are = "are" if count > 1 else "is"
    subject = f"🚨 URGENT: {count} UniFi Device{s_plural} Offline - {site_name}"
    table_rows = "".join([f"<tr><td style='padding:10px;border-bottom:1px solid #eee;'><b>{d['name']}</b></td><td style='padding:10px;border-bottom:1px solid #eee;'>{d['model']}</td><td style='padding:10px;border-bottom:1px solid #eee;color:#ff3b30;'>{d['duration']}</td></tr>" for d in devices])
    html_body = f"<html><body style='font-family:sans-serif;'><h2>🚨 Device Offline Alert</h2><p><b>{count} device{s_plural}</b> at <b>{site_name}</b> {is_are} unreachable.</p><table style='width:100%;text-align:left;'>{table_rows}</table></body></html>"
    return send_email(subject, html_body, site_name)

def send_consolidated_recovery_alert(site_name, devices):
    count = len(devices)
    subject = f"✅ RECOVERED: {count} UniFi Device(s) Online - {site_name}"
    table_rows = "".join([f"<tr><td style='padding:10px;'>{d['name']}</td><td>ONLINE</td></tr>" for d in devices])
    html_body = f"<html><body><h2>✅ Devices Recovered</h2><p>Devices at <b>{site_name}</b> are back online.</p><table>{table_rows}</table></body></html>"
    return send_email(subject, html_body, site_name)

def send_email(subject, html_body, log_identifier):
    if not SMTP_SERVER or not EMAIL_TO: return False
    msg = MIMEMultipart('alternative'); msg['From'] = EMAIL_FROM; msg['To'] = EMAIL_TO; msg['Subject'] = subject
    msg.attach(MIMEText(html_body, 'html'))
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10)
        if SMTP_USER and SMTP_PASS: server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg); server.quit()
        log(f"*** EMAIL SENT: {subject} ***"); return True
    except Exception as e: log(f"!! Email failed: {str(e)}"); return False

def parse_iso_time(ts_str):
    try:
        if not ts_str: return None
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except: return None

def fetch_modern_unifi(alert_state, pending_offline, pending_recovery):
    if not API_KEY: return []
    cards = []
    try:
        headers = {"X-API-KEY": API_KEY, "Accept": "application/json"}
        dev_res = requests.get(f"{MODERN_URL}/devices", headers=headers, timeout=30).json().get('data', [])
        sites_res = requests.get(f"{MODERN_URL}/sites", headers=headers, timeout=30).json().get('data', [])
        hosts_res = requests.get(f"{MODERN_URL}/hosts", headers=headers, timeout=30).json().get('data', [])
        
        current_time = datetime.now(timezone.utc)
        current_bucket = int(current_time.timestamp() / 300)
        host_map = {h.get('id'): h for h in hosts_res}

        for host_group in dev_res:
            host_id = host_group.get('hostId')
            name = host_group.get('hostName') or "Unnamed Site"
            if name.lower() in IGNORE_SITES: continue
                
            devices_list = host_group.get('devices', [])
            site_data = next((s for s in sites_res if s.get('hostId') == host_id), {})
            host_data = host_map.get(host_id, {})
            
            # Application Heartbeat: deviceStateLastChanged (Unix timestamp)
            reported = host_data.get('reportedState', {})
            app_reboot_ts = reported.get('deviceStateLastChanged', 0)
            app_was_just_restarted = (current_time.timestamp() - app_reboot_ts) < 600 # 10 min window

            status, weight, issues, inventory = "Green", 0, [], []
            primary_model = None
            recent_devs, historical_devs = 0, 0

            for d in devices_list:
                dev_status = str(d.get('status', 'unknown')).lower()
                dev_mac = d.get('mac')
                dev_name = d.get("name") or dev_mac
                dev_model = d.get("model") or "UniFi"
                alert_key = dev_mac or f"{name}_{dev_name}"
                is_gw = d.get('isConsole') or d.get('type', '').lower() in ['ugw', 'uxg', 'gateway']
                if is_gw: primary_model = dev_model

                offline_str, is_hist = "", False

                if dev_status != "online":
                    if dev_status in ['getting_ready', 'updating', 'provisioning', 'adopting']:
                        inventory.append({"name": dev_name, "model": dev_model, "status": dev_status.upper(), "offline_duration": ""})
                        continue

                    # GATEWAY SUPPRESSION: Only suppress the Gateway itself if the app just restarted
                    if is_gw and app_was_just_restarted:
                        dev_status = "ONLINE"
                    else:
                        dt_str = d.get('lastSeenAt') or d.get('lastConnectionStateChange')
                        last_seen_dt = parse_iso_time(dt_str)
                        if last_seen_dt:
                            diff_sec = (current_time - last_seen_dt).total_seconds()
                            offline_str = format_duration(diff_sec)
                            grace = GW_GRACE_PERIOD_SECONDS if is_gw else DEVICE_GRACE_PERIOD_SECONDS
                            is_hist = (diff_sec >= HISTORICAL_OFFLINE_SECONDS)
                            
                            if diff_sec > grace:
                                if is_hist: historical_devs += 1
                                else: recent_devs += 1
                                
                                if is_gw:
                                    if is_hist:
                                        if weight < 8: status, weight = "Grey", 8
                                        issues.append({"label": "💤 GATEWAY HISTORICALLY DOWN", "time": "> 30d", "severity": "historical"})
                                    else:
                                        if weight < 20: status, weight = "Red", 20
                                        issues.append({"label": "🚨 GATEWAY OFFLINE", "time": f"{offline_str} ago", "severity": "critical"})

                                if diff_sec >= ALERT_THRESHOLD_SECONDS and alert_key not in alert_state:
                                    if name not in pending_offline: pending_offline[name] = []
                                    pending_offline[name].append({"name": dev_name, "model": dev_model, "duration": offline_str, "mac": alert_key})
                        else:
                            historical_devs += 1
                            offline_str = ">30d"

                if dev_status == "online" and alert_key in alert_state:
                    if name not in pending_recovery: pending_recovery[name] = []
                    pending_recovery[name].append({"name": dev_name, "model": dev_model, "mac": alert_key})
                
                inventory.append({"name": dev_name, "model": dev_model, "status": dev_status.upper(), "offline_duration": offline_str})

            # --- SMART ISP FILTER ---
            isp_periods = reported.get('internetIssues5min', {}).get('periods', [])
            if not isp_periods: isp_periods = site_data.get('statistics', {}).get('internetIssues', [])
            
            valid_buckets = 0
            for iss in isp_periods:
                if iss.get('not_reported') or iss.get('notReported') or iss.get('high_latency'): continue
                if (current_bucket - iss.get('index', 0)) <= 24: valid_buckets += 1

            if valid_buckets >= 2:
                if weight < 15: status, weight = "Yellow", 15
                issues.append({"label": "📡 RECENT ISP ISSUE", "time": "< 2h ago", "severity": "warning"})

            if recent_devs > 0 and weight < 10:
                status, weight = "Yellow", 10
                issues.append({"label": f"⚠️ {recent_devs} Device(s) Offline", "time": "Partial", "severity": "warning"})
            
            if historical_devs > 0 and weight < 5:
                # Site stays Green, but list the historically dead devices
                issues.append({"label": f"💤 {historical_devs} Device(s) Historically Down", "time": "> 30d", "severity": "historical"})

            cards.append({"SiteName": name, "Model": primary_model or "Gateway", "ISP": site_data.get('statistics', {}).get('ispInfo', {}).get('name', ''), "Inventory": inventory, "Status": status, "IssuesCount": weight, "IssuesList": issues})
    except Exception as e: log(f"!! Modern API Error: {str(e)}")
    return cards

def fetch_classic_unifi(alert_state, pending_offline, pending_recovery):
    if not CLASSIC_URL: return []
    cards = []
    current_time = datetime.now(timezone.utc)
    try:
        session = requests.Session()
        session.post(f"{CLASSIC_URL}/api/login", json={"username": CLASSIC_USER, "password": CLASSIC_PASS}, verify=False, timeout=15)
        sites_res = session.get(f"{CLASSIC_URL}/api/self/sites", verify=False, timeout=15).json().get('data', [])
        for site in sites_res:
            site_desc = site.get('desc', 'Unnamed Site')
            if site_desc.lower() in IGNORE_SITES: continue
            dev_res = session.get(f"{CLASSIC_URL}/api/s/{site.get('name')}/stat/device", verify=False, timeout=15).json().get('data', [])
            if not dev_res: continue
            inventory, status, weight, issues, recent_devs, historical_devs = [], "Green", 0, [], 0, 0
            for dev in dev_res:
                is_off = (dev.get("state", 0) == 0)
                offline_str = ""
                if is_off:
                    last_seen = dev.get('last_seen') or dev.get('last_disconnect')
                    if last_seen:
                        diff_sec = (current_time - datetime.fromtimestamp(float(last_seen), timezone.utc)).total_seconds()
                        offline_str = format_duration(diff_sec)
                        if diff_sec > DEVICE_GRACE_PERIOD_SECONDS:
                            if diff_sec >= HISTORICAL_OFFLINE_SECONDS: historical_devs += 1
                            else: recent_devs += 1
                    else: historical_devs += 1; offline_str = ">30d"
                inventory.append({"name": dev.get("name") or dev.get("mac"), "model": dev.get("model"), "status": "OFFLINE" if is_off else "ONLINE", "offline_duration": offline_str})
            
            if recent_devs > 0: status, weight = "Yellow", 10; issues.append({"label": f"⚠️ {recent_devs} Device(s) Offline", "time": "Partial", "severity": "warning"})
            cards.append({"SiteName": f"{site_desc} (Cloud)", "Model": "Classic", "ISP": "", "Inventory": inventory, "Status": status, "IssuesCount": weight, "IssuesList": issues})
        session.post(f"{CLASSIC_URL}/api/logout", verify=False)
    except Exception as e: log(f"!! Classic Error: {str(e)}")
    return cards

def harvest_data():
    alert_state = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f: alert_state = json.load(f)
        except: pass
    while True:
        log(">>> Starting Unified Multi-Controller Harvest...")
        pending_off, pending_rec = {}, {}
        modern = fetch_modern_unifi(alert_state, pending_off, pending_rec)
        classic = fetch_classic_unifi(alert_state, pending_off, pending_rec)
        for s, devs in pending_off.items():
            if send_consolidated_offline_alert(s, devs):
                for d in devs: alert_state[d['mac']] = datetime.now(timezone.utc).isoformat()
        for s, devs in pending_rec.items():
            if send_consolidated_recovery_alert(s, devs):
                for d in devs:
                    if d['mac'] in alert_state: del alert_state[d['mac']]
        all_cards = modern + classic
        all_cards.sort(key=lambda x: (-x['IssuesCount'], x['SiteName']))
        with open(TEMP_DATA_FILE, "w", encoding="utf-8") as f: json.dump({"timestamp": datetime.now().strftime("%H:%M:%S"), "sites": all_cards}, f, indent=4)
        os.replace(TEMP_DATA_FILE, DATA_FILE)
        with open(TEMP_STATE_FILE, "w", encoding="utf-8") as f: json.dump(alert_state, f)
        os.replace(TEMP_STATE_FILE, STATE_FILE)
        log(f"*** HARVEST SUCCESS: {len(all_cards)} Sites ***")
        time.sleep(POLL_INTERVAL)

class MyHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args): pass 
    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)): return 
        super().handle_error(request, client_address)
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Access-Control-Allow-Origin', '*')
        SimpleHTTPRequestHandler.end_headers(self)

if __name__ == "__main__":
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w") as f: json.dump({"timestamp": "N/A", "sites": []}, f)
    threading.Thread(target=harvest_data, daemon=True).start()
    HTTPServer(('0.0.0.0', 8080), MyHandler).serve_forever()
