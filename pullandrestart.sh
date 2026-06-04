#!/bin/bash

set -e

REPO_URL="https://github.com/Kharics/LF7Gelb"
REPO_DIR="/home/ric/LF7Gelb"
SERVICE_NAME="temperature_reader.service"

echo "=== LF7Gelb Update Script ==="

if [ ! -d "$REPO_DIR/.git" ]; then
    echo "Repository nicht gefunden. Klone nach $REPO_DIR ..."
    git clone "$REPO_URL" "$REPO_DIR"
else
    echo "Repository gefunden. Pull wird ausgeführt ..."
    cd "$REPO_DIR"
    git pull
fi

echo
echo "Service wird neu gestartet: $SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo
echo "Service Status:"
sudo systemctl status "$SERVICE_NAME" --no-pager

echo
echo "Update abgeschlossen."