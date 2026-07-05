import ipaddress
import logging
import os
import socket
import ssl
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import boto3
import requests
from flask import Flask, jsonify, render_template, request

# ──────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


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
RETRY_ATTEMPTS = int(os.environ.get("RETRY_ATTEMPTS", 2))
HEALTHY_STATUS_MIN = int(os.environ.get("HEALTHY_STATUS_MIN", 200))
HEALTHY_STATUS_MAX = int(os.environ.get("HEALTHY_STATUS_MAX", 399))
LOG_RETENTION_DAYS = int(os.environ.get("LOG_RETENTION_DAYS", 90))
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

# AWS SNS Alerts Configuration
AWS_SNS_TOPIC_ARN = os.environ.get("AWS_SNS_TOPIC_ARN", "")

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


@contextmanager
def get_db():
    """Context manager for safe DB access with auto commit/rollback."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db_lock, get_db() as conn:
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

        # Backward compatibility check for ssl_days column
        cursor.execute("PRAGMA table_info(uptime_logs)")
        columns = [row[1] for row in cursor.fetchall()]
        if "ssl_days" not in columns:
            cursor.execute("ALTER TABLE uptime_logs ADD COLUMN ssl_days INTEGER")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        for url in INITIAL_URLS:
            cursor.execute("""
                INSERT INTO monitors (url, active, created_at)
                VALUES (?, 1, ?)
                ON CONFLICT(url) DO UPDATE SET active = 1
            """, (normalize_url(url), now))
    logger.info("Database initialised — monitors and uptime_logs ready.")


def normalize_url(url):
    cleaned = (url or "").strip()
    if cleaned and "://" not in cleaned:
        cleaned = f"https://{cleaned}"
    return cleaned.rstrip("/")


def validate_url(url):
    cleaned = normalize_url(url)
    if not cleaned:
        raise ValueError("URL is required.")

    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("URL must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError("URL is missing a hostname.")

    hostname = (parsed.hostname or "").lower()
    if hostname in {"localhost", "::1"}:
        raise ValueError("Local URLs are not allowed.")

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return cleaned

    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        raise ValueError("Private or local network targets are not allowed.")
    return cleaned


def get_active_urls():
    with db_lock, get_db() as conn:
        rows = conn.execute("""
            SELECT url
            FROM monitors
            WHERE active = 1
            ORDER BY id ASC
        """).fetchall()
    return [row["url"] for row in rows]


def get_all_monitors():
    """Return every monitor with its active flag."""
    with db_lock, get_db() as conn:
        rows = conn.execute("""
            SELECT url, active
            FROM monitors
            ORDER BY id ASC
        """).fetchall()
    return [{"url": row["url"], "active": bool(row["active"])} for row in rows]


def add_monitor(url):
    normalized = validate_url(url)

    with db_lock, get_db() as conn:
        conn.execute("""
            INSERT INTO monitors (url, active, created_at)
            VALUES (?, 1, ?)
            ON CONFLICT(url) DO UPDATE SET active = 1
        """, (normalized, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))

    latest_status.setdefault(normalized, {"url": normalized, "is_up": None})
    return normalized


def stop_monitor(url):
    normalized = validate_url(url)
    with db_lock, get_db() as conn:
        conn.execute("UPDATE monitors SET active = 0 WHERE url = ?", (normalized,))

    latest_status.pop(normalized, None)
    currently_down.discard(normalized)
    return normalized


def resume_monitor(url):
    """Re-activate a stopped monitor."""
    normalized = validate_url(url)
    with db_lock, get_db() as conn:
        conn.execute("UPDATE monitors SET active = 1 WHERE url = ?", (normalized,))

    latest_status.setdefault(normalized, {"url": normalized, "is_up": None})
    return normalized


def remove_monitor(url):
    return stop_monitor(url)


def log_result(url, status_code, response_ms, is_up, ssl_days=None):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with db_lock, get_db() as conn:
        conn.cursor().execute("""
            INSERT INTO uptime_logs (url, timestamp, status_code, response_ms, is_up, ssl_days)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            url,
            timestamp,
            status_code,
            round(response_ms, 2),
            1 if is_up else 0,
            ssl_days
        ))

    latest_status[url] = {
        "url": url,
        "timestamp": timestamp,
        "status_code": status_code,
        "response_ms": round(response_ms, 2),
        "is_up": is_up,
        "ssl_days": ssl_days,
    }


def fetch_recent_logs(limit=50):
    with db_lock, get_db() as conn:
        cursor = conn.execute("PRAGMA table_info(uptime_logs)")
        columns = [row[1] for row in cursor.fetchall()]
        select_cols = "id, url, timestamp, status_code, response_ms, is_up"
        if "ssl_days" in columns:
            select_cols += ", ssl_days"
        
        rows = conn.execute(f"""
            SELECT {select_cols}
            FROM uptime_logs
            ORDER BY id DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(row) for row in rows]


def fetch_summary():
    with db_lock, get_db() as conn:
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
    with db_lock, get_db() as conn:
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
    return [dict(row) for row in rows]


def prune_old_logs():
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOG_RETENTION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    with db_lock, get_db() as conn:
        conn.execute("DELETE FROM uptime_logs WHERE timestamp < ?", (cutoff,))
    logger.info("Pruned uptime logs older than %d days.", LOG_RETENTION_DAYS)


# ──────────────────────────────────────────────
# SSL CHECKER
# ──────────────────────────────────────────────
def get_ssl_days_remaining(hostname, port=443, timeout=5):
    """Retrieve remaining days for an SSL certificate."""
    try:
        context = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                expire_date_str = cert.get('notAfter')
                if not expire_date_str:
                    return None
                expire_date = datetime.strptime(expire_date_str, "%b %d %H:%M:%S %Y %Z")
                expire_date = expire_date.replace(tzinfo=timezone.utc)
                remaining = expire_date - datetime.now(timezone.utc)
                return max(0, remaining.days)
    except Exception as e:
        logger.warning("SSL check failed for %s: %s", hostname, e)
        return None


# ──────────────────────────────────────────────
# AWS SNS ALERTS
# ──────────────────────────────────────────────
def send_aws_sns_alert(url, status_code, response_ms, event):
    """Send an alert message to AWS SNS."""
    if not AWS_SNS_TOPIC_ARN:
        logger.debug("AWS_SNS_TOPIC_ARN not set — skipping SNS alert.")
        return False

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if event == "DOWN":
        subject = f"🔴 ALERT: Site Down - {url}"
        message = (
            f"Uptime Monitor Alert!\n\n"
            f"Target URL   : {url}\n"
            f"Event        : SITE IS DOWN (Unreachable / Bad Status)\n"
            f"Status Code  : {status_code if status_code else 'No response / Timeout'}\n"
            f"Response Time: {response_ms:.1f} ms\n"
            f"Alert Time   : {ts}\n"
        )
    else:
        subject = f"🟢 RECOVERED: Site Online - {url}"
        message = (
            f"Uptime Monitor Recovery Alert!\n\n"
            f"Target URL   : {url}\n"
            f"Event        : SITE RECOVERED (Back Online)\n"
            f"Status Code  : {status_code}\n"
            f"Response Time: {response_ms:.1f} ms\n"
            f"Recovery Time: {ts}\n"
        )

    try:
        if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
            sns = boto3.client(
                "sns",
                aws_access_key_id=AWS_ACCESS_KEY_ID,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                region_name=AWS_REGION,
            )
        else:
            sns = boto3.client("sns", region_name=AWS_REGION)

        logger.info("SNS publishing message for %s to topic %s...", url, AWS_SNS_TOPIC_ARN)
        sns.publish(
            TopicArn=AWS_SNS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
        logger.info("SNS alert published successfully.")
        return True
    except Exception as e:
        logger.error("SNS publication failed: %s", e)
        return False


# ──────────────────────────────────────────────
# DISCORD ALERTS
# ──────────────────────────────────────────────
def send_discord_alert(url, status_code, response_ms, event):
    """Send a rich embed to Discord."""
    if not DISCORD_WEBHOOK_URL:
        logger.debug("Discord webhook URL not set — skipping alert.")
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
            logger.info("Discord alert sent -> %s for %s", event, url)
        else:
            logger.warning("Discord unexpected response: %d", resp.status_code)
    except Exception as e:
        logger.error("Discord failed to send alert: %s", e)


def handle_alerts(url, is_up, status_code, response_ms):
    if not is_up and url not in currently_down:
        currently_down.add(url)
        send_discord_alert(url, status_code, response_ms, "DOWN")
        send_aws_sns_alert(url, status_code, response_ms, "DOWN")
    elif is_up and url in currently_down:
        currently_down.discard(url)
        send_discord_alert(url, status_code, response_ms, "RECOVERED")
        send_aws_sns_alert(url, status_code, response_ms, "RECOVERED")


# ──────────────────────────────────────────────
# AWS S3 BACKUP
# ──────────────────────────────────────────────
def backup_to_s3():
    """Upload the SQLite database to AWS S3 once a day."""
    if not AWS_BUCKET_NAME:
        logger.debug("AWS_BUCKET_NAME not set — skipping backup.")
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

        logger.info("S3 uploading %s to s3://%s/%s...", DB_FILE, AWS_BUCKET_NAME, s3_key)
        s3.upload_file(DB_FILE, AWS_BUCKET_NAME, s3_key)
        monitor_state["last_backup_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("S3 backup successful.")
        return True
    except Exception as e:
        logger.error("S3 backup failed: %s", e)
        return False


# ──────────────────────────────────────────────
# HEALTH CHECK
# ──────────────────────────────────────────────
def check_url(url):
    attempts = max(1, RETRY_ATTEMPTS)
    # Check SSL days remaining if HTTPS
    ssl_days = None
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.hostname:
        ssl_days = get_ssl_days_remaining(parsed.hostname)

    for attempt in range(attempts):
        try:
            start = time.time()
            response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
            elapsed_ms = (time.time() - start) * 1000
            is_up = HEALTHY_STATUS_MIN <= response.status_code <= HEALTHY_STATUS_MAX
            return response.status_code, elapsed_ms, is_up, ssl_days
        except Exception:
            if attempt < attempts - 1:
                continue
            return None, 0, False, ssl_days


def run_single_check(url):
    status_code, response_ms, is_up, ssl_days = check_url(url)
    log_result(url, status_code, response_ms, is_up, ssl_days)
    print_result(url, status_code, response_ms, is_up, ssl_days)
    handle_alerts(url, is_up, status_code, response_ms)
    return latest_status[url]


def run_check_round():
    with monitor_lock:
        urls = get_active_urls()

        monitor_state["round"] += 1
        monitor_state["last_check_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("--- Round %d [%s UTC] ---", monitor_state['round'], datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))

        results = []
        for url in urls:
            results.append(run_single_check(url))

        print_summary()
        return results


# ──────────────────────────────────────────────
# DISPLAY HELPERS
# ──────────────────────────────────────────────
def print_result(url, status_code, response_ms, is_up, ssl_days=None):
    status_label = "UP  " if is_up else "DOWN"
    code_str = str(status_code) if status_code else "N/A"
    ssl_str = f"{ssl_days}d SSL" if ssl_days is not None else "N/A SSL"
    logger.info("%s | %3s | %7.1f ms | %8s | %s", status_label, code_str, response_ms, ssl_str, url)


def print_summary():
    rows = fetch_summary()
    lines = ["\n" + "-" * 65]
    lines.append(f"  {'URL':<38} {'Checks':>6} {'Uptime':>8} {'Avg ms':>8}")
    lines.append("-" * 65)
    for row in rows:
        short_url = row["url"].replace("https://", "")[:38]
        lines.append(f"  {short_url:<38} {row['total']:>6} {row['uptime_pct']:>7.1f}% {row['avg_ms']:>8}")
    lines.append("-" * 65)
    logger.info("\n".join(lines))


# ──────────────────────────────────────────────
# WEB API
# ──────────────────────────────────────────────
@app.route("/")
def dashboard():
    return render_template("index.html")


@app.get("/api/status")
def api_status():
    urls = get_active_urls()
    all_monitors = get_all_monitors()

    # Get history
    with db_lock, get_db() as conn:
        cursor = conn.execute("PRAGMA table_info(uptime_logs)")
        columns = [row[1] for row in cursor.fetchall()]
        select_cols = "url, is_up, timestamp"
        if "ssl_days" in columns:
            select_cols += ", ssl_days"
        
        history_rows = conn.execute(f"""
            SELECT {select_cols}
            FROM (
                SELECT url, is_up, timestamp, id {" , ssl_days" if "ssl_days" in columns else ""}
                FROM uptime_logs
                ORDER BY id DESC
                LIMIT 500
            )
            ORDER BY id ASC
        """).fetchall()

    history_by_url = {}
    for row in history_rows:
        u = row["url"]
        history_by_url.setdefault(u, [])
        history_by_url[u].append({
            "is_up": bool(row["is_up"]),
            "timestamp": row["timestamp"],
            "ssl_days": row["ssl_days"] if "ssl_days" in columns else None
        })

    latest_data = []
    for url in urls:
        item = latest_status.get(url, {"url": url, "is_up": None})
        if url in history_by_url:
            item["history"] = history_by_url[url][-20:]
            latest_ssl = history_by_url[url][-1].get("ssl_days")
            if latest_ssl is not None:
                item["ssl_days"] = latest_ssl
        else:
            item["history"] = []
        latest_data.append(item)

    return jsonify({
        "state": monitor_state,
        "urls": urls,
        "all_monitors": all_monitors,
        "latest": latest_data,
        "summary": fetch_summary(),
        "chart": fetch_chart_points(),
        "config": {
            "check_interval": CHECK_INTERVAL_SECONDS,
            "request_timeout": REQUEST_TIMEOUT_SECONDS,
            "healthy_status_min": HEALTHY_STATUS_MIN,
            "healthy_status_max": HEALTHY_STATUS_MAX,
            "discord_enabled": bool(DISCORD_WEBHOOK_URL),
            "s3_enabled": bool(AWS_BUCKET_NAME),
            "sns_enabled": bool(AWS_SNS_TOPIC_ARN),
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


@app.post("/api/urls/stop")
def api_stop_url():
    data = request.get_json(silent=True) or {}
    try:
        url = stop_monitor(data.get("url") or "")
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "url": url})


@app.post("/api/urls/resume")
def api_resume_url():
    data = request.get_json(silent=True) or {}
    try:
        url = resume_monitor(data.get("url") or "")
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
        prune_old_logs()
        logger.info("Next check in %ds.", CHECK_INTERVAL_SECONDS)
        time.sleep(CHECK_INTERVAL_SECONDS)


def main():
    logger.info("=" * 65)
    logger.info("Uptime Monitor V2 — advanced dashboard enabled")
    logger.info("=" * 65)
    logger.info("Dashboard      : http://localhost:%d", PORT)
    logger.info("Discord Alerts : %s", 'ENABLED' if DISCORD_WEBHOOK_URL else 'DISABLED (set DISCORD_WEBHOOK_URL env var)')
    logger.info("AWS SNS Alerts : %s", 'ENABLED' if AWS_SNS_TOPIC_ARN else 'DISABLED (set AWS_SNS_TOPIC_ARN env var)')
    logger.info("Database       : %s", DB_FILE)
    logger.info("S3 Backups     : %s", 'ENABLED (Every 24h)' if AWS_BUCKET_NAME else 'DISABLED (missing AWS_BUCKET_NAME env var)')
    init_db()
    logger.info("Monitoring     : %d URLs every %ds", len(get_active_urls()), CHECK_INTERVAL_SECONDS)
    threading.Thread(target=monitor_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
