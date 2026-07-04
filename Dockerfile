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

# Copy application code and dashboard assets
COPY monitor.py .
COPY templates ./templates
COPY static ./static

# Default data directory for the SQLite volume mount
ENV DATA_DIR=/data

# Create the data directory
RUN mkdir -p /data

# Run the monitor and dashboard
EXPOSE 5000
CMD ["python", "monitor.py"]
