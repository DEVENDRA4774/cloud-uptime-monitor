import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

import boto3
import requests
from flask import Flask, jsonify, render_template, request


# ──────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────
DEFAULT_URLS = [
    "https://www.google.com",
    "https://www.github.com",
    "https://www.flipkart.com",
]


def parse_urls(value):
    urls = [url.strip() for url in value.split(",") if url.strip()]
    return urls or DEFAULT_URLS


INITIAL_URLS = parse_urls(os.environ.get("URLS_TO_MONITOR", ",".join(DEFAULT_URLS)))
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL", 60))
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("REQUEST_TIMEOUT", 10))
HEALTHY_STATUS_MIN = int(os.environ.get("HEALTHY_STATUS_MIN", 200))
HEALTHY_STATUS_MAX = int(os.environ.get("HEALTHY_STATUS_MAX", 399))
PORT = int(os.environ.get("PORT", 5000))

# Data directory — defaults to "." for local, set to "/data" in Docker
DATA_DIR = os.environ.get("DATA_DIR", ".")
DB_FILE = os.path.join(DATA_DIR, "uptime.db")

# Discord Webhook URL — read from environment variable
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# AWS S3 Backup Configuration
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_BUCKET_NAME = os.environ.get("AWS_BUCKET_NAME", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

app = Flask(__name__)
db_lock = threading.Lock()
monitor_lock = threading.Lock()
currently_down = set()
latest_status = {}
monitor_state = {
    "round": 0,
    "last_backup_at": None,
    "last_check_at": None,
    "started_at": datetime.now(timezone.utc).isoformat(),
}


# ──────────────────────────────────────────────
# DATABASE SETUP
# ──────────────────────────────────────────────
def get_connection():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_lock:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS uptime_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT    NOT NULL,
                timestamp   TEXT    NOT NULL,
                status_code INTEGER,
                response_ms REAL,
                is_up       INTEGER NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS monitors (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                url        TEXT    NOT NULL UNIQUE,
                active     INTEGER NOT NULL DEFAULT 1,
                created_at TEXT    NOT NULL
            )
        """)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        for url in INITIAL_URLS:
            cursor.execute("""
                INSERT INTO monitors (url, active, created_at)
                VALUES (?, 1, ?)
                ON CONFLICT(url) DO UPDATE SET active = 1
            """, (normalize_url(url), now))
        conn.commit()
        conn.close()
    print("[DB] Database initialised — monitors and uptime_logs ready.", flush=True)


def normalize_url(url):
    cleaned = url.strip()
    if cleaned and not cleaned.startswith(("http://", "https://")):
        cleaned = f"https://{cleaned}"
    return cleaned.rstrip("/")


def get_active_urls():
    with db_lock:
        conn = get_connection()
        rows = conn.execute("""
            SELECT url
            FROM monitors
            WHERE active = 1
            ORDER BY id ASC
        """).fetchall()
        conn.close()
    return [row["url"] for row in rows]


def add_monitor(url):
    normalized = normalize_url(url)
    if not normalized:
        raise ValueError("URL is required.")

    with db_lock:
        conn = get_connection()
        conn.execute("""
            INSERT INTO monitors (url, active, created_at)
            VALUES (?, 1, ?)
            ON CONFLICT(url) DO UPDATE SET active = 1
        """, (normalized, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()

    latest_status.setdefault(normalized, {"url": normalized, "is_up": None})
    return normalized


def remove_monitor(url):
    normalized = normalize_url(url)
    with db_lock:
        conn = get_connection()
        conn.execute("UPDATE monitors SET active = 0 WHERE url = ?", (normalized,))
        conn.commit()
        conn.close()

    latest_status.pop(normalized, None)
    currently_down.discard(normalized)
    return normalized


def log_result(url, status_code, response_ms, is_up):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with db_lock:
        conn = get_connection()
        conn.cursor().execute("""
            INSERT INTO uptime_logs (url, timestamp, status_code, response_ms, is_up)
            VALUES (?, ?, ?, ?, ?)
        """, (
            url,
            timestamp,
            status_code,
            round(response_ms, 2),
            1 if is_up else 0
        ))
        conn.commit()
        conn.close()

    latest_status[url] = {
        "url": url,
        "timestamp": timestamp,
        "status_code": status_code,
        "response_ms": round(response_ms, 2),
        "is_up": is_up,
    }


def fetch_recent_logs(limit=50):
    with db_lock:
        conn = get_connection()
        rows = conn.execute("""
            SELECT id, url, timestamp, status_code, response_ms, is_up
            FROM uptime_logs
            ORDER BY id DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
    return [dict(row) for row in rows]


def fetch_summary():
    with db_lock:
        conn = get_connection()
        rows = conn.execute("""
            SELECT url,
                   COUNT(*) AS total,
                   SUM(is_up) AS up_count,
                   ROUND(AVG(response_ms), 1) AS avg_ms,
                   MAX(timestamp) AS last_seen
            FROM uptime_logs
            GROUP BY url
            ORDER BY url
        """).fetchall()
        conn.close()

    summary = []
    for row in rows:
        total = row["total"] or 0
        up_count = row["up_count"] or 0
        uptime_pct = round((up_count / total * 100), 2) if total else 0
        summary.append({
            "url": row["url"],
            "total": total,
            "up_count": up_count,
            "down_count": total - up_count,
            "uptime_pct": uptime_pct,
            "avg_ms": row["avg_ms"] or 0,
            "last_seen": row["last_seen"],
        })
    return summary


def fetch_chart_points(limit=80):
    with db_lock:
        conn = get_connection()
        rows = conn.execute("""
            SELECT timestamp,
                   ROUND(AVG(response_ms), 1) AS avg_ms,
                   ROUND(AVG(is_up) * 100, 1) AS uptime_pct
            FROM (
                SELECT timestamp, response_ms, is_up
                FROM uptime_logs
                ORDER BY id DESC
                LIMIT ?
            )
            GROUP BY timestamp
            ORDER BY timestamp ASC
        """, (limit,)).fetchall()
        conn.close()
    return [dict(row) for row in rows]


# ──────────────────────────────────────────────
# DISCORD ALERTS
# ──────────────────────────────────────────────
def send_discord_alert(url, status_code, response_ms, event):
    """Send a rich embed to Discord."""
    if not DISCORD_WEBHOOK_URL:
        print("  [Discord] Webhook URL not set — skipping alert.", flush=True)
        return

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if event == "DOWN":
        color = 0xFF0000
        title = "SITE DOWN"
        message = (
            f"**{url}** is not responding!\n"
            f"Status Code : `{status_code if status_code else 'No response / Timeout'}`\n"
            f"Response Time: `{response_ms:.1f} ms`\n"
            f"Time         : `{ts}`"
        )
    else:
        color = 0x00AA55
        title = "SITE RECOVERED"
        message = (
            f"**{url}** is back online!\n"
            f"Status Code : `{status_code}`\n"
            f"Response Time: `{response_ms:.1f} ms`\n"
            f"Time         : `{ts}`"
        )

    payload = {"embeds": [{"title": title, "description": message, "color": color}]}

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code == 204:
            print(f"  [Discord] Alert sent -> {event} for {url}", flush=True)
        else:
            print(f"  [Discord] Unexpected response: {resp.status_code}", flush=True)
    except Exception as e:
        print(f"  [Discord] Failed to send alert: {e}", flush=True)


def handle_alerts(url, is_up, status_code, response_ms):
    if not is_up and url not in currently_down:
        currently_down.add(url)
        send_discord_alert(url, status_code, response_ms, "DOWN")
    elif is_up and url in currently_down:
        currently_down.discard(url)
        send_discord_alert(url, status_code, response_ms, "RECOVERED")


# ──────────────────────────────────────────────
# AWS S3 BACKUP
# ──────────────────────────────────────────────
def backup_to_s3():
    """Upload the SQLite database to AWS S3 once a day."""
    if not AWS_BUCKET_NAME:
        print("  [S3 Backup] AWS_BUCKET_NAME not set — skipping backup.", flush=True)
        return False

    try:
        if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
            s3 = boto3.client(
                "s3",
                aws_access_key_id=AWS_ACCESS_KEY_ID,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                region_name=AWS_REGION,
            )
        else:
            s3 = boto3.client("s3", region_name=AWS_REGION)

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        s3_key = f"backups/uptime-{date_str}.db"

        print(f"  [S3 Backup] Uploading {DB_FILE} to s3://{AWS_BUCKET_NAME}/{s3_key}...", flush=True)
        s3.upload_file(DB_FILE, AWS_BUCKET_NAME, s3_key)
        monitor_state["last_backup_at"] = datetime.now(timezone.utc).isoformat()
        print("  [S3 Backup] Backup successful.", flush=True)
        return True
    except Exception as e:
        print(f"  [S3 Backup] Failed to upload backup: {e}", flush=True)
        return False


# ──────────────────────────────────────────────
# HEALTH CHECK
# ──────────────────────────────────────────────
def check_url(url):
    try:
        start = time.time()
        response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        elapsed_ms = (time.time() - start) * 1000
        is_up = HEALTHY_STATUS_MIN <= response.status_code <= HEALTHY_STATUS_MAX
        return response.status_code, elapsed_ms, is_up
    except requests.exceptions.RequestException:
        return None, 0, False


def run_single_check(url):
    status_code, response_ms, is_up = check_url(url)
    log_result(url, status_code, response_ms, is_up)
    print_result(url, status_code, response_ms, is_up)
    handle_alerts(url, is_up, status_code, response_ms)
    return latest_status[url]


def run_check_round():
    with monitor_lock:
        urls = get_active_urls()

        monitor_state["round"] += 1
        monitor_state["last_check_at"] = datetime.now(timezone.utc).isoformat()
        print(f"\n--- Round {monitor_state['round']} [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC] ---")

        results = []
        for url in urls:
            results.append(run_single_check(url))

        print_summary()
        return results


# ──────────────────────────────────────────────
# DISPLAY HELPERS
# ──────────────────────────────────────────────
def print_result(url, status_code, response_ms, is_up):
    status_label = "UP  " if is_up else "DOWN"
    code_str = str(status_code) if status_code else "N/A"
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {status_label} | {code_str:>3} | {response_ms:>7.1f} ms | {url}", flush=True)


def print_summary():
    rows = fetch_summary()
    print("\n" + "-" * 65)
    print(f"  {'URL':<38} {'Checks':>6} {'Uptime':>8} {'Avg ms':>8}")
    print("-" * 65)
    for row in rows:
        short_url = row["url"].replace("https://", "")[:38]
        print(f"  {short_url:<38} {row['total']:>6} {row['uptime_pct']:>7.1f}% {row['avg_ms']:>8}")
    print("-" * 65 + "\n")


# ──────────────────────────────────────────────
# WEB API
# ──────────────────────────────────────────────
@app.route("/")
def dashboard():
    return render_template("index.html")


@app.get("/api/status")
def api_status():
    urls = get_active_urls()
    return jsonify({
        "state": monitor_state,
        "urls": urls,
        "latest": [latest_status.get(url, {"url": url, "is_up": None}) for url in urls],
        "summary": fetch_summary(),
        "chart": fetch_chart_points(),
        "config": {
            "check_interval": CHECK_INTERVAL_SECONDS,
            "request_timeout": REQUEST_TIMEOUT_SECONDS,
            "healthy_status_min": HEALTHY_STATUS_MIN,
            "healthy_status_max": HEALTHY_STATUS_MAX,
            "discord_enabled": bool(DISCORD_WEBHOOK_URL),
            "s3_enabled": bool(AWS_BUCKET_NAME),
        },
    })


@app.get("/api/logs")
def api_logs():
    limit = min(int(request.args.get("limit", 80)), 250)
    return jsonify(fetch_recent_logs(limit))


@app.post("/api/check-now")
def api_check_now():
    results = run_check_round()
    return jsonify({"ok": True, "results": results})


@app.post("/api/urls")
def api_add_url():
    data = request.get_json(silent=True) or {}
    try:
        url = add_monitor(data.get("url") or "")
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "url": url})


@app.delete("/api/urls")
def api_delete_url():
    data = request.get_json(silent=True) or {}
    url = remove_monitor(data.get("url") or "")
    return jsonify({"ok": True, "url": url})


@app.get("/api/health")
def api_health():
    return jsonify({
        "ok": True,
        "version": "2.0",
        "database": DB_FILE,
        "active_monitors": len(get_active_urls()),
    })


# ──────────────────────────────────────────────
# MAIN LOOP
# ──────────────────────────────────────────────
def monitor_loop():
    last_backup_time = 0
    while True:
        if time.time() - last_backup_time >= 86400:
            if AWS_BUCKET_NAME:
                backup_to_s3()
            last_backup_time = time.time()

        run_check_round()
        print(f"   Next check in {CHECK_INTERVAL_SECONDS}s.", flush=True)
        time.sleep(CHECK_INTERVAL_SECONDS)


def main():
    print("=" * 65, flush=True)
    print("   Uptime Monitor V2 — advanced dashboard enabled", flush=True)
    print("=" * 65, flush=True)
    print(f"   Dashboard      : http://localhost:{PORT}", flush=True)
    print(f"   Discord Alerts : {'ENABLED' if DISCORD_WEBHOOK_URL else 'DISABLED (set DISCORD_WEBHOOK_URL env var)'}", flush=True)
    print(f"   Database       : {DB_FILE}", flush=True)
    print(f"   S3 Backups     : {'ENABLED (Every 24h)' if AWS_BUCKET_NAME else 'DISABLED (missing AWS_BUCKET_NAME env var)'}", flush=True)
    init_db()
    print(f"   Monitoring     : {len(get_active_urls())} URLs every {CHECK_INTERVAL_SECONDS}s\n", flush=True)
    threading.Thread(target=monitor_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
