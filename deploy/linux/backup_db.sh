#!/bin/sh
# Daily snapshot of the warehouse.
#
# Nothing in this system ever deletes sales history, which makes the database
# the only copy of it. This writes one compressed pg_dump per day and keeps the
# last $KEEP_DAYS of them.
#
#   deploy/linux/backup_db.sh                 run once by hand
#   restore:  pg_restore --clean --if-exists -d "<DATABASE_URL>" <file>.dump
#
# Scheduled by sales-analytics-backup.timer. Copy the backup directory off this
# server too -- a backup on the same disk does not survive losing the disk.
set -eu

APP_DIR=${APP_DIR:-/opt/sales-analytics}
BACKUP_DIR=${BACKUP_DIR:-/var/backups/sales-analytics}
KEEP_DAYS=${KEEP_DAYS:-30}

# Same source of truth the app uses. `schema=` is Prisma's and libpq rejects
# the whole URL over it, so it is stripped exactly as ingest.database_url does.
URL=$(sed -n 's/^[[:space:]]*DATABASE_URL[[:space:]]*=[[:space:]]*//p' "$APP_DIR/db/.env" \
      | head -n 1 | tr -d "\"'" | sed -e 's/[?&]schema=[^&]*//')
if [ -z "$URL" ]; then
    echo "No DATABASE_URL in $APP_DIR/db/.env" >&2
    exit 1
fi

umask 077                                  # dumps hold every sale: owner only
mkdir -p "$BACKUP_DIR"
OUT="$BACKUP_DIR/sales-$(date +%Y-%m-%d_%H%M).dump"

pg_dump --format=custom --no-owner --file="$OUT.partial" "$URL"
mv "$OUT.partial" "$OUT"                   # a half-written dump never looks complete
echo "Wrote $OUT ($(du -h "$OUT" | cut -f1))"

find "$BACKUP_DIR" -name 'sales-*.dump' -type f -mtime +"$KEEP_DAYS" -print -delete
