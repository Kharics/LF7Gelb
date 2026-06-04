#!/bin/bash

SERVICE_NAME="temperature_reader.service"

sudo systemctl status "$SERVICE_NAME" --no-pager