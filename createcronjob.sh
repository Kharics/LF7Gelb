#!/bin/bash

set -e

REPO_DIR="/home/ric/LF7Gelb"
LOG_DIR="$REPO_DIR/logs"
BACKUP_DIR="$REPO_DIR/log_backups"
BACKUP_SCRIPT="$REPO_DIR/backup_logs.sh"
BACKUP_LOG="$BACKUP_DIR/backup_cron.log"

echo "=== Log Backup Cron Setup ==="

mkdir -p "$LOG_DIR"
mkdir -p "$BACKUP_DIR"

echo "Erstelle Backup-Skript: $BACKUP_SCRIPT"

cat > "$BACKUP_SCRIPT" << 'EOF'
#!/bin/bash

REPO_DIR="/home/ric/LF7Gelb"
LOG_DIR="$REPO_DIR/logs"
BACKUP_DIR="$REPO_DIR/log_backups"
BACKUP_LOG="$BACKUP_DIR/backup_cron.log"

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
BACKUP_FILE="$BACKUP_DIR/logs_backup_$TIMESTAMP.tar.gz"

mkdir -p "$BACKUP_DIR"

echo "[$(date +"%Y-%m-%d %H:%M:%S")] Backup gestartet" >> "$BACKUP_LOG"

if [ ! -d "$LOG_DIR" ]; then
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] FEHLER: Log-Ordner existiert nicht: $LOG_DIR" >> "$BACKUP_LOG"
    exit 1
fi

LOG_COUNT=$(find "$LOG_DIR" -type f | wc -l)
LOG_SIZE=$(du -sh "$LOG_DIR" | awk '{print $1}')

echo "[$(date +"%Y-%m-%d %H:%M:%S")] Log-Dateien: $LOG_COUNT" >> "$BACKUP_LOG"
echo "[$(date +"%Y-%m-%d %H:%M:%S")] Log-Ordner-Größe: $LOG_SIZE" >> "$BACKUP_LOG"

if [ "$LOG_COUNT" -eq 0 ]; then
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] Keine Logs vorhanden, Backup übersprungen" >> "$BACKUP_LOG"
    exit 0
fi

tar -czf "$BACKUP_FILE" -C "$REPO_DIR" logs

if [ -f "$BACKUP_FILE" ]; then
    BACKUP_SIZE=$(du -h "$BACKUP_FILE" | awk '{print $1}')
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] Backup erstellt: $BACKUP_FILE Größe: $BACKUP_SIZE" >> "$BACKUP_LOG"
else
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] FEHLER: Backup konnte nicht erstellt werden" >> "$BACKUP_LOG"
    exit 1
fi

# Optional: Backups löschen, die älter als 7 Tage sind
find "$BACKUP_DIR" -name "logs_backup_*.tar.gz" -type f -mtime +7 -delete

echo "[$(date +"%Y-%m-%d %H:%M:%S")] Backup beendet" >> "$BACKUP_LOG"
echo "----------------------------------------" >> "$BACKUP_LOG"
EOF

chmod +x "$BACKUP_SCRIPT"

echo "Backup-Skript ausführbar gemacht."

CRON_JOB="*/30 * * * * $BACKUP_SCRIPT"

echo "Installiere Cronjob: $CRON_JOB"

(crontab -l 2>/dev/null | grep -v "$BACKUP_SCRIPT" ; echo "$CRON_JOB") | crontab -

echo
echo "Fertig."
echo "Cronjob läuft alle 30 Minuten."
echo "Backup-Skript: $BACKUP_SCRIPT"
echo "Log-Quelle: $LOG_DIR"
echo "Backup-Ziel: $BACKUP_DIR"
echo "Backup-Log: $BACKUP_LOG"