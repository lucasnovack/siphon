#!/usr/bin/env bash
set -euo pipefail

COMPOSE="docker compose -f $(dirname "$0")/docker-compose.yml"

usage() {
  cat <<EOF
Usage: ./testenv/mysql.sh <command>

Commands:
  start     Start all test services (MySQL + MinIO)
  stop      Stop all containers (data volumes preserved)
  destroy   Stop and remove containers + volumes (all data lost)
  status    Show container status
  shell     Open a MySQL shell as siphon user
  logs      Follow logs for all services

MySQL connection string for Siphon:
  mysql://siphon:siphon@host.docker.internal:3306/testdb

MinIO connection for Siphon:
  Endpoint:   host.docker.internal:9000
  Access key: minioadmin
  Secret key: minioadmin
  Console UI: http://localhost:9001
EOF
}

wait_healthy() {
  local service=$1
  echo -n "Waiting for $service"
  for _ in $(seq 1 30); do
    if $COMPOSE ps "$service" 2>/dev/null | grep -q "healthy"; then
      echo " ready."
      return 0
    fi
    echo -n "."
    sleep 2
  done
  echo ""
  echo "$service did not become healthy in time. Check: ./testenv/mysql.sh logs"
  exit 1
}

case "${1:-}" in
  start)
    echo "Starting test services (MySQL + MinIO)..."
    $COMPOSE up -d
    wait_healthy mysql
    wait_healthy minio
    echo ""
    echo "MySQL:  mysql://siphon:siphon@host.docker.internal:3306/testdb"
    echo "MinIO:  endpoint=host.docker.internal:9000  key=minioadmin  secret=minioadmin"
    echo "MinIO console: http://localhost:9001"
    ;;
  stop)
    echo "Stopping test services (data volumes preserved)..."
    $COMPOSE down
    echo "Done. Run 'start' to bring them back."
    ;;
  destroy)
    read -rp "This will delete all test data (MySQL + MinIO). Continue? [y/N] " confirm
    [[ "$confirm" == [yY] ]] || exit 0
    echo "Destroying containers and volumes..."
    $COMPOSE down -v
    echo "Done."
    ;;
  status)
    $COMPOSE ps
    ;;
  shell)
    $COMPOSE exec mysql mysql -usiphon -psiphon testdb
    ;;
  logs)
    $COMPOSE logs -f
    ;;
  *)
    usage
    exit 1
    ;;
esac
