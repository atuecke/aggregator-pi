# Fetch static binaries
FROM prom/node-exporter AS nodeexporter
# contains the agent-capable prometheus binary
FROM prom/prometheus AS promstage


# Final runtime
FROM --platform=$BUILDPLATFORM python:3.11-slim
ENV PYTHONUNBUFFERED=1

# Copy binaries
COPY --from=nodeexporter /bin/node_exporter   /usr/local/bin/node_exporter
COPY --from=promstage    /bin/prometheus      /usr/local/bin/prometheus

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    supervisor \
    redis-server \
    gettext-base \
    rclone \
    build-essential \
    ffmpeg \
    libsndfile1 && \
    rm -rf /var/lib/apt/lists/*

# Make sure the log directory supervisor will write to exists **before** PID 1 starts
RUN mkdir -p /data/logs

# Install Python requirements
RUN pip install --no-cache-dir \ 
                fastapi \ 
                "uvicorn[standard]" \ 
                influxdb3-python prometheus_client psutil PyYAML python-multipart \ 
                tflite-runtime librosa pydub "numpy<2.0" resampy birdnetlib \ 
                redis

# Copy files
WORKDIR                          /app
COPY app                         ./app
COPY supervisord.conf            ./
COPY logging.yaml                /etc/iot/
COPY prometheus-agent.yml.tpl    /etc/prom_agent/prometheus-agent.yml.tpl
COPY redis.conf /etc/redis/redis.conf
COPY entrypoint.sh               /usr/local/bin/entrypoint.sh
COPY settings.yaml /etc/iot/settings.yaml

# Expose 8000 for incoming receiver HTTP, 8001 for python metrics, 9100 for prometheus metrics, 6379 for redis
EXPOSE 8000 8001 9100 6379

# Make entrypoint executable inside the image
RUN chmod +x /usr/local/bin/entrypoint.sh

# Entrypoint renders the real config then execs supervisord
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["supervisord", "-c", "/app/supervisord.conf"]