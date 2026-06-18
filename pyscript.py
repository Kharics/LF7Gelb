#!/usr/bin/env python3

import argparse
import glob
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

import lgpio
from prometheus_client import start_http_server, Gauge


# ============================================================
# Konfiguration
# ============================================================

PROMETHEUS_PORT = 8787
GPIO_CHIP = 0

# GPIO-Belegung
AMPEL_GRUEN_GPIO = 22
AMPEL_GELB_GPIO = 17
AMPEL_ROT_GPIO = 27
BEWEGUNG_GPIO = 26

# Temperaturgrenzen
TEMP_GELB_AB = 25.0
TEMP_ROT_AB = 27.0

# Messintervall Sensoren
INTERVAL_SECONDS = 2

# Standardordner
DEFAULT_LOG_DIR = "logs"
DEFAULT_AUFNAHME_DIR = "aufnahmen"

# Kamera
DEFAULT_CAPTURE_INTERVAL_SECONDS = 30


# ============================================================
# Globale Variablen
# ============================================================

stop = False
logging_enabled = False
current_log_file = None
last_logged_files = set()
last_capture_time = 0
camera_command = None


# ============================================================
# Prometheus Metriken
# ============================================================

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

kamera_aufnahme_success = Gauge(
    "projekt_kamera_aufnahme_success",
    "Letzte Kameraaufnahme erfolgreich: 1=ja, 0=nein"
)

kamera_aufnahme_total = Gauge(
    "projekt_kamera_aufnahme_total",
    "Anzahl erfolgreicher Kameraaufnahmen seit Programmstart"
)

kamera_last_capture_timestamp = Gauge(
    "projekt_kamera_last_capture_timestamp_seconds",
    "Unix-Zeitstempel der letzten erfolgreichen Kameraaufnahme"
)

kamera_last_file_size_bytes = Gauge(
    "projekt_kamera_last_file_size_bytes",
    "Dateigroesse der letzten Kameraaufnahme in Bytes"
)

kamera_aufnahme_files_total = Gauge(
    "projekt_kamera_aufnahme_files_total",
    "Anzahl der Bilddateien im Aufnahmeordner"
)


# ============================================================
# Argumente
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Raspberry Pi Sensor Exporter fuer Prometheus/Grafana mit Kamera"
    )

    parser.add_argument(
        "--log-dir",
        required=False,
        default=DEFAULT_LOG_DIR,
        help="Ordner fuer Logdateien. Standard: logs"
    )

    parser.add_argument(
        "--aufnahme-dir",
        required=False,
        default=DEFAULT_AUFNAHME_DIR,
        help="Ordner fuer Kameraaufnahmen. Standard: aufnahmen"
    )

    parser.add_argument(
        "--capture-interval",
        required=False,
        type=int,
        default=DEFAULT_CAPTURE_INTERVAL_SECONDS,
        help="Intervall fuer Kameraaufnahmen in Sekunden. Standard: 30"
    )

    parser.add_argument(
        "--disable-camera",
        action="store_true",
        help="Deaktiviert Kameraaufnahmen."
    )

    parser.add_argument(
        "--motion-active-low",
        action="store_true",
        help="Bewegung wird erkannt, wenn GPIO26 LOW ist."
    )

    parser.add_argument(
        "--motion-pull-up",
        action="store_true",
        help="Aktiviert internen Pull-Up fuer GPIO26."
    )

    parser.add_argument(
        "--motion-pull-down",
        action="store_true",
        help="Aktiviert internen Pull-Down fuer GPIO26."
    )

    return parser.parse_args()


# ============================================================
# Logging
# ============================================================

def setup_logging(log_dir):
    global logging_enabled
    global current_log_file

    logging_enabled = True
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

    log_info("Logging gestartet")
    log_info("Logdatei: %s", current_log_file)
    log_info("Logordner: %s", log_dir)


def log_info(message, *args):
    if logging_enabled:
        logging.info(message, *args)
        last_log_write_timestamp.set(time.time())
    else:
        if args:
            message = message % args
        print(message)


def log_error(message, *args):
    if logging_enabled:
        logging.error(message, *args)
        last_log_write_timestamp.set(time.time())
    else:
        if args:
            message = message % args
        print(f"FEHLER: {message}")


def update_log_metrics(log_dir):
    global last_logged_files

    log_files = []

    try:
        for entry in os.scandir(log_dir):
            if entry.is_file() and entry.name.endswith(".log"):
                log_files.append(entry)
    except FileNotFoundError:
        log_files_total.set(0)
        return

    log_files_total.set(len(log_files))

    current_files = set()

    for entry in log_files:
        filename = entry.name
        current_files.add(filename)

        stat = entry.stat()

        log_file_size_bytes.labels(filename=filename).set(stat.st_size)
        log_file_created_timestamp.labels(filename=filename).set(stat.st_ctime)

    removed_files = last_logged_files - current_files

    for filename in removed_files:
        try:
            log_file_size_bytes.remove(filename)
            log_file_created_timestamp.remove(filename)
        except KeyError:
            pass

    last_logged_files = current_files


# ============================================================
# Temperatur lesen
# ============================================================

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


# ============================================================
# GPIO Setup
# ============================================================

def setup_gpio(motion_pull_up=False, motion_pull_down=False):
    handle = lgpio.gpiochip_open(GPIO_CHIP)

    lgpio.gpio_claim_output(handle, AMPEL_GRUEN_GPIO, 0, 0)
    lgpio.gpio_claim_output(handle, AMPEL_GELB_GPIO, 0, 0)
    lgpio.gpio_claim_output(handle, AMPEL_ROT_GPIO, 0, 0)

    flags = 0

    if motion_pull_up and motion_pull_down:
        raise ValueError("Nutze entweder --motion-pull-up oder --motion-pull-down, nicht beide gleichzeitig.")

    if motion_pull_up:
        flags = lgpio.SET_PULL_UP
    elif motion_pull_down:
        flags = lgpio.SET_PULL_DOWN

    lgpio.gpio_claim_input(handle, BEWEGUNG_GPIO, flags)

    return handle


def all_lights_off(handle):
    lgpio.gpio_write(handle, AMPEL_GRUEN_GPIO, 0)
    lgpio.gpio_write(handle, AMPEL_GELB_GPIO, 0)
    lgpio.gpio_write(handle, AMPEL_ROT_GPIO, 0)


# ============================================================
# Ampel
# ============================================================

def calculate_ampel_stufe(temp_c):
    if temp_c > TEMP_ROT_AB:
        return 3
    elif temp_c >= TEMP_GELB_AB:
        return 2
    else:
        return 1


def set_ampel(handle, stufe):
    all_lights_off(handle)

    if stufe == 1:
        lgpio.gpio_write(handle, AMPEL_GRUEN_GPIO, 1)
    elif stufe == 2:
        lgpio.gpio_write(handle, AMPEL_GELB_GPIO, 1)
    elif stufe == 3:
        lgpio.gpio_write(handle, AMPEL_ROT_GPIO, 1)

    ampel_stufe.set(stufe)


# ============================================================
# Bewegungsmelder
# ============================================================

def read_motion(handle, motion_active_low):
    raw = lgpio.gpio_read(handle, BEWEGUNG_GPIO)

    bewegung_raw_gpio.set(raw)

    if motion_active_low:
        detected = raw == 0
    else:
        detected = raw == 1

    bewegung_erkannt.set(1 if detected else 0)

    return detected, raw


# ============================================================
# Stop-Logik
# ============================================================

def update_stop(temp_c, motion_detected):
    global stop

    old_stop = stop

    temp_stop = temp_c > TEMP_ROT_AB
    motion_stop = motion_detected

    stop = temp_stop or motion_stop

    stop_status.set(1 if stop else 0)
    stop_changed.set(1 if old_stop != stop else 0)

    return temp_stop, motion_stop


# ============================================================
# Kamera
# ============================================================

def find_camera_command():
    if shutil.which("rpicam-still"):
        return "rpicam-still"

    if shutil.which("libcamera-still"):
        return "libcamera-still"

    return None


def setup_camera(aufnahme_dir, camera_disabled):
    global camera_command

    os.makedirs(aufnahme_dir, exist_ok=True)

    if camera_disabled:
        camera_command = None
        kamera_aufnahme_success.set(0)
        log_info("Kamera deaktiviert durch --disable-camera")
        return

    camera_command = find_camera_command()

    if camera_command is None:
        kamera_aufnahme_success.set(0)
        log_error("Kein Kamera-Befehl gefunden. Erwartet: rpicam-still oder libcamera-still")
        return

    log_info("Kamera-Befehl gefunden: %s", camera_command)
    log_info("Aufnahmeordner: %s", aufnahme_dir)


def update_aufnahme_metrics(aufnahme_dir):
    count = 0

    try:
        for entry in os.scandir(aufnahme_dir):
            if entry.is_file() and entry.name.lower().endswith((".jpg", ".jpeg", ".png")):
                count += 1
    except FileNotFoundError:
        count = 0

    kamera_aufnahme_files_total.set(count)


def capture_image_if_due(aufnahme_dir, capture_interval):
    global last_capture_time

    if camera_command is None:
        update_aufnahme_metrics(aufnahme_dir)
        return None

    now = time.time()

    if now - last_capture_time < capture_interval:
        update_aufnahme_metrics(aufnahme_dir)
        return None

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    image_path = os.path.join(aufnahme_dir, f"aufnahme_{timestamp}.jpg")

    command = [
        camera_command,
        "-o",
        image_path,
        "--nopreview",
        "-t",
        "1000"
    ]

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15
        )

        if result.returncode != 0:
            kamera_aufnahme_success.set(0)
            log_error("Kameraaufnahme fehlgeschlagen: %s", result.stderr.strip())
            update_aufnahme_metrics(aufnahme_dir)
            return None

        if not os.path.isfile(image_path):
            kamera_aufnahme_success.set(0)
            log_error("Kameraaufnahme fehlgeschlagen: Datei wurde nicht erstellt: %s", image_path)
            update_aufnahme_metrics(aufnahme_dir)
            return None

        file_size = os.path.getsize(image_path)

        last_capture_time = now
        kamera_aufnahme_success.set(1)
        kamera_aufnahme_total.inc()
        kamera_last_capture_timestamp.set(now)
        kamera_last_file_size_bytes.set(file_size)

        update_aufnahme_metrics(aufnahme_dir)

        log_info("Kameraaufnahme gespeichert: %s | Groesse=%s Bytes", image_path, file_size)
        return image_path

    except subprocess.TimeoutExpired:
        kamera_aufnahme_success.set(0)
        log_error("Kameraaufnahme Timeout nach 15 Sekunden")
        update_aufnahme_metrics(aufnahme_dir)
        return None

    except Exception as error:
        kamera_aufnahme_success.set(0)
        log_error("Kameraaufnahme Fehler: %s", error)
        update_aufnahme_metrics(aufnahme_dir)
        return None


# ============================================================
# Main
# ============================================================

def main():
    args = parse_args()

    setup_logging(args.log_dir)

    log_info("Starte Prometheus Exporter auf Port %s", PROMETHEUS_PORT)
    log_info("Messintervall Sensoren: %s Sekunden", INTERVAL_SECONDS)
    log_info("Kamera-Aufnahmeintervall: %s Sekunden", args.capture_interval)
    log_info("Logordner: %s", args.log_dir)
    log_info("Aufnahmeordner: %s", args.aufnahme_dir)

    log_info("Bewegungsmelder GPIO: GPIO%s", BEWEGUNG_GPIO)
    log_info("Bewegung aktiv bei: %s", "LOW" if args.motion_active_low else "HIGH")

    if args.motion_pull_up:
        log_info("GPIO26 Pull-Modus: Pull-Up")
    elif args.motion_pull_down:
        log_info("GPIO26 Pull-Modus: Pull-Down")
    else:
        log_info("GPIO26 Pull-Modus: Kein interner Pull")

    start_http_server(PROMETHEUS_PORT)
    log_info("Metrics erreichbar unter: http://localhost:%s/metrics", PROMETHEUS_PORT)

    temperature_file = find_temperature_file()
    log_info("Temperaturdatei gefunden: %s", temperature_file)

    handle = setup_gpio(
        motion_pull_up=args.motion_pull_up,
        motion_pull_down=args.motion_pull_down
    )

    setup_camera(
        aufnahme_dir=args.aufnahme_dir,
        camera_disabled=args.disable_camera
    )

    log_info("GPIO-Belegung:")
    log_info("  Ampel Gruen: GPIO%s", AMPEL_GRUEN_GPIO)
    log_info("  Ampel Gelb : GPIO%s", AMPEL_GELB_GPIO)
    log_info("  Ampel Rot  : GPIO%s", AMPEL_ROT_GPIO)
    log_info("  Bewegung   : GPIO%s", BEWEGUNG_GPIO)

    try:
        while True:
            try:
                temp_c = read_temperature_celsius(temperature_file)
                temperature_celsius.set(temp_c)
                temperature_read_success.set(1)

                motion_detected, motion_raw = read_motion(
                    handle,
                    motion_active_low=args.motion_active_low
                )

                stufe = calculate_ampel_stufe(temp_c)
                set_ampel(handle, stufe)

                temp_stop, motion_stop = update_stop(temp_c, motion_detected)

                capture_image_if_due(
                    aufnahme_dir=args.aufnahme_dir,
                    capture_interval=args.capture_interval
                )

                now = time.time()
                last_update_timestamp.set(now)

                update_log_metrics(args.log_dir)

                log_info(
                    "Temp=%.3f C | Ampel=%s | BewegungRaw=%s | BewegungDetected=%s | TempStop=%s | MotionStop=%s | Stop=%s",
                    temp_c,
                    stufe,
                    motion_raw,
                    motion_detected,
                    temp_stop,
                    motion_stop,
                    stop
                )

            except Exception as error:
                temperature_read_success.set(0)
                update_log_metrics(args.log_dir)
                update_aufnahme_metrics(args.aufnahme_dir)
                log_error("Fehler im Messzyklus: %s", error)

            time.sleep(INTERVAL_SECONDS)

    except KeyboardInterrupt:
        log_info("Programm beendet durch KeyboardInterrupt.")

    finally:
        try:
            all_lights_off(handle)
            lgpio.gpiochip_close(handle)
            log_info("GPIO geschlossen. Ampel ausgeschaltet.")
        except Exception as error:
            log_error("Fehler beim GPIO-Cleanup: %s", error)

        log_info("Programm beendet.")


if __name__ == "__main__":
    main()