
# Aggregator Pi

A docker image meant for a Raspberry Pi 4/5 that ingests raw sound data (.wav format) via HTTP from multiple sources and processes them. It can run local inference with [BirdNET](https://birdnet-team.github.io/BirdNET-Analyzer/) and uploads the results to InfluxDB 3 hosted in the cloud. It can also upload the raw files to an s3 bucket.
## Testing the script locally
## Setting up the Server

### Setting up InfluxDB 3
1. SSH into whatever server you plan to use. I use a KVM set up on [Chameleon Cloud](https://www.chameleoncloud.org/) running Ubuntu24.04.

2. Whether from firewall or security groups, make sure you open TCP port 8181.

3. Download and install InfluxDB 3
```
sudo apt update
curl -O https://www.influxdata.com/d/install_influxdb3.sh && sh install_influxdb3.sh
```
When prompted, type n
4. Create a user for InfluxDB (or just use your own)
```
sudo useradd -rs /bin/false influxdb
sudo mkdir -p /var/lib/influxdb3
sudo chown -R influxdb:influxdb /var/lib/influxdb3
```

5. Write the systemd unit file so it can run as a service in the background
```
sudo tee /etc/systemd/system/influxdb3.service >/dev/null <<'EOF'
[Unit]
Description=InfluxDB 3 Core (CLI download)
After=network.target

[Service]
User=influxdb
Group=influxdb

# point ExecStart at your downloaded binary + data dir
ExecStart=/usr/local/bin/influxdb3 serve \
  --object-store=file \
  --data-dir=/var/lib/influxdb3 \
  --node-id=%H

# auto-restart on crash, helpful limits
Restart=on-failure
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF
```

6. Active InfluxDB
```
sudo systemctl daemon-reload
sudo systemctl enable --now influxdb3
sudo systemctl status influxdb3
```
It should display that the InfluxDB service is Active

7. Create a tokin
```
influxdb3 create token --admin
```
Make sure to save this token. You need it to access InfluxDB

8. Create the databa
```
export INFLUXDB3_AUTH_TOKEN=<paste-output-here>
influxdb3 create database recordings
```

### Setting up Prometheus
1. SSH into whatever server you plan to use. I use a KVM set up on [Chameleon Cloud](https://www.chameleoncloud.org/) running Ubuntu24.04.

2. Whether from firewall or security groups, make sure you open TCP port 9090.

3. Install Prometheus and its dependencies
```
sudo -i
apt update && apt install -y apache2-utils curl
apt install prometheus

```

4. Create a bcrypt’d admin password
`htpasswd -nB -C 10 admin`
Copy the "$2y$..." hash that appears

5. Set up the web config
```
sudo tee /etc/prometheus/web_config.yml <<'EOF'
basic_auth_users:
  admin: "<your_hash_here>"
EOF
```
**You might need to nano into the file and copy it manually**

6. Create the service
`mkdir -p /etc/systemd/system/prometheus.service.d`
```
cat >/etc/systemd/system/prometheus.service.d/override.conf <<'EOF'
[Service]
# Wipe the ExecStart set by the package …
ExecStart=
# … and replace it with ours.
ExecStart=/usr/bin/prometheus \
  --config.file=/etc/prometheus/prometheus.yml \
  --storage.tsdb.path=/var/lib/prometheus \
  --storage.tsdb.retention.time=15d \
  --web.config.file=/etc/prometheus/web_config.yml \
  --web.enable-remote-write-receiver
EOF
```

7. Give Prometheus access & restart
```
chown -R prometheus:prometheus /var/lib/prometheus
chmod 750 /var/lib/prometheus

sudo systemctl daemon-reload
sudo systemctl restart prometheus
sudo systemctl status prometheus
```
It should display that the Prometheus service is Active

8. Install the Prometheus node exporter and make it a service
(This part is for scraping local metrics on the server)
```
sudo mkdir -p /etc/systemd/system/prometheus-node-exporter.service.d

cat <<’EOF’ | sudo tee /etc/systemd/system/prometheus-node-exporter.service.d/override.conf
[Service]
ExecStart=
ExecStart=/usr/bin/prometheus-node-exporter \
  --web.listen-address=127.0.0.1:9100
EOF

sudo systemctl daemon-reload
sudo systemctl restart prometheus-node-exporter
```

9. Set up the node exporter config
nano into /etc/prometheus/prometheus.yml :
```
global:
  scrape_interval:     15s # Set the scrape interval to every 15 seconds. Default is every 1 minute.
  evaluation_interval: 15s # Evaluate rules every 15 seconds. The default is every 1 minute.
  # scrape_timeout is set to the global default (10s).
  # Attach these labels to any time series or alerts when communicating with
  # external systems (federation, remote storage, Alertmanager).
  external_labels:
      monitor: 'example'
# Alertmanager configuration
alerting:
  alertmanagers:
  - static_configs:
    - targets: ['localhost:9093']
# Load rules once and periodically evaluate them according to the global 'evaluation_interval'.
rule_files:
  # - "first_rules.yml"
  # - "second_rules.yml"
scrape_configs:
  # Self-scrape (needs auth because you locked down /metrics)
  - job_name: 'prometheus'
    scrape_interval: 5s
    scrape_timeout: 5s
    static_configs:
      - targets: ['localhost:9090']
    basic_auth:
      username: admin
      password: "YOUR_ADMIN_PASSWORD"
  # Host-level Node Exporter on the KVM
  - job_name: 'kvm_node'
    static_configs:
      - targets: ['localhost:9100']
        labels:
          role: kvm
```
**Make sure to replace "YOUR_ADMIN_PASSWORD" with the one you made earlier**

`sudo systemctl reload prometheus`


### Setting up Grafana
Setting up Grafana on the server for data visualization is the easiest and most straight forward part of setting up the server. Follow [their official](https://grafana.com/docs/grafana/latest/setup-grafana/installation/debian/) instructions. They even have a video.

**Make sure to open TCP port 3000**
