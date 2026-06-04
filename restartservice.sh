#!/bin/bash

SERVICE_NAME="temperature_reader.service"

echo "Restarting $SERVICE_NAME ..."
sudo systemctl restart "$SERVICE_NAME"

echo
echo "Status:"
sudo systemctl status "$SERVICE_NAME" --no-pager