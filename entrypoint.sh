#!/bin/sh
# Render the real prometheus-agent.yml
envsubst '$PROM_SCRAPE_INTERVAL $KVM_IP $PROM_USER $PROM_PASS $AGGREGATOR_UUID' \
  < /etc/prom_agent/prometheus-agent.yml.tpl \
  > /etc/prom_agent/prometheus-agent.yml

# Now start supervisord (the CMD)
exec "$@"