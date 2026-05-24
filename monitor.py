import requests
import sqlite3
import time
import os
import boto3
from datetime import datetime, timezone

# ──────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────
URLS_TO_MONITOR = [
    "https://www.google.com",
    "https://www.github.com",
    "https://www.wikipedia.org",
    "https://httpstat.us/200",   # Always returns 200 (healthy test)
    "https://httpstat.us/503",   # Always returns 503 (failure test)
]

CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL", 60))

# Data directory — defaults to "." for local, set to "/data" in Docker
DATA_DIR = os.environ.get("DATA_DIR", ".")
DB_FILE  = os.path.join(DATA_DIR, "uptime.db")

# Discord Webhook URL — read from environment variable
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# AWS S3 Backup Configuration
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_BUCKET_NAME = os.environ.get("AWS_BUCKET_NAME", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


# ──────────────────────────────────────────────
# DATABASE SETUP
# ──────────────────────────────────────────────
def init_db(conn):
    conn.cursor().execute("""
        CREATE TABLE IF NOT EXISTS uptime_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url         TEXT    NOT NULL,
            timestamp   TEXT    NOT NULL,
            status_code INTEGER,
            response_ms REAL,
            is_up       INTEGER NOT NULL
        )
    """)
    conn.commit()
    print("[DB] Database initialised — uptime_logs table ready.", flush=True)


def log_result(conn, url, status_code, response_ms, is_up):
    conn.cursor().execute("""
        INSERT INTO uptime_logs (url, timestamp, status_code, response_ms, is_up)
        VALUES (?, ?, ?, ?, ?)
    """, (
        url,
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        status_code,
        round(response_ms, 2),
        1 if is_up else 0
    ))
    conn.commit()


# ──────────────────────────────────────────────
# DISCORD ALERTS
# ──────────────────────────────────────────────
# Tracks which URLs are currently DOWN so we don't spam alerts
_currently_down = set()


def send_discord_alert(url, status_code, response_ms, event):
    """
    Send a rich embed to Discord.
    event = "DOWN" or "RECOVERED"
    """
    if not DISCORD_WEBHOOK_URL:
        print("  [Discord] ⚠️  Webhook URL not set — skipping alert.", flush=True)
        return

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if event == "DOWN":
        color   = 0xFF0000          # Red
        title   = "🔴  SITE DOWN"
        message = (
            f"**{url}** is not responding!\n"
            f"Status Code : `{status_code if status_code else 'No response / Timeout'}`\n"
            f"Response Time: `{response_ms:.1f} ms`\n"
            f"Time         : `{ts}`"
        )
    else:  # RECOVERED
        color   = 0x00FF00          # Green
        title   = "✅  SITE RECOVERED"
        message = (
            f"**{url}** is back online!\n"
            f"Status Code : `200 OK`\n"
            f"Response Time: `{response_ms:.1f} ms`\n"
            f"Time         : `{ts}`"
        )

    payload = {
        "embeds": [{
            "title":       title,
            "description": message,
            "color":       color,
        }]
    }

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code == 204:
            print(f"  [Discord] Alert sent → {event} for {url}", flush=True)
        else:
            print(f"  [Discord] Unexpected response: {resp.status_code}", flush=True)
    except Exception as e:
        print(f"  [Discord] Failed to send alert: {e}", flush=True)


def handle_alerts(url, is_up, status_code, response_ms):
    """
    Fire a DOWN alert only on the FIRST failure (no spam).
    Fire a RECOVERED alert the moment the site comes back.
    """
    global _currently_down

    if not is_up and url not in _currently_down:
        _currently_down.add(url)
        send_discord_alert(url, status_code, response_ms, "DOWN")

    elif is_up and url in _currently_down:
        _currently_down.discard(url)
        send_discord_alert(url, status_code, response_ms, "RECOVERED")


# ──────────────────────────────────────────────
# AWS S3 BACKUP
# ──────────────────────────────────────────────
def backup_to_s3():
    """Upload the SQLite database to AWS S3 once a day."""
    if not AWS_BUCKET_NAME:
        print("  [S3 Backup] ⚠️  AWS_BUCKET_NAME not set — skipping backup.", flush=True)
        return False
        
    try:
        # If explicit credentials are provided, use them. 
        # Otherwise, boto3 automatically uses the IAM role attached to the EC2 instance.
        if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
            s3 = boto3.client(
                's3',
                aws_access_key_id=AWS_ACCESS_KEY_ID,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                region_name=AWS_REGION
            )
        else:
            s3 = boto3.client('s3', region_name=AWS_REGION)
        
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        s3_key = f"backups/uptime-{date_str}.db"
        
        print(f"  [S3 Backup] ⏳ Uploading {DB_FILE} to s3://{AWS_BUCKET_NAME}/{s3_key}...", flush=True)
        s3.upload_file(DB_FILE, AWS_BUCKET_NAME, s3_key)
        print(f"  [S3 Backup] ✅ Backup successful!", flush=True)
        return True
    except Exception as e:
        print(f"  [S3 Backup] ❌ Failed to upload backup: {e}", flush=True)
        return False


# ──────────────────────────────────────────────
# HEALTH CHECK
# ──────────────────────────────────────────────
def check_url(url):
    try:
        start = time.time()
        response = requests.get(url, timeout=10)
        elapsed_ms = (time.time() - start) * 1000
        is_up = response.status_code == 200
        return response.status_code, elapsed_ms, is_up
    except requests.exceptions.ConnectionError:
        return None, 0, False
    except requests.exceptions.Timeout:
        return None, 0, False


# ──────────────────────────────────────────────
# DISPLAY HELPERS
# ──────────────────────────────────────────────
def print_result(url, status_code, response_ms, is_up):
    status_label = "✅  UP  " if is_up else "🔴 DOWN"
    code_str     = str(status_code) if status_code else "N/A"
    ts           = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {status_label} | {code_str:>3}  | {response_ms:>7.1f} ms | {url}", flush=True)


def print_summary(conn):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT url,
               COUNT(*)                       AS total,
               SUM(is_up)                     AS up_count,
               ROUND(AVG(response_ms), 1)     AS avg_ms
        FROM   uptime_logs
        GROUP  BY url
    """)
    rows = cursor.fetchall()
    print("\n" + "─" * 65)
    print(f"  {'URL':<38} {'Checks':>6} {'Uptime':>8} {'Avg ms':>8}")
    print("─" * 65)
    for url, total, up_count, avg_ms in rows:
        uptime_pct = (up_count / total * 100) if total else 0
        short_url  = url.replace("https://", "")[:38]
        print(f"  {short_url:<38} {total:>6} {uptime_pct:>7.1f}% {avg_ms:>8}")
    print("─" * 65 + "\n")


# ──────────────────────────────────────────────
# MAIN LOOP
# ──────────────────────────────────────────────
def main():
    print("=" * 65, flush=True)
    print("   🖥️  Uptime Monitor V2 — Live on AWS! 🚀", flush=True)
    print("=" * 65, flush=True)

    webhook_set = bool(DISCORD_WEBHOOK_URL)
    print(f"   Discord Alerts : {'✅ ENABLED' if webhook_set else '⚠️  DISABLED (set DISCORD_WEBHOOK_URL env var)'}", flush=True)
    print(f"   Database       : {DB_FILE}", flush=True)
    
    aws_set = bool(AWS_BUCKET_NAME)
    print(f"   S3 Backups     : {'✅ ENABLED (Every 24h)' if aws_set else '⚠️  DISABLED (missing AWS_BUCKET_NAME env var)'}", flush=True)
    
    print(f"   Monitoring     : {len(URLS_TO_MONITOR)} URLs every {CHECK_INTERVAL_SECONDS}s\n", flush=True)

    conn = sqlite3.connect(DB_FILE)
    init_db(conn)

    round_number = 0
    last_backup_time = 0  # Forces a backup on the very first round if configured

    try:
        while True:
            round_number += 1
            print(f"\n--- Round {round_number}  [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC] ---")
            
            # Trigger daily S3 backup (86400 seconds = 24 hours)
            if time.time() - last_backup_time >= 86400:
                if aws_set:
                    backup_to_s3()
                last_backup_time = time.time()

            for url in URLS_TO_MONITOR:
                status_code, response_ms, is_up = check_url(url)
                log_result(conn, url, status_code, response_ms, is_up)
                print_result(url, status_code, response_ms, is_up)
                handle_alerts(url, is_up, status_code, response_ms)

            print_summary(conn)
            print(f"   ⏳ Next check in {CHECK_INTERVAL_SECONDS}s …", flush=True)
            time.sleep(CHECK_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        print("\n\n[Monitor] Stopped by user.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
