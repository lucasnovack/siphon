#!/usr/bin/env bash
set -euo pipefail

COMPOSE="docker compose"

usage() {
  cat <<EOF
Usage: ./siphon.sh <command>

Commands:
  start       Build (if needed) and start all services in background
  stop        Stop all services
  restart     Restart all services
  logs        Follow logs for siphon (Ctrl+C to exit)
  status      Show container status
  reset       Stop and remove containers, volumes and images (destructive)
EOF
}

case "${1:-}" in
  start)
    echo "Starting Siphon..."
    $COMPOSE up -d --build
    echo ""
    echo "Services:"
    $COMPOSE ps
    echo ""
    echo "UI: http://localhost:8000"
    echo "MinIO: http://localhost:9001  (minioadmin / minioadmin)"
    ;;
  stop)
    $COMPOSE down
    ;;
  restart)
    $COMPOSE down
    $COMPOSE up -d --build
    ;;
  logs)
    $COMPOSE logs -f siphon
    ;;
  status)
    $COMPOSE ps
    ;;
  reset)
    read -rp "This will delete all data. Continue? [y/N] " confirm
    [[ "$confirm" == [yY] ]] || exit 0
    $COMPOSE down -v --rmi local
    ;;
  *)
    usage
    exit 1
    ;;
esac
