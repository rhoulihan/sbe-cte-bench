#!/usr/bin/env bash
# Entrypoint for the mongo-bench-shard1-router container.
# Brings up cfgsvr + shard1 + mongos via supervisord, then initiates the
# replica sets idempotently.
set -euo pipefail

# Wait for a port to accept TCP connections.
wait_for_port() {
    local port="$1"
    local timeout="${2:-60}"
    local elapsed=0
    while ! mongosh --quiet --port "$port" --eval 'db.runCommand({ping:1}).ok' >/dev/null 2>&1; do
        if [[ "$elapsed" -ge "$timeout" ]]; then
            echo "timeout waiting for port $port" >&2
            return 1
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
}

# Initiate a replica set if not already initialized. Idempotent.
init_rs() {
    local port="$1"
    local rs_name="$2"
    local member_host="$3"
    local extra="${4:-}"

    if mongosh --quiet --port "$port" --eval 'rs.status().ok' >/dev/null 2>&1; then
        return 0
    fi

    mongosh --quiet --port "$port" --eval "rs.initiate({_id:'$rs_name',$extra members:[{_id:0, host:'$member_host'}]})"
}

# Start supervisord in the background.
/usr/bin/supervisord -c /etc/supervisor/conf.d/sbe-cte-bench.conf &
SUPERVISOR_PID=$!

# Trap so we forward signals.
trap "kill -TERM $SUPERVISOR_PID; wait $SUPERVISOR_PID" TERM INT

# Wait for cfgsvr (port 27019).
wait_for_port 27019 90

# Initialize cfgsvr replica set.
init_rs 27019 cfgRS "$(hostname):27019" "configsvr:true,"

# Wait for shard1 (port 27018).
wait_for_port 27018 90

# Initialize shard1 replica set.
init_rs 27018 shard1 "$(hostname):27018"

# Wait for mongos (port 27017).
wait_for_port 27017 90

echo "shard router topology ready"
wait $SUPERVISOR_PID
