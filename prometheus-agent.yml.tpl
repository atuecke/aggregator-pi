global:
  scrape_interval: ${PROM_SCRAPE_INTERVAL}

scrape_configs:
  - job_name: 'node'
    static_configs:
      - targets: ['localhost:9100']
        labels:
          aggregator_uuid: ${AGGREGATOR_UUID}

  - job_name: 'python'
    static_configs:
      - targets: ['localhost:8001']
        labels:
          aggregator_uuid: ${AGGREGATOR_UUID}

  - job_name: 'aggregator_supervisor'
    static_configs:
      - targets: ['localhost:9105']
        labels:
            aggregator_uuid: ${AGGREGATOR_UUID}

remote_write:
  - url: "http://${KVM_IP}:9090/api/v1/write"
    basic_auth:
      username: "${PROM_USER}"
      password: "${PROM_PASS}"
    queue_config:
      capacity: 5000
      max_shards: 1
