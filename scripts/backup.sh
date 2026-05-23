#!/bin/bash
# FireworkRouter 每日备份脚本
# 推荐配 cron: 0 3 * * * /opt/FireworksRouter/scripts/backup.sh >> /var/log/fwr-backup.log 2>&1

set -e

INSTALL_DIR="${INSTALL_DIR:-/opt/FireworksRouter}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/fireworkrouter}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

mkdir -p "$BACKUP_DIR"
DATE=$(date +%Y%m%d-%H%M%S)

cd "$INSTALL_DIR"

# ============== 1. Postgres dump ==============
if docker compose ps postgres --status running -q | grep -q .; then
    echo "[backup] dumping postgres..."
    docker compose exec -T postgres pg_dump -U fwr fwr \
        | gzip > "$BACKUP_DIR/db-${DATE}.sql.gz"
    echo "  → $BACKUP_DIR/db-${DATE}.sql.gz ($(du -h "$BACKUP_DIR/db-${DATE}.sql.gz" | cut -f1))"
elif [ -f data/fireworkrouter.db ]; then
    echo "[backup] copying sqlite..."
    # SQLite WAL 模式下要用 .backup 命令保证一致性
    docker compose exec -T api sqlite3 /app/data/fireworkrouter.db ".backup '/tmp/backup.db'" 2>/dev/null \
        || cp data/fireworkrouter.db "/tmp/sqlite-${DATE}.db"
    docker compose exec -T api cat /tmp/backup.db 2>/dev/null > "$BACKUP_DIR/sqlite-${DATE}.db" \
        || cp "/tmp/sqlite-${DATE}.db" "$BACKUP_DIR/sqlite-${DATE}.db"
    gzip "$BACKUP_DIR/sqlite-${DATE}.db"
fi

# ============== 2. 价格表导出（容灾备份）==============
ADMIN_TOKEN=$(grep '^ADMIN_TOKEN=' "$INSTALL_DIR/.env" | cut -d= -f2)
if [ -n "$ADMIN_TOKEN" ]; then
    echo "[backup] exporting price catalog..."
    curl -sf http://127.0.0.1:8000/admin/price-catalog/export-json \
        -H "Authorization: Bearer $ADMIN_TOKEN" \
        > "$BACKUP_DIR/price-catalog-${DATE}.json" || echo "  ⚠ price-catalog export skipped"
fi

# ============== 3. .env 备份（含 Fernet key！务必加密保存）==============
echo "[backup] backing up .env..."
cp "$INSTALL_DIR/.env" "$BACKUP_DIR/env-${DATE}.bak"
chmod 600 "$BACKUP_DIR/env-${DATE}.bak"

# ============== 4. 清理过期备份 ==============
echo "[backup] purging > ${RETENTION_DAYS} days..."
find "$BACKUP_DIR" -maxdepth 1 -mtime +${RETENTION_DAYS} -type f -print -delete

# ============== 5. 报告 ==============
TOTAL_SIZE=$(du -sh "$BACKUP_DIR" | cut -f1)
echo "[backup] done. ${BACKUP_DIR} = ${TOTAL_SIZE}"
