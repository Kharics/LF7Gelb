#!/usr/bin/env python3

import argparse
import glob
import logging
import os
import sys
import time
from datetime import datetime

from prometheus_client import start_http_server, Gauge

from gpiozero import LED, DigitalInputDevice, Device
from gpiozero.pins.lgpio import LGPIOFactory


# =========================
# GPIO Backend
# =========================

Device.pin_factory = LGPIOFactory(chip=0)


# =========================
# Prometheus
# =========================

PROMETHEUS_PORT = 8788


# =========================
# GPIO-Belegung
# =========================
# GPIO22 = Grün
# GPIO17 = Gelb
# GPIO27 = Rot
# GPIO26 = Bewegungsmelder

AMPEL_GRUEN_GPIO = 22
AMPEL_GELB_GPIO = 17
AMPEL_ROT_GPIO = 27

BEWEGUNG_GPIO = 26

# True  = GPIO HIGH bedeutet Bewegung erkannt
# False = GPIO LOW bedeutet Bewegung erkannt
BEWEGUNG_ACTIVE_HIGH = True


# =========================
# Temperaturgrenzen
# =========================

TEMP_GELB_AB = 25.0
TEMP_ROT_AB = 27.0


# =========================
# Globale Variablen
# =========================

stop = False
current_log_file = None
last_logged_files = set()


# =========================
# Prometheus-Metriken
# =========================

temperature_celsius = Gauge(
    "projekt_temperature_celsius",
    "Aktuelle Temperatur in Grad Celsius"
)

temperature_read_success = Gauge(
    "projekt_temperature_read_success",
    "Temperatur erfolgreich gelesen: 1=ja, 0=nein"
)

ampel_stufe = Gauge(
    "projekt_ampel_stufe",
    "Ampelstufe: 1=gruen, 2=gelb, 3=rot"
)

bewegung_erkannt = Gauge(
    "projekt_bewegung_erkannt",
    "Bewegungsmelder: 1=Bewegung erkannt, 0=keine Bewegung"
)

bewegung_raw_gpio = Gauge(
    "projekt_bewegung_raw_gpio",
    "Rohwert GPIO26: 1=HIGH, 0=LOW"
)

stop_status = Gauge(
    "projekt_stop_status",
    "Globale Stop-Variable: 1=true/stop, 0=false/run"
)

stop_changed = Gauge(
    "projekt_stop_changed",
    "Stop-Variable hat sich im letzten Zyklus geaendert: 1=ja, 0=nein"
)

last_update_timestamp = Gauge(
    "projekt_last_update_timestamp_seconds",
    "Unix-Zeitstempel der letzten erfolgreichen Aktualisierung"
)

log_files_total = Gauge(
    "projekt_log_files_total",
    "Anzahl der Logdateien im konfigurierten Logordner"
)

log_file_size_bytes = Gauge(
    "projekt_log_file_size_bytes",
    "Groesse einzelner Logdateien in Bytes",
    ["filename"]
)

log_file_created_timestamp = Gauge(
    "projekt_log_file_created_timestamp_seconds",
    "Erstellungs-/ctime-Zeitstempel einzelner Logdateien",
    ["filename"]
)

current_log_created_timestamp = Gauge(
    "projekt_current_log_created_timestamp_seconds",
    "Unix-Zeitstempel, wann die aktuelle Logdatei erstellt wurde"
)

last_log_write_timestamp = Gauge(
    "projekt_last_log_write_timestamp_seconds",
    "Unix-Zeitstempel des letzten Logeintrags"
)


# =========================
# Logging
# =========================

def setup_logging(log_dir):
    global current_log_file

    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    current_log_file = os.path.join(log_dir, f"sensor_exporter_{timestamp}.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(current_log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ]
    )

    created_at = time.time()
    current_log_created_timestamp.set(created_at)
    last_log_write_timestamp.set(created_at)

    logging.info("Logging gestartet")
    logging.info("Logdatei: %s", current_log_file)
    logging.info("Logordner: %s", log_dir)


def log_info(message, *args):
    logging.info(message, *args)
    last_log_write_timestamp.set(time.time())


def log_error(message, *args):
    logging.error(message, *args)
    last_log_write_timestamp.set(time.time())


def update_log_metrics(log_dir):
    global last_logged_files

    log_files = []

    for entry in os.scandir(log_dir):
        if entry.is_file() and entry.name.endswith(".log"):
            log_files.append(entry)

    log_files_total.set(len(log_files))

    current_files = set()

    for entry in log_files:
        filename = entry.name
        current_files.add(filename)

        stat = entry.stat()

        log_file_size_bytes.labels(filename=filename).set(stat.st_size)
        log_file_created_timestamp.labels(filename=filename).set(stat.st_ctime)

    # Entfernt alte Labels, falls Logdateien gelöscht wurden
    removed_files = last_logged_files - current_files

    for filename in removed_files:
        try:
            log_file_size_bytes.remove(filename)
            log_file_created_timestamp.remove(filename)
        except KeyError:
            pass

    last_logged_files = current_files


# =========================
# Temperatur lesen
# =========================

def find_temperature_file():
    possible_patterns = [
        "/sys/bus/w1/devices/28-*/w1_slave",
        "/sys/devices/w1_bus_master1/28-*/w1_slave",
        "/sys/devices/w1_bus_master1/28-*/temperature",
        "/sys/devices/w1_bus_master1/28-*/temperatur",
    ]

    for pattern in possible_patterns:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]

    raise FileNotFoundError(
        "Kein DS18B20 Temperatursensor gefunden. "
        "Pruefe One-Wire, Verkabelung und Pull-Up-Widerstand."
    )


def read_temperature_celsius(path):
    with open(path, "r", encoding="utf-8") as file:
        content = file.read().strip()

    if "t=" in content:
        lines = content.splitlines()

        if lines and "YES" not in lines[0]:
            raise RuntimeError("DS18B20 CRC-Check fehlgeschlagen.")

        raw_value = content.split("t=")[1].split()[0]
        return float(raw_value) / 1000.0

    value = float(content.split()[0])

    if abs(value) > 200:
        return value / 1000.0

    return value


# =========================
# Ampel
# =========================

def calculate_ampel_stufe(temp_c):
    if temp_c > TEMP_ROT_AB:
        return 3
    elif temp_c >= TEMP_GELB_AB:
        return 2
    else:
        return 1


def set_ampel(stufe, green, yellow, red):
    green.off()
    yellow.off()
    red.off()

    if stufe == 1:
        green.on()
    elif stufe == 2:
        yellow.on()
    elif stufe == 3:
        red.on()

    ampel_stufe.set(stufe)


# =========================
# Bewegungsmelder
# =========================

def read_motion(sensor):
    raw = sensor.value
    bewegung_raw_gpio.set(raw)

    if BEWEGUNG_ACTIVE_HIGH:
        detected = raw == 1
    else:
        detected = raw == 0

    bewegung_erkannt.set(1 if detected else 0)
    return detected


# =========================
# Stop-Logik
# =========================

def update_stop(temp_c, motion_detected):
    global stop

    old_stop = stop

    temp_stop = temp_c > TEMP_ROT_AB
    motion_stop = motion_detected

    stop = temp_stop or motion_stop

    stop_status.set(1 if stop else 0)
    stop_changed.set(1 if old_stop != stop else 0)


# =========================
# Argumente
# =========================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Raspberry Pi Sensor Exporter fuer Prometheus/Grafana"
    )

    parser.add_argument(
        "--log-dir",
        required=True,
        help="Ordner, in dem Logdateien gespeichert werden sollen"
    )

    return parser.parse_args()


# =========================
# Main
# =========================

def main():
    args = parse_args()

    setup_logging(args.log_dir)

    log_info("Starte Prometheus Exporter auf Port %s", PROMETHEUS_PORT)
    start_http_server(PROMETHEUS_PORT)

    temperature_file = find_temperature_file()
    log_info("Temperaturdatei gefunden: %s", temperature_file)

    green = LED(AMPEL_GRUEN_GPIO)
    yellow = LED(AMPEL_GELB_GPIO)
    red = LED(AMPEL_ROT_GPIO)

    motion_sensor = DigitalInputDevice(
        BEWEGUNG_GPIO,
        pull_up=None,
        active_state=True,
        bounce_time=0.05
    )

    log_info("GPIO-Belegung:")
    log_info("  Ampel Gruen: GPIO%s", AMPEL_GRUEN_GPIO)
    log_info("  Ampel Gelb : GPIO%s", AMPEL_GELB_GPIO)
    log_info("  Ampel Rot  : GPIO%s", AMPEL_ROT_GPIO)
    log_info("  Bewegung   : GPIO%s", BEWEGUNG_GPIO)
    log_info("Metrics: http://localhost:%s/metrics", PROMETHEUS_PORT)

    try:
        while True:
            try:
                temp_c = read_temperature_celsius(temperature_file)
                temperature_celsius.set(temp_c)
                temperature_read_success.set(1)

                motion_detected = read_motion(motion_sensor)

                stufe = calculate_ampel_stufe(temp_c)
                set_ampel(stufe, green, yellow, red)

                update_stop(temp_c, motion_detected)

                last_update_timestamp.set(time.time())

                update_log_metrics(args.log_dir)

                log_info(
                    "Temp=%.3f C | Ampel=%s | Bewegung=%s | Stop=%s",
                    temp_c,
                    stufe,
                    motion_detected,
                    stop
                )

            except Exception as error:
                temperature_read_success.set(0)
                update_log_metrics(args.log_dir)
                log_error("Fehler: %s", error)

            time.sleep(2)

    except KeyboardInterrupt:
        log_info("Programm beendet durch KeyboardInterrupt.")

    finally:
        green.off()
        yellow.off()
        red.off()

        green.close()
        yellow.close()
        red.close()
        motion_sensor.close()

        log_info("GPIO geschlossen. Programm beendet.")


if __name__ == "__main__":
    main()