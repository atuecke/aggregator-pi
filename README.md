
# Aggregator Pi

A docker image meant for a Raspberry Pi 4/5 that ingests raw sound data (.wav format) via HTTP from multiple sources and processes them. It can run local inference with [BirdNET](https://birdnet-team.github.io/BirdNET-Analyzer/) and uploads the results to InfluxDB 3 hosted in the cloud, aswell as metrics to Prometheus. It can also upload the raw files to an s3 bucket.

To get set up, follow the below steps.
## Testing the Container Locally

Make sure you have docker installed

Configure settings in `/aggregator/settings.yaml`. Settings can also be set in environment variables. Environment variables override settings in the yaml file. All secret variables such as  `influx_token` should be put in environment variables. Save environment variables in a new file `/aggregator/.env`. Any booleans should be`true/false` in config.yaml file or `"0"/"1"` in .env. Variables in config.yaml should be lowercase and their corresponding variables in .env should be uppercase.

For example, if settings.yaml has `metrics_interval_sec: 15` but the .env file has `METRICS_INTERVAL_SEC="10"`, it will be set to 10 instead of 15.

**Once you have set all settings (make sure the server is set up first, see the server section below), you can run the container:**

Navigate to /aggregator/ in the terminal and run `docker compose up`. Alternitively, if you are using VS Code, install the "Container Tools" extension and right click on `docker-compose-dev.yml` and click "Compose Up". A new volume is created automatically and mounted on /data/. All recordings and logs are saved in that directory. You should see metrics showing up in Prometheus. To view them, connect Grafana to Prometheus and create a Grafana dashboard. You may use the example json dashboard I have created. **Make sure to replace all references of "YOUR_UID_HERE" with whatever your Prometheus UID is in Grafana.** 

To actually test the container (on raw audio files), you have two options.

Option 1: Make `generate_mock_audio` true in settings. This turns on a very basic process that generates white noise locally in the container just to make sure that the other processes work.

Option 2: To fully test the container, use [mock listener scripts](https://github.com/atuecke/mock-listener) that I made. These are used to immitate multiple listeners, simultaneously streaming (or uploading) audio to an endpoint on the container. Make sure you have a mock audio .wav file saved to send. I would recommend downloading [this](https://xeno-canto.org/169082) and trimming it to 10-20 seconds. Run stress_test_stream.py with the following arguments:

`--file`: The path to the audio file you want to repeatedly send

`--num-sources`: The number of listeners you want to simulate sending **simultaneously**

`--interval`: The number seconds to wait between each send. For a full duty cycle, this would be zero

`--stagger`: The timespan to randomly stagger the emulated listeners. This stops all of them from sending at the exact same time, which is more realistic

`--url`: The endpoint to send to. In this case, it would be `http://localhost:8000/recordings_stream` because you are running the container locally

For example: `python stress_test_stream.py --file ./wav_samples/sample.wav --num-sources 5 --interval 2 --stagger 10 --url http://localhost:8000/recordings_stream` would randomly stagger 5 asynchronous emulated listeners across 10 seconds, with 2 seconds break between re-sending the file for each one.

You can now run example queries for InfluxDB under the Explore tab in Grafana. You should also be able to see your raw sound files showing up in your s3 bucket if you have uploading turned on. Additionally, you should see the various queues in my example dashboard start to increase. I have found that a PI5 can handle about 30 concurrent listeners.
## Using BalenaOS

1. Follow [these](https://docs.balena.io/learn/getting-started/raspberrypi5/python/) instructions to create a Balena Fleet in Balena Cloud and flash the OS onto your Raspberry Pi. **Make sure you toggle "Public Device URL" to ON.** 

2. Configure fleet (or device) variables on the Balena Dashboard. This is the same as creating the .env file in the Local Testing section above.

3. Navigate inside of this directroy (`aggegator-pi/`) in a terminal window. Then push this container to your Balena Fleet.
`balena push <fleet_name>`

A new volume is created automatically and mounted on /data/. All recordings and logs are saved in that directory. You should see metrics showing up in Prometheus. To view them, connect Grafana to Prometheus and create a Grafana dashboard. You may use the example json dashboard I have created. **Make sure to replace all references of "YOUR_UID_HERE" with whatever your Prometheus UID is in Grafana.** 

To actually test the container (on raw audio files), you have two options.

Option 1: Make `generate_mock_audio` true in settings. This turns on a very basic process that generates white noise locally in the container just to make sure that the other processes work.

Option 2: To fully test the container, use [mock listener scripts](https://github.com/atuecke/mock-listener) that I made. These are used to immitate multiple listeners, simultaneously streaming (or uploading) audio to an endpoint on the container. Make sure you have a mock audio .wav file saved to send. I would recommend downloading [this](https://xeno-canto.org/169082) and trimming it to 10-20 seconds. Run stress_test_stream.py with the following arguments:

`--file`: The path to the audio file you want to repeatedly send

`--num-sources`: The number of listeners you want to simulate sending **simultaneously**

`--interval`: The number seconds to wait between each send. For a full duty cycle, this would be zero

`--stagger`: The timespan to randomly stagger the emulated listeners. This stops all of them from sending at the exact same time, which is more realistic

`--url`: The endpoint to send to. In this case, it would be your device's public url copied from Balena Cloud dashboard. For 
example: `https://<your_device_uuid>.balena-devices.com/recordings_stream`.

For example: `python stress_test_stream.py --file ./wav_samples/sample.wav --num-sources 5 --interval 2 --stagger 10 --url http://<your_device_uuid>/recordings_stream` would randomly stagger 5 asynchronous emulated listeners across 10 seconds, with 2 seconds break between re-sending the file for each one.

You can now run example queries for InfluxDB under the Explore tab in Grafana. You should also be able to see your raw sound files showing up in your s3 bucket if you have uploading turned on. Additionally, you should see the various queues in my example dashboard start to increase. I have found that a PI5 can handle about 30 concurrent listeners.
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

### Setting up the s3 Bucket
There really isn't one way to do this. I use [Chameleon Cloud's](https://www.chameleoncloud.org/) Object Store, but you could just as easily use an Amazon s3 bucket. Just make sure that all credentials are in your environment variables.
