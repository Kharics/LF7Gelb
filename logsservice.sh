#!/bin/bash

SERVICE_NAME="temperature_reader.service"

journalctl -u "$SERVICE_NAME" -f