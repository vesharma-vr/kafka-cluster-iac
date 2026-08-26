#!/bin/bash
set -euo pipefail

CLUSTER_ID="zsvUnJ-LSWW-5vb3PEALHQ"
KAFKA_DIST="kafka_2.13-4.3.1"
KAFKA_URL="https://downloads.apache.org/kafka/4.3.1/kafka_2.13-4.3.1.tgz"
INSTALL_DIR="/opt/kafka"
DATA_DIR="/var/lib/kafka/data"

# 1. Install Java + tools (no full dnf update; not needed and slows boot)
dnf install -y java-17-amazon-corretto-headless wget tar

# 2. Create system user "kafka" if it doesn't exist
if ! id -u kafka >/dev/null 2>&1; then
  useradd -r -m -d /home/kafka kafka
fi

# 3. Data dir
mkdir -p "${DATA_DIR}"
chown -R kafka:kafka "${DATA_DIR}"

# 4. Download + extract Kafka, arrange /opt/kafka as install root
if [ ! -d "${INSTALL_DIR}" ]; then
  cd /opt
  wget -q "${KAFKA_URL}" -O "/opt/${KAFKA_DIST}.tgz"
  tar -xzf "/opt/${KAFKA_DIST}.tgz" -C /opt
  mv "/opt/${KAFKA_DIST}" "${INSTALL_DIR}"
  rm -f "/opt/${KAFKA_DIST}.tgz"
fi
chown -R kafka:kafka "${INSTALL_DIR}"

# 5. Resolve this instance's own private IP via IMDSv2 (single node, so this is
# also the only value needed for both the controller quorum voter and the
# advertised listener)
TOKEN=$(curl -sS -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
OWN_PRIVATE_IP=$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/local-ipv4)

# 6. Write server.properties (single-node KRaft: this node is its own and only
# quorum voter, combined broker+controller roles)
cat > "${INSTALL_DIR}/config/server.properties" <<EOF
process.roles=broker,controller
node.id=1
controller.quorum.voters=1@${OWN_PRIVATE_IP}:9093
listeners=PLAINTEXT://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093
inter.broker.listener.name=PLAINTEXT
advertised.listeners=PLAINTEXT://${OWN_PRIVATE_IP}:9092
controller.listener.names=CONTROLLER
listener.security.protocol.map=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT
log.dirs=${DATA_DIR}
num.partitions=1
default.replication.factor=1
offsets.topic.replication.factor=1
transaction.state.log.replication.factor=1
transaction.state.log.min.isr=1
EOF
chown kafka:kafka "${INSTALL_DIR}/config/server.properties"

# 7. Format storage as the kafka user (idempotent across reboots)
sudo -u kafka "${INSTALL_DIR}/bin/kafka-storage.sh" format \
  --cluster-id "${CLUSTER_ID}" \
  --config "${INSTALL_DIR}/config/server.properties" \
  --ignore-formatted

# 8. systemd unit
cat > /etc/systemd/system/kafka.service <<EOF
[Unit]
Description=Apache Kafka (KRaft, single node)
After=network.target

[Service]
Type=simple
User=kafka
Environment=JAVA_HOME=/usr/lib/jvm/java-17-amazon-corretto
ExecStart=${INSTALL_DIR}/bin/kafka-server-start.sh ${INSTALL_DIR}/config/server.properties
Restart=on-failure
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

# 9. Enable + start
systemctl daemon-reload
systemctl enable --now kafka
