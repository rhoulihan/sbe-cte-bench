#!/bin/bash
# Install MongoDB 8.0 with systemd cgroup caps matching ADB Always Free
# (1 OCPU = 2 vCPU equivalent, ~3 GB memory envelope).
#
# Tested on Oracle Linux 9 / RHEL 9 / Rocky 9. Adjust the repo path for
# other distros.
set -euo pipefail

if [[ "$EUID" -ne 0 ]] && ! sudo -n true 2>/dev/null; then
    echo "ERROR: this script needs sudo. Run as root or with passwordless sudo." >&2
    exit 1
fi

echo "=== Adding MongoDB 8.0 yum repo ==="
sudo tee /etc/yum.repos.d/mongodb-org-8.0.repo > /dev/null <<'EOF'
[mongodb-org-8.0]
name=MongoDB Repository
baseurl=https://repo.mongodb.org/yum/redhat/9/mongodb-org/8.0/x86_64/
gpgcheck=1
enabled=1
gpgkey=https://pgp.mongodb.com/server-8.0.asc
EOF

echo "=== Installing mongodb-org + mongosh ==="
sudo dnf install -y mongodb-org mongodb-mongosh

echo "=== Configuring /etc/mongod.conf ==="
sudo tee /etc/mongod.conf > /dev/null <<'EOF'
storage:
  dbPath: /var/lib/mongo
  wiredTiger:
    engineConfig:
      cacheSizeGB: 1.5
      journalCompressor: snappy

systemLog:
  destination: file
  logAppend: true
  path: /var/log/mongodb/mongod.log

net:
  port: 27017
  bindIp: 127.0.0.1

processManagement:
  timeZoneInfo: /usr/share/zoneinfo

replication:
  replSetName: rs0
  enableMajorityReadConcern: true

setParameter:
  internalQueryFrameworkControl: trySbeEngine
EOF

echo "=== Applying systemd cgroup caps (CPUQuota=200%, MemoryMax=3G) ==="
sudo mkdir -p /etc/systemd/system/mongod.service.d
sudo tee /etc/systemd/system/mongod.service.d/limits.conf > /dev/null <<'EOF'
[Service]
# Match ADB Always Free: 1 OCPU = 2 vCPU equivalent, ~3 GB memory envelope.
CPUQuota=200%
MemoryMax=3G
MemoryHigh=2.7G
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now mongod

echo "=== Waiting for mongod to start ==="
for _ in $(seq 1 20); do
    if mongosh --quiet --eval 'db.runCommand({ping:1}).ok' >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

echo "=== Initializing single-node replica set rs0 ==="
mongosh --quiet --eval 'try {
  rs.initiate({_id:"rs0", members:[{_id:0, host:"127.0.0.1:27017"}]});
} catch(e) { print("init: " + e.message); }'

echo "=== Waiting for primary election ==="
for _ in $(seq 1 30); do
    state=$(mongosh --quiet --eval 'rs.status().myState' 2>/dev/null)
    if [[ "$state" == "1" ]]; then break; fi
    sleep 1
done

echo
echo "=== mongod status ==="
sudo systemctl --no-pager status mongod | head -12
echo
echo "=== verify ==="
mongosh --quiet --eval 'print("host=" + db.serverStatus().host
  + " ver=" + db.serverStatus().version
  + " replSet=" + (db.serverStatus().repl ? db.serverStatus().repl.setName : "none")
  + " primary=" + (rs.status().myState === 1));'
echo
echo "DONE. Mongo capped at 2 vCPU / 3 GB, journaling on, SBE on by default."
