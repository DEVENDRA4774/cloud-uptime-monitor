# Cloud Uptime Monitor V2

An advanced, Dockerized uptime monitor built with Python, Flask, SQLite, Discord alerts, and AWS S3 backups. V1 was a terminal monitor. V2 adds a full interactive dashboard, API endpoints, persistent monitor management, charts, and browser controls.

## V2 Upgrade Summary

- Advanced Flask dashboard at `http://localhost:5000`.
- Persistent monitor URLs stored in SQLite.
- Add/remove website monitors from the browser.
- Manual **Check now** action.
- Live uptime, response-time, healthy-service, alert, and backup metrics.
- Recent activity feed and response-time chart.
- REST API routes for status, logs, URL management, manual checks, and health.
- Configurable check interval, timeout, healthy status-code range, and startup URLs.
- Docker support with dashboard port exposed.

## 🖥️ Interactive Dashboard

This project includes a live web dashboard with:

- Real-time uptime, response-time, and service-health metrics.
- A response-time trend chart.
- Recent check activity.
- Add/remove monitor URLs from the browser, saved permanently in SQLite.
- A **Check now** button for manual checks.

Run locally:

```powershell
cd D:\cloud-uptime-monitor-main\cloud-uptime-monitor-main
python -m pip install -r requirements.txt
python monitor.py
```

Then open:

```text
http://localhost:5000
```

With Docker:

```powershell
docker compose up --build
```

Then open `http://localhost:5000`.

## Configuration

Copy `.env.example` to `.env` and update the values you need:

```env
URLS_TO_MONITOR=https://google.com,https://github.com
CHECK_INTERVAL=60
REQUEST_TIMEOUT=10
HEALTHY_STATUS_MIN=200
HEALTHY_STATUS_MAX=399
PORT=5000
DISCORD_WEBHOOK_URL=
AWS_BUCKET_NAME=
AWS_REGION=us-east-1
```

## 🛠️ How it Works
1. **You build a website** (e.g., `www.mycoolwebsite.com`).
2. **You add that URL to this app** from the dashboard, or by setting the `URLS_TO_MONITOR` environment variable.
3. **The app watches it 24/7:** The AWS server silently checks the website every 60 seconds.
4. **🔴 Red on Discord:** If the website crashes, the app instantly sends a **RED** message to Discord saying: `[DOWN]`.
5. **✅ Green on Discord:** Once the website is fixed, it sends a **GREEN** message saying `[RECOVERED]`.

---

## 📚 Command Cheat Sheet

### 💻 1. Commands for your Windows Computer (PowerShell)
*Run these locally to send code to GitHub or connect to the server.*

| Command | What it does | How it works |
| :--- | :--- | :--- |
| `cd D:\uptime-monitor` | Change Directory | Moves your terminal into your project folder. Always run this first. |
| `git add .` | Stage files | Tells Git to get all changed files ready to be saved. |
| `git commit -m "Message"` | Save files | Takes a "snapshot" of your code with a description. |
| `git push` | Upload to GitHub | Uploads your code to GitHub (which triggers the automated AWS deployment!). |
| `ssh -i uptime-key.pem ec2-user@YOUR_IP` | Secure Shell | The "magic door". Uses your `.pem` key to securely log into the remote AWS server. |

### ☁️ 2. Commands for your AWS Server (Linux)
*Run these ONLY after you have used the `ssh` command and your prompt says `[ec2-user@ip...]`.*

| Command | What it does | How it works |
| :--- | :--- | :--- |
| `cd ~/uptime-monitor` | Change Directory | Moves you into your project folder on the server. |
| `ls` | List files | Shows a list of all files and folders in your current directory. |
| `cat monitor.py` | concatenate | Prints the entire contents of a file directly onto your screen. |
| `exit` | Logout | Closes the SSH connection and returns you to Windows PowerShell. |

### 🐳 3. Docker Commands (Run on the Server)
*Docker is the engine that runs your Python script continuously in the background.*

| Command | What it does | How it works |
| :--- | :--- | :--- |
| `docker compose logs -f` | View live output | Connects to your running monitor and streams the live logs. (Press `Ctrl + C` to stop watching). |
| `docker compose down` | Stop the app | Completely shuts down your application. |
| `docker compose up -d` | Start the app | Starts the app silently in the background (`-d` = detached). |
| `docker compose up --build -d` | Rebuild & Start | Forces Docker to read new code and build a fresh container before starting it. |
