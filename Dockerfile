FROM --platform=$BUILDPLATFORM python:3.11-slim
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends supervisor rclone build-essential && \
    rm -rf /var/lib/apt/lists/*

# Make sure the log directory supervisor will write to exists **before** PID 1 starts
RUN mkdir -p /data/logs

# Install Python requirements
RUN pip install --no-cache-dir watchdog influxdb3-python psutil PyYAML

# Copy files
WORKDIR /app
COPY app ./app
COPY supervisord.conf ./
COPY logging.yaml /etc/iot/

# Run all services under Supervisor (PID 1)
CMD ["supervisord", "-c", "/app/supervisord.conf"]