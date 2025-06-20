FROM --platform=$BUILDPLATFORM python:3.11-slim
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends supervisor rclone build-essential && \
    rm -rf /var/lib/apt/lists/*

# Install Python requirements
RUN pip install --no-cache-dir watchdog influxdb3-python psutil

# Copy files
WORKDIR /app
COPY app ./app
COPY supervisord.conf ./

# Expose default Telegraf port
EXPOSE 8125

# Run all services under Supervisor (PID 1)
CMD ["supervisord", "-c", "/app/supervisord.conf"]