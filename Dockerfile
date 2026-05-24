# ──────────────────────────────────────────────
# Uptime Monitor — Production Dockerfile
# ──────────────────────────────────────────────
FROM python:3.11-slim

# Prevent Python from buffering stdout/stderr (critical for docker logs)
ENV PYTHONUNBUFFERED=1

# Set working directory inside the container
WORKDIR /app

# Copy and install dependencies first (layer caching optimisation)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY monitor.py .

# Default data directory for the SQLite volume mount
ENV DATA_DIR=/data

# Create the data directory
RUN mkdir -p /data

# Run the monitor
CMD ["python", "monitor.py"]
