#!/usr/bin/env bash
# Swap between standard and sharded MongoDB topologies for S06/S07-sharded/S14-V14c.
#
# Usage:
#   ./topology-swap.sh up standard       # bring up standard topology
#   ./topology-swap.sh up sharded        # tear down standard, bring up sharded
#   ./topology-swap.sh down              # tear down whatever's up
#   ./topology-swap.sh which             # report which topology is active

set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STANDARD_COMPOSE="$INFRA_DIR/compose.standard.yaml"
SHARDED_COMPOSE="$INFRA_DIR/compose.sharded.yaml"

current_topology() {
    if docker ps --filter "name=mongo-bench-shard" --format '{{.Names}}' | grep -q .; then
        echo "sharded"
    elif docker ps --filter "name=mongo-bench" --format '{{.Names}}' | grep -q '^mongo-bench$'; then
        echo "standard"
    else
        echo "none"
    fi
}

case "${1:-}" in
  up)
    target="${2:-}"
    [[ -n "$target" ]] || { echo "usage: $0 up <standard|sharded>" >&2; exit 1; }
    current="$(current_topology)"
    if [[ "$current" == "$target" ]]; then
        echo "topology already $target"
        exit 0
    fi
    if [[ "$current" != "none" ]]; then
        echo "tearing down $current topology"
        case "$current" in
          standard) docker compose -f "$STANDARD_COMPOSE" down -v ;;
          sharded)  docker compose -f "$SHARDED_COMPOSE"  down -v ;;
        esac
    fi
    echo "bringing up $target topology"
    case "$target" in
      standard) docker compose -f "$STANDARD_COMPOSE" up -d ;;
      sharded)  docker compose -f "$SHARDED_COMPOSE"  up -d ;;
      *) echo "unknown topology: $target" >&2; exit 1 ;;
    esac
    ;;

  down)
    current="$(current_topology)"
    case "$current" in
      standard) docker compose -f "$STANDARD_COMPOSE" down -v ;;
      sharded)  docker compose -f "$SHARDED_COMPOSE"  down -v ;;
      none)     echo "no topology up; nothing to do" ;;
    esac
    ;;

  which)
    current_topology
    ;;

  *)
    echo "usage: $0 {up <standard|sharded>|down|which}" >&2
    exit 1
    ;;
esac
