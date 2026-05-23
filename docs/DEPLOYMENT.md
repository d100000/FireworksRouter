# FireworkRouter 生产部署指南

> 适用：在自己的 Linux 服务器上从源码完整部署一套 FireworkRouter。

## 目录

- [一、容量评估](#一容量评估)
- [二、硬件 / 系统要求](#二硬件--系统要求)
- [三、方案 A：Docker Compose（推荐）](#三方案-adocker-compose推荐)
- [四、方案 B：裸机 systemd](#四方案-b裸机-systemd)
- [五、反向代理 + HTTPS（Caddy / Nginx）](#五反向代理--https)
- [六、生产化调优清单](#六生产化调优清单)
- [七、备份与恢复](#七备份与恢复)
- [八、升级流程](#八升级流程)
- [九、监控与告警](#九监控与告警)
- [十、故障排查](#十故障排查)

---

## 一、容量评估

| 使用规模 | 推荐配置 | 备注 |
| --- | --- | --- |
| **≤ 50 QPS / 100 把 Key** | 单机 2C4G + SQLite | 默认配置即可 |
| **50-300 QPS** | 单机 4C8G + PostgreSQL | 切 PG + 4 worker |
| **300-1000 QPS** | 单机 8C16G + PostgreSQL + Redis | 4-8 worker + httpx 连接池 |
| **1000+ QPS** | 多机 + Nginx 负载均衡 + PG 独立机 | 横向扩展 |

**瓶颈优先级**：SQLite 写吞吐 → Uvicorn worker 数 → httpx 连接池 → 网络带宽。

---

## 二、硬件 / 系统要求

### 推荐配置（中小商业 ~300 QPS）

| 资源 | 规格 |
| --- | --- |
| **CPU** | 4 vCPU |
| **内存** | 8 GB |
| **磁盘** | 50 GB SSD（含日志和 DB） |
| **带宽** | 5 Mbps 上行 |
| **系统** | Ubuntu 22.04 LTS / Debian 12 / CentOS Stream 9 |

### 最低配置（个人/测试）

| 资源 | 规格 |
| --- | --- |
| **CPU** | 1 vCPU |
| **内存** | 2 GB |
| **磁盘** | 20 GB |

### 必须开放的端口

| 端口 | 用途 |
| --- | --- |
| **80** | HTTP（Caddy/Nginx 自动跳转 HTTPS） |
| **443** | HTTPS 对外 |
| **22** | SSH 管理（建议改非标 + key 登录） |

---

## 三、方案 A：Docker Compose（推荐）

### 1. 服务器准备

```bash
# Ubuntu 22.04 示例
sudo apt update && sudo apt install -y curl git ca-certificates

# 安装 Docker + Compose
curl -fsSL https://get.docker.com | sudo bash
sudo usermod -aG docker $USER   # 当前用户加入 docker 组
newgrp docker                    # 立刻生效
docker version
docker compose version
```

### 2. 克隆代码

```bash
cd /opt
sudo git clone https://github.com/d100000/FireworksRouter.git
sudo chown -R $USER:$USER FireworksRouter
cd FireworksRouter
```

### 3. 生成密钥 + 编辑 `.env`

```bash
# 自动生成 ADMIN_TOKEN / FERNET_KEY / SESSION_SECRET / ADMIN_PASSWORD_HASH
# 第一个参数 = 你的初始登录密码
python3 -c "import sys; exec(open('scripts/bootstrap.py').read())" "your-strong-password-here"

# 检查生成结果
cat .env
```

> ⚠️ `UPSTREAM_KEY_FERNET_KEY` 必须**备份**！丢了所有上游 Fireworks Key 都解不开。

### 4. 关键 `.env` 改造（生产用 PostgreSQL）

```ini
APP_ENV=production
LOG_LEVEL=INFO

# 把 SQLite 改成 PostgreSQL（docker-compose.yml 里 postgres 服务）
DATABASE_URL=postgresql+asyncpg://fwr:CHANGE_ME_TO_A_STRONG_PASSWORD@postgres:5432/fwr

# 也可启用 Redis（可选）
REDIS_URL=redis://redis:6379/0

# 探针并发可以调高（多 worker 时尤其重要）
PROBE_CONCURRENCY=20
PROBE_INTERVAL_MINUTES=15

# 余额查询超时（弱网络环境可调高，默认 160s 上限）
# 在 fireworks.py 内置常量 BALANCE_QUERY_TIMEOUT_S=160
```

### 5. 改 `docker-compose.yml` 的 PG 密码

编辑 `docker-compose.yml`：
```yaml
postgres:
  environment:
    POSTGRES_USER: fwr
    POSTGRES_PASSWORD: CHANGE_ME_TO_A_STRONG_PASSWORD   # ← 同 .env
    POSTGRES_DB: fwr
```

### 6. 启动

```bash
# 拉镜像 + 构建 + 启动
docker compose up -d --build

# 看日志确认启动成功
docker compose logs -f api | head -50
# 期望看到："FireworkRouter v0.1.0 starting" 和 "alembic upgrade ... done"
```

### 7. 编译前端 SPA（产物会被 api 容器挂载）

```bash
# 在宿主机上编译（也可以在 Dockerfile 里集成，但分开更易调试）
cd frontend
sudo apt install -y nodejs npm   # 或用 nvm 装 Node 20
npm install
npm run build
cd ..

# api 已挂载 ./frontend/dist 到容器，但我们的 Dockerfile 是 COPY 模式
# 改为挂载方式（更新 docker-compose.yml）或重 build：
docker compose up -d --build api
```

### 8. 验证

```bash
curl http://127.0.0.1:8011/healthz
# {"status":"ok"}

curl http://127.0.0.1:8011/system/info
# {"service":"FireworkRouter", "version":"0.1.0", ...}
```

浏览器打开 `http://你的服务器IP:8011/` → 用你刚才设的密码登录。

---

## 四、方案 B：裸机 systemd

适合喜欢直接管 Python 进程的场景。

### 1. 系统准备

```bash
sudo apt install -y python3.11 python3.11-venv python3-pip git build-essential \
                    postgresql-15 redis-server nginx

sudo systemctl enable --now postgresql redis-server
```

### 2. 建数据库 + 用户

```bash
sudo -u postgres psql <<EOF
CREATE USER fwr WITH PASSWORD 'CHANGE_ME_TO_A_STRONG_PASSWORD';
CREATE DATABASE fwr OWNER fwr;
GRANT ALL PRIVILEGES ON DATABASE fwr TO fwr;
EOF
```

### 3. 部署代码

```bash
sudo mkdir -p /opt/FireworksRouter
sudo chown $USER:$USER /opt/FireworksRouter
cd /opt
git clone https://github.com/d100000/FireworksRouter.git
cd FireworksRouter

python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install gunicorn
```

### 4. 生成 `.env`

```bash
python scripts/bootstrap.py "your-strong-password-here"
# 编辑 DATABASE_URL 指向 PG：
#   DATABASE_URL=postgresql+asyncpg://fwr:CHANGE_ME@127.0.0.1:5432/fwr
```

### 5. 构建前端

```bash
cd frontend
npm install
npm run build
cd ..
```

### 6. systemd 单元文件

新建 `/etc/systemd/system/fireworkrouter.service`：

```ini
[Unit]
Description=FireworkRouter API
After=network.target postgresql.service redis-server.service
Wants=postgresql.service

[Service]
Type=simple
User=fwr
Group=fwr
WorkingDirectory=/opt/FireworksRouter
EnvironmentFile=/opt/FireworksRouter/.env
ExecStart=/opt/FireworksRouter/.venv/bin/gunicorn \
    -k uvicorn.workers.UvicornWorker \
    -w 4 \
    -b 127.0.0.1:8011 \
    --access-logfile - \
    --error-logfile - \
    --timeout 180 \
    --graceful-timeout 30 \
    app.main:app

Restart=on-failure
RestartSec=5
LimitNOFILE=65536
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
# 建 fwr 用户
sudo useradd -r -s /sbin/nologin -d /opt/FireworksRouter fwr
sudo chown -R fwr:fwr /opt/FireworksRouter

sudo systemctl daemon-reload
sudo systemctl enable --now fireworkrouter
sudo systemctl status fireworkrouter
journalctl -u fireworkrouter -f
```

---

## 五、反向代理 + HTTPS

### 推荐：Caddy（自动 HTTPS）

```bash
# Ubuntu
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
    sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | \
    sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

`/etc/caddy/Caddyfile`：
```
your-domain.example.com {
    encode gzip

    # SSE 流式响应必须关 buffer
    reverse_proxy 127.0.0.1:8011 {
        flush_interval -1
        transport http {
            response_header_timeout 180s
            read_timeout 180s
        }
    }
}
```

```bash
sudo systemctl reload caddy
# Caddy 会自动从 Let's Encrypt 申请证书 + 自动续期
```

### 备选：Nginx

`/etc/nginx/sites-available/fireworkrouter`：
```nginx
server {
    listen 80;
    server_name your-domain.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.example.com;

    ssl_certificate     /etc/letsencrypt/live/your-domain.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.example.com/privkey.pem;

    # SSE 流式必须配置
    proxy_buffering off;
    proxy_cache off;
    proxy_http_version 1.1;
    proxy_set_header Connection "";

    # 大模型响应慢，超时给足
    proxy_read_timeout 180s;
    proxy_send_timeout 180s;

    client_max_body_size 50M;   # 给 image/audio 上传留空间

    location / {
        proxy_pass http://127.0.0.1:8011;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo certbot --nginx -d your-domain.example.com
sudo ln -s /etc/nginx/sites-available/fireworkrouter /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

---

## 六、生产化调优清单

### 必做

| 项 | 命令 / 配置 |
| --- | --- |
| **改默认 admin 密码** | 登录后右上角 → 修改密码 |
| **改 PG 默认密码** | `docker-compose.yml` + `.env` 同步 |
| **关闭 SQLite，切 PG** | `.env` 的 `DATABASE_URL` |
| **配置 HTTPS** | Caddy 或 Nginx + certbot |
| **备份 Fernet key** | `UPSTREAM_KEY_FERNET_KEY` 异地存档 |
| **设置 logs_retention_days** | 系统设置 → 日志保留天数（默认 30） |

### 推荐

| 项 | 收益 |
| --- | --- |
| **Uvicorn 4 worker** | Dockerfile 已默认 `-w 4` |
| **`PROBE_ON_STARTUP=false`** | 启动快 15s（牺牲启动时刷余额） |
| **PG `shared_buffers=256MB`** | 中等 PG 调优 |
| **Caddy 启用 gzip** | API 响应压缩 50% |

### `.env` 生产模板

```ini
APP_ENV=production
APP_PORT=8011
LOG_LEVEL=INFO

# PostgreSQL（生产必备）
DATABASE_URL=postgresql+asyncpg://fwr:STRONG_PASSWORD@postgres:5432/fwr

# Redis（可选；当前未强依赖）
REDIS_URL=redis://redis:6379/0

# 鉴权（bootstrap.py 自动生成）
ADMIN_TOKEN=...
ADMIN_PASSWORD_HASH=...
SESSION_TOKEN_SECRET=...
SESSION_TOKEN_TTL_HOURS=24

# 上游 Key 加密
UPSTREAM_KEY_FERNET_KEY=...  # ⚠️ 备份！

# 调度
GATEWAY_MAX_RETRY_CREDENTIALS=3
GATEWAY_MAX_RETRY_INTERVAL_S=30

# Fireworks
FIREWORKS_INFERENCE_BASE_URL=https://api.fireworks.ai/inference/v1
FIREWORKS_ADMIN_BASE_URL=https://api.fireworks.ai/v1
GATEWAY_DEFAULT_TIMEOUT_S=180

# 探针
PROBE_INTERVAL_MINUTES=15
PROBE_CONCURRENCY=20
PROBE_MIN_BALANCE_USD=0.5
PROBE_ON_STARTUP=false

# 上游代理（中转海外节点用）
# HTTP_PROXY=http://127.0.0.1:7890
# HTTPS_PROXY=http://127.0.0.1:7890
```

---

## 七、备份与恢复

### 必须备份

1. **`.env`**（含 Fernet key）→ 复制到异地 / 加密云盘
2. **PostgreSQL 数据库**
3. **价格表 / 模型配置**（可通过 UI 导出 JSON 作快速备份）

### 备份脚本（每天 03:00 凌晨）

`/opt/FireworksRouter/scripts/backup.sh`：
```bash
#!/bin/bash
set -e
BACKUP_DIR=/var/backups/fireworkrouter
mkdir -p "$BACKUP_DIR"
DATE=$(date +%Y%m%d)

# Postgres dump
docker compose -f /opt/FireworksRouter/docker-compose.yml exec -T postgres \
    pg_dump -U fwr fwr | gzip > "$BACKUP_DIR/db-$DATE.sql.gz"

# 价格表导出（通过 API）
ADMIN_TOKEN=$(grep '^ADMIN_TOKEN=' /opt/FireworksRouter/.env | cut -d= -f2)
curl -s http://127.0.0.1:8011/admin/price-catalog/export-json \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    | gzip > "$BACKUP_DIR/price-catalog-$DATE.json.gz"

# .env 备份（含 Fernet key）
cp /opt/FireworksRouter/.env "$BACKUP_DIR/env-$DATE.bak"

# 保留 30 天
find "$BACKUP_DIR" -mtime +30 -delete
```

```bash
sudo chmod +x /opt/FireworksRouter/scripts/backup.sh
# crontab -e
0 3 * * * /opt/FireworksRouter/scripts/backup.sh >> /var/log/fwr-backup.log 2>&1
```

### 恢复 Postgres

```bash
gunzip < db-20260523.sql.gz | \
    docker compose exec -T postgres psql -U fwr fwr
```

---

## 八、升级流程

```bash
cd /opt/FireworksRouter

# 1. 备份（重要！）
./scripts/backup.sh

# 2. 拉最新代码
git fetch && git pull origin main

# 3. 重新构建前端
cd frontend && npm install && npm run build && cd ..

# 4. 重启 API（自动跑 alembic upgrade head）
docker compose up -d --build api
# 或裸机：sudo systemctl restart fireworkrouter

# 5. 验证
docker compose logs api | tail -30
curl http://127.0.0.1:8011/healthz
```

### 数据库迁移失败回滚

```bash
# Postgres：用最近一次 dump 恢复
docker compose stop api
docker compose exec -T postgres dropdb -U fwr fwr
docker compose exec -T postgres createdb -U fwr fwr
gunzip < /var/backups/fireworkrouter/db-yesterday.sql.gz | \
    docker compose exec -T postgres psql -U fwr fwr
git reset --hard <last-good-commit>
docker compose up -d --build api
```

---

## 九、监控与告警

### 健康检查

| 检查项 | 命令 |
| --- | --- |
| API 存活 | `curl http://127.0.0.1:8011/healthz` |
| API 就绪 | `curl http://127.0.0.1:8011/readyz` |
| 探针运行 | 看 Dashboard「冷却中 / 总余额」 |
| DB 连接 | `docker compose exec postgres pg_isready -U fwr` |

### 简单告警（Cron + curl）

`/opt/FireworksRouter/scripts/healthcheck.sh`：
```bash
#!/bin/bash
if ! curl -fs --max-time 10 http://127.0.0.1:8011/healthz > /dev/null; then
    # 发飞书 webhook（替换成你的）
    curl -X POST https://open.feishu.cn/open-apis/bot/v2/hook/XXX \
        -H "Content-Type: application/json" \
        -d '{"msg_type":"text","content":{"text":"⚠️ FireworkRouter API DOWN!"}}'
fi
```

```bash
# crontab -e
*/2 * * * * /opt/FireworksRouter/scripts/healthcheck.sh
```

### 资源占用

```bash
# 容器资源
docker stats fireworkrouter-api-1 fireworkrouter-postgres-1

# 磁盘（PG 数据增长）
du -sh /var/lib/docker/volumes/fireworkrouter_postgres_data/_data
```

---

## 十、故障排查

### 启动失败

```bash
# 容器日志
docker compose logs api --tail 100

# 常见问题：
# 1. ADMIN_PASSWORD_HASH 为空 → 跑 python scripts/bootstrap.py
# 2. DATABASE_URL 连不上 → docker compose ps 看 postgres 状态
# 3. alembic migration 失败 → 备份 + 检查迁移文件
```

### 调用 /v1/chat/completions 失败

```bash
# 看错误码
curl -v http://127.0.0.1:8011/v1/chat/completions \
    -H "Authorization: Bearer sk-fwr-..." \
    -d '{"model":"gpt-oss-120b","messages":[{"role":"user","content":"hi"}]}'

# 错误码对照（见 README 错误处理矩阵）：
# 401 → API Key 无效；后台「API Keys」检查
# 402 → 余额不足；后台「上游 Key 池」点「更新所有余额」
# 404 → 模型未启用；后台「模型管理」启用 + 填价
# 429 → 限流；查 Retry-After header
# 503 → 全部上游 Key 在冷却；后台看「冷却态」详情
```

### 性能差

```bash
# 1. 检查是否在用 SQLite（生产应该 PG）
grep DATABASE_URL .env

# 2. 检查 worker 数（应 ≥ 4）
docker compose exec api pgrep -af "gunicorn|uvicorn" | wc -l

# 3. PG 慢查询
docker compose exec postgres psql -U fwr fwr -c \
    "SELECT pid, query, state, query_start FROM pg_stat_activity WHERE state='active';"

# 4. 看上游延迟
# 「调用日志」筛 latency_ms > 5000，看是哪把 Key
```

### 容器无法启动 + 报"address already in use"

```bash
sudo ss -tlnp | grep :8011   # 看谁占了 8011
sudo systemctl stop nginx    # 或别的占用者
docker compose up -d
```

### 数据库锁死（SQLite WAL 模式偶发）

```bash
docker compose exec api ls -la /app/data/
# 如果看到 fireworkrouter.db-shm fireworkrouter.db-wal
# 重启 api 让 WAL checkpoint 写回
docker compose restart api
```

---

## 附：单机部署 vs 多机扩展

### 单机部署架构（推荐起步）

```
        ┌──────────────────────────────────┐
        │  Caddy/Nginx (HTTPS, gzip)       │ :443
        └──────────────┬───────────────────┘
                       │
        ┌──────────────▼───────────────────┐
        │  FastAPI / Gunicorn × 4 worker   │ :8011
        │  + APScheduler 探针 / metrics    │
        └──┬─────────────────────────┬─────┘
           │                         │
   ┌───────▼──────┐         ┌────────▼──────┐
   │ PostgreSQL 15│         │  Redis 7      │
   │              │         │  (限流可选)    │
   └──────────────┘         └───────────────┘
```

### 多机扩展架构（千 QPS+）

```
                ┌──────────────┐
                │ Nginx LB / HA│ :443
                └──┬───────┬───┘
                   │       │
         ┌─────────▼──┐ ┌──▼─────────┐
         │ API 实例 1 │ │ API 实例 2 │  ... × N
         └─────┬──────┘ └────┬───────┘
               │             │
               └──────┬──────┘
                      │
            ┌─────────▼─────────┐
            │ PostgreSQL 主从    │
            │ + PgBouncer 连接池 │
            └───────────────────┘
                      │
            ┌─────────▼─────────┐
            │  Redis Sentinel   │
            └───────────────────┘
```

**注意**：当前代码的 `metrics worker` + `probe scheduler` 是**单实例任务**，多实例部署时只能在一台机器跑这些后台任务（用环境变量 `BACKGROUND_TASKS=true/false` 区分）。这是下一步可改进的点。

---

## 一键部署脚本（最简）

参见 [`scripts/deploy.sh`](../scripts/deploy.sh)（已生成，含交互式询问）：

```bash
curl -fsSL https://raw.githubusercontent.com/d100000/FireworksRouter/main/scripts/deploy.sh | bash
```
