# FireworkRouter

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com/)
[![Vue](https://img.shields.io/badge/Vue-3.5-42b883.svg)](https://vuejs.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()

> OpenAI 兼容的 [Fireworks.ai](https://fireworks.ai/) 多 Key 智能调度中转分发系统。单租户管理端、7 种调度策略、错误码差异化退避、per-(Key,Model) 冷却、稳定性监控、调度轨迹可视化。

## 截图

![登录页](docs/screenshots/login.png) ![Dashboard](docs/screenshots/dashboard.png) ![调度轨迹](docs/screenshots/trace.png)

*（实际启动后 `http://127.0.0.1:8000/` 看效果，初始密码 `admin`）*

## 功能特性

### 🔑 OpenAI 兼容网关
- `POST /v1/chat/completions`（含 SSE 流式）/ `/completions` / `/embeddings` / `/images/generations` / `/audio/*` / `/rerank`
- `GET /v1/models` / `/v1/models/{id}`
- 直接用 OpenAI SDK `base_url=http://127.0.0.1:8000/v1`

### ⚖️ 7 种调度策略
| 策略 | 说明 |
| --- | --- |
| `weighted_random` | 加权随机（默认） |
| `round_robin` | per-model 游标轮询 |
| `priority` | 严格优先级 + 同级加权随机 |
| `least_used` | 选最少用的 Key |
| `most_balance` | 选余额最高的 Key |
| `session_sticky` | 按 `prompt_cache_key` / `user` / 8 源 fallback 一致性哈希 |
| **`fill_first`** ⭐ | 顺序填满：一把用完再用下一把（按订阅窗口结算的场景最优） |

### 🚦 错误码差异化退避
| HTTP | 影响范围 | 冷却 | 重试本请求 |
| --- | --- | --- | --- |
| 401/403 | 整 Key | 30 min + auto_disabled | ❌ |
| 402 | 整 Key | 1h + auto_disabled | ❌ |
| 404 | **per-(Key, model)** | 12h | ❌ |
| 408/5xx | 整 Key | 1min 指数到 30min | ✅ |
| 429 | per-(Key, model) | 1s 指数到 30min | ✅ |
| 422/400 | 不冷却 | — | 客户端错误透传 |

### 📊 稳定性监控（每行 Key 直接看）
- **稳定性评分**（0–100%，按近 24h 成功率 + 流量加权计算）
- **余额条**：`$48.5 / $50.0` + 彩色进度条 + 百分比
- **最近 1h sparkline**：6 个 10min 桶，绿条 success + 红条 failed 叠加
- **双圆点指示**：请求是否通畅 + 探针是否通畅
- **冷却态可视化**：cooldown_until / cooldown_reason / backoff_level
- **调度轨迹可视化**：散点图（横轴时间、纵轴上游 Key）+ 桑基图（API Key → 模型 → 上游 Key）

### 🗝️ 多层 Key 管理
- 上游 Fireworks Key：Fernet 加密、批量导入、自动发现 `account_id`、`monthly-spend-usd` quota 探针
- 下游 API Key：`sk-fwr-` 形式，支持 label、ACL、配额、过期、旋转
- 模型管理：与 Fireworks 双向同步、定价、`public_name` ↔ `fireworks_path` 映射

### 🛠️ 单管理端 + 单密码
- 一个密码登录，签发 24h session JWT；不需要多用户体系
- 同时保留 `ADMIN_TOKEN` backdoor（CLI/CI 用）
- 暗色主题切换 + 持久化

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python 3.11 · FastAPI · SQLAlchemy 2.0 (async) · httpx · APScheduler · Alembic |
| 数据库 | SQLite（开发）/ PostgreSQL（生产） |
| 前端 | Vue 3 · Vite · Pinia · Vue Router · Naive UI · ECharts · vue-echarts |
| 部署 | Docker / Docker Compose |
| 鉴权 | bcrypt + JWT (HS256) |

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/d100000/FireworksRouter.git
cd FireworksRouter

# 2. 后端
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. 生成 .env（默认密码 admin）
python scripts/bootstrap.py
# 或自定义密码
python scripts/bootstrap.py "your-strong-password"

# 4. 构建前端
cd frontend && npm install && npm run build && cd ..

# 5. 启动（启动时自动 alembic upgrade head）
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开 `http://127.0.0.1:8000/` → 输入初始密码 `admin` → 进入 Dashboard。

### 添加上游 Fireworks Key

UI：「上游 Key 池」→「添加 Key」/「批量导入」

或 API：
```bash
ADMIN_TOKEN=$(grep '^ADMIN_TOKEN=' .env | cut -d= -f2)
curl -X POST http://127.0.0.1:8000/admin/upstream-keys \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"key":"fw_xxxxxxxxxx","name":"my-key","priority":10}'
```

入库时自动：
1. 调 `GET /v1/accounts` 发现 `account_id`
2. 调 `GET /v1/accounts/{id}/quotas` 解析 `monthly-spend-usd` 配额作为「余额」
3. `suspendState=UNSUSPENDED` + 余额 ≥ 阈值 → 标记 `active`，加入调度池

### 颁发下游 API Key

UI：「API Keys」→「新建」

或 API：
```bash
curl -X POST http://127.0.0.1:8000/admin/api-keys \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"label":"production-app","unlimited_quota":true,"stream_enabled":true}'
# → 返回 sk-fwr-xxxxxxxx
```

### 用 OpenAI SDK 调用

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="sk-fwr-xxxxxxxx",
)

resp = client.chat.completions.create(
    model="gpt-oss-120b",  # 用本地 public_name
    messages=[{"role": "user", "content": "hi"}],
    stream=True,
)
for chunk in resp:
    print(chunk.choices[0].delta.content or "", end="")
```

## Docker 部署

```bash
# 编辑 .env（用 bootstrap.py 生成密钥）
python scripts/bootstrap.py

# 启动 PostgreSQL + Redis + API
docker compose up -d
```

## API 端点（46 个）

完整 OpenAPI 文档：`http://127.0.0.1:8000/docs`

| 类别 | 端点 |
| --- | --- |
| **OpenAI 兼容** | `/v1/chat/completions`、`/v1/completions`、`/v1/embeddings`、`/v1/images/generations`、`/v1/rerank`、`/v1/audio/*`、`/v1/models[/{id}]` |
| **管理：鉴权** | `POST /admin/auth/login`、`POST /admin/auth/logout` |
| **管理：上游 Key** | `GET/POST/PATCH/DELETE /admin/upstream-keys`、`/batch`、`/{id}/probe`、`/{id}/metrics`、`/{id}/error-breakdown`、`/{id}/model-states`、`/probe-now` |
| **管理：下游 Key** | `GET/POST/PATCH/DELETE /admin/api-keys`、`/{id}/rotate` |
| **管理：模型** | `GET/POST/PATCH/DELETE /admin/models`、`/sync`、`/batch-status` |
| **管理：日志** | `GET /admin/logs/requests`、`/admin/logs/probes` |
| **管理：统计** | `GET /admin/stats/{overview,today,top,timeseries,keys-health,request-trace,flow-sankey}` |
| **管理：设置** | `GET/PATCH /admin/settings` |
| **公开** | `GET /system/info`、`/healthz`、`/readyz` |

## 数据库优化亮点

为了让 100+ 把上游 Key 的列表页（含 sparkline）保持流畅，做了**物化字段**优化：

- `UpstreamKey.recent_buckets_json` — 最近 1h 的 6 个 10min sparkline 数据，由后台任务每分钟刷新
- `UpstreamKey.last_probe_ok / ms / at` — 最近一次探针结果物化
- `UpstreamKey.success_count_24h / failed_count_24h / stability_score` — 24h 稳定性物化

**结果**：列表页只用一条 `SELECT * FROM upstream_keys`（100 行）即拿到所有可视化数据，**零额外查询**。Metrics worker 每分钟跑一次 `_refresh_recent_buckets()` + `_refresh_last_probe()`，单条 SQL 拉全量后内存聚合再批量回写。

## 项目结构

```
.
├── app/                          # 后端
│   ├── main.py                   # FastAPI 入口 + lifespan
│   ├── config.py                 # pydantic-settings
│   ├── db.py                     # SQLAlchemy async 引擎
│   ├── crypto.py                 # Fernet 加密
│   ├── api/                      # HTTP 路由
│   │   ├── deps.py               # 鉴权依赖
│   │   ├── admin_auth.py         # 单密码登录
│   │   ├── admin.py              # 上游 Key / API Key / 日志 / 统计
│   │   ├── admin_models.py       # 模型管理
│   │   ├── admin_metrics.py      # per-key 监控
│   │   ├── admin_settings.py     # 系统设置 KV
│   │   └── system.py             # 公开元信息
│   ├── gateway/                  # OpenAI 兼容网关
│   │   ├── router.py             # /v1/* 端点
│   │   └── proxy.py              # 流式转发 + 失败切换 + 计费
│   ├── services/
│   │   ├── fireworks.py          # Fireworks 客户端
│   │   ├── scheduler.py          # 7 种调度策略
│   │   ├── cooldown.py           # 错误码差异化退避
│   │   ├── metrics.py            # 5min 桶聚合 + 物化字段
│   │   ├── balance.py            # 余额探针
│   │   ├── upstream.py           # 上游 Key 入库
│   │   ├── models.py             # 模型映射 + 同步
│   │   ├── settings.py           # KV 设置
│   │   └── session.py            # bcrypt + session JWT
│   ├── models/                   # SQLAlchemy 模型
│   └── tasks/                    # APScheduler 任务
├── alembic/                      # 数据库迁移
├── frontend/                     # Vue 3 SPA
│   └── src/
│       ├── views/                # 8 个页面
│       ├── components/           # KpiCard / StatusDot / BalanceBar / Sparkline / HealthSignal
│       ├── stores/               # Pinia (auth / theme)
│       ├── styles/globals.css    # Teal 主题 + Glass + Mesh
│       ├── api/                  # axios 客户端
│       └── router/
├── scripts/
│   ├── bootstrap.py              # 生成 .env
│   └── issue_token.py            # CLI 颁发 API Key
├── docs/PRD.md                   # 完整产品需求文档
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## 路线图

- ✅ M1-M3：核心网关 + 用户系统 + 模型映射 + Alembic
- ✅ M5：7 种调度 + 错误码退避 + per-(Key,Model) 冷却
- ✅ v2：单密码管理端 + 监控仪表盘 + 调度轨迹可视化
- ✅ v3：物化字段 DB 优化 + 余额/sparkline 可视化
- ⏳ 告警通知（飞书/钉钉 webhook）
- ⏳ Prometheus metrics 端点
- ⏳ 多上游 Provider（DeepSeek / Together / Anthropic 直连）

## 许可证

MIT
