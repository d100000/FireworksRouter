# FireworkRouter

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com/)
[![Vue](https://img.shields.io/badge/Vue-3.5-42b883.svg)](https://vuejs.org/)
[![Naive UI](https://img.shields.io/badge/Naive%20UI-2.41-14b8a6.svg)](https://www.naiveui.com/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()

> 🎆 一站式 [Fireworks.ai](https://fireworks.ai/) **多 Key 智能调度中转**。OpenAI / Anthropic 双协议兼容，错误码差异化退避，per-(Key, Model) 冷却状态机，物化字段 sparkline 监控，单密码管理后台。

## ✨ 它能做什么

- **多 Key 池调度**：100+ 把 Fireworks Key 自动分流，7 种策略可切换（含 `fill_first` 顺序填满 + `session_sticky` 8 源会话粘性）
- **零代码改造**：业务直接用 OpenAI / Anthropic SDK，把 `base_url` 指过来即可
- **失败自愈**：上游 401/402/404/429/5xx 按错误码差异化退避；per-(Key, Model) 二元组冷却，单模型 404 不影响其它模型
- **实时监控**：每行 Key 都有余额条 + 24h SVG sparkline + 请求/探针双圆点 + 稳定性评分
- **完整可观测**：调用日志可展开看完整计费公式、错误详情、流状态；调度轨迹散点图 + 桑基图
- **OpenAI 标准错误**：参考 one-api / new-api 实践，错误响应严格遵循 `{error: {message, type, code, param}}` 结构；429 带 `Retry-After`

## 📸 截图

> 启动后打开 [http://127.0.0.1:8011/](http://127.0.0.1:8011/)，初始密码 `admin`（请先在「修改密码」页改掉）

| 界面 | 说明 |
| --- | --- |
| 登录页 | Mesh gradient + 三颗 teal blur orb + 网格 overlay 装饰 |
| Dashboard | 8 张多色 KPI 卡 + 24h 趋势 + Key 健康 Top 5/Bottom 5 + Top API Key / 模型 / 上游 |
| 上游 Key 池 | 每行：余额条 + 百分比 + 24h sparkline + 双圆点 + 稳定性评分 + 一键展开抽屉 |
| 调用日志 | 7 维筛选 + 彩色 chip 表格 + 行内展开看计费公式 + 错误详情 |
| 调度轨迹 | 散点图（时间 × 上游 Key，颜色=状态，大小=延迟）+ 桑基图（API Key → 模型 → 上游 Key）|

## 🎯 客户端兼容矩阵

| SDK / 工具 | base_url | 鉴权头 | 路径 |
| --- | --- | --- | --- |
| **OpenAI Python/JS SDK** | `http://your-host/v1` | `Authorization: Bearer sk-fwr-...` | `/v1/chat/completions`, `/v1/models` |
| **Cursor / Cline / Continue** | `http://your-host/v1` | 同上 | 同上 |
| **Claude Code / Anthropic SDK** | `http://your-host` | `x-api-key: sk-fwr-...` | `/v1/messages` |
| 误填 `/openai/v1` 前缀 | 自动兼容 | 同上 | 透明 |
| 误填 `/api/v1` 前缀 | 自动兼容 | 同上 | 透明 |

## 🛠️ 核心功能详解

### 1. OpenAI 兼容网关（10+ 端点）

```
POST  /v1/chat/completions          ✅ 含 SSE 流式 + reasoning_content
POST  /v1/completions
POST  /v1/embeddings
POST  /v1/images/generations
POST  /v1/audio/transcriptions      ✅ multipart 透传
POST  /v1/audio/translations
POST  /v1/audio/speech
POST  /v1/rerank
GET   /v1/models
GET   /v1/models/{model:path}        ✅ 路径式 model_id 支持
```

**直接用 OpenAI SDK：**
```python
from openai import OpenAI
client = OpenAI(base_url="http://your-host/v1", api_key="sk-fwr-xxx")
client.chat.completions.create(model="gpt-oss-120b", messages=[...], stream=True)
```

### 2. Anthropic Claude 协议（Claude Code / Anthropic SDK）

`POST /v1/messages` — 接受 `x-api-key` 或 `Authorization: Bearer` 任一鉴权头，请求/响应在本地完成 **Anthropic Messages ↔ OpenAI Chat 双向翻译**：

| Anthropic 字段 | → OpenAI 字段 |
| --- | --- |
| `system: string\|list` | `messages[0] = {role: "system", content: ...}` |
| `content: [{type:"text", text}]` | `content: "string"` |
| `max_tokens / temperature / top_p / top_k / stop_sequences` | 同名映射 |

响应翻译回 Anthropic Messages 信封：
- `choices[0].message.content` → `content: [{type:"text", text}]`
- `finish_reason: stop/length/tool_calls` → `stop_reason: end_turn/max_tokens/tool_use`
- `usage.prompt_tokens / completion_tokens` → `usage.input_tokens / output_tokens`

**流式**：拼装 5 种标准 SSE event：`message_start → content_block_start → content_block_delta(text_delta) → content_block_stop → message_delta → message_stop`

**用 Anthropic SDK：**
```python
from anthropic import Anthropic
client = Anthropic(base_url="http://your-host", api_key="sk-fwr-xxx")
client.messages.create(model="gpt-oss-120b", max_tokens=100, messages=[...])
```

### 3. 7 种调度策略

| 策略 | 适用场景 |
| --- | --- |
| `weighted_random` | 按权重加权随机（默认；最均衡） |
| `round_robin` | per-model 游标轮询（防爆 4096 上限） |
| `priority` | 严格优先级降序，同级再加权随机 |
| `least_used` | 选 `last_used_at` 最早的（冷 Key 优先） |
| `most_balance` | 选余额最高的（穷尽富 Key 优先） |
| `session_sticky` | **8 源 fallback 会话粘性**：`prompt_cache_key → metadata.user_id → conversation_id → user → messages FNV → X-Session-ID → ... → api_key_id` |
| **`fill_first`** ⭐ | **顺序填满**：永远取候选池第一把（优先级最高 + ID 最小），冷却才让位 — 按订阅窗口结算的 Key 池最优 |

### 4. 错误码差异化退避（参考 CLIProxyAPI + new-api）

| 上游 HTTP | 网关响应 | 冷却范围 | 时长 | 可重试 |
| --- | --- | --- | --- | --- |
| 200/2xx | 透传 | — | — | — |
| 400/422 | **透传上游 raw** | 不冷却 | — | ❌ 客户端错误 |
| 401/403 | 401 + `authentication_error` | 整 Key | 30 min（30min 后转 `auto_disabled`） | ❌ 切下一把 |
| 402 | 429 + `insufficient_quota` | 整 Key | 1h | ❌ 切下一把 |
| 404（模型不支持） | 404 + `model_not_found` | **(Key, Model) 二元组** | 12h | ❌ 切下一把 |
| 408/5xx | 502 + `upstream_error` | 整 Key | 1min 指数到 30min | ✅ |
| 429 | 429 + `rate_limit_error` + `Retry-After` | **(Key, Model)** | 1s 指数到 30min | ✅ |
| 503 | 503 + `overloaded_error` | 整 Key | 1min | ✅ |
| 网络异常 | — | 整 Key | 30s | ✅ |

**全部 Key 失败时**智能升级：连续 ≥ 2 把 401/402 → 503 `service_unavailable`（SDK 会自动重试）。

**关键词触发整 Key 长期禁用**（15 个关键词）：响应体含 `invalid api key / account_deactivated / banned / revoked / expired / insufficient quota / billing_hard_limit_reached / ...` 任一 → 立即设 1h cooldown + `keyword_match` 标记。

### 5. 标准 OpenAI 错误结构

所有错误响应严格遵循官方规范，**绝不会出现 `{"detail": ...}` 包裹**：

```json
{
  "error": {
    "message": "Model 'kimi-k2-instruct-0905' not found",
    "type": "not_found_error",
    "code": "model_not_found",
    "param": null,
    "details": {
      "upstream_status": 404,
      "request_id": "fwr-c9c078a3968644829f432e4b"
    }
  }
}
```

错误 type 完整集合：`invalid_request_error / authentication_error / permission_error / not_found_error / request_too_large / rate_limit_error / insufficient_quota / upstream_error / overloaded_error / service_unavailable / timeout_error`

### 6. 数据库性能优化（物化字段方案）

为了让 100+ 把 Key 的列表页（含 sparkline）保持流畅，**避开 N+1 查询**，关键监控数据物化到 `upstream_keys` 表主行：

| 字段 | 数据来源 | 刷新周期 |
| --- | --- | --- |
| `recent_buckets_json` | 聚合 `key_metric_buckets` 最近 1h 的 5min 桶 → 6 个 10min 桶 | 60s |
| `last_probe_ok / ms / at` | `probe_history` 最近一条 | 60s |
| `success_count_24h / failed_count_24h / stability_score` | 24h 桶汇总 + `success/(s+f) × (1-exp(-n/10))` 流量加权 | 60s |

**结果**：列表查询只跑一条 `SELECT * FROM upstream_keys`（100 行 < 10ms），无任何额外 JOIN / 子查询。后台 metrics worker 每 60s 跑一次聚合 + 批量回写，单次 < 50ms。

### 7. 单密码管理后台（不要多用户系统）

- **登录**：一个密码即可（首次 `admin`）
- **改密码**：右上角头像菜单 → 「🛡️ 修改密码」→ 旧密码 + 新密码 + 确认
- **密码存储**：bcrypt 哈希，DB `system_settings` 优先于 `.env` 的 `ADMIN_PASSWORD_HASH`（首次启动用 .env 引导，改密后用 DB 值）
- **双通道鉴权**：
  - UI 登录 → session JWT（24h）
  - CLI / 脚本 / CI → `.env` 的 `ADMIN_TOKEN`（永久 backdoor）

### 8. 调用日志（参考 newAPI 风格）

**顶部 7 维筛选条**：时间范围 / 状态码 / 流式 / 模型 / 上游 Key / API Key / Request ID

**表格列**（彩色 chip + 双行密度）：
- 时间（相对时间副标）
- API Key（label 彩色 chip + preview mono 副标）
- 端点（teal code chip）
- 模型（按 hash 着色 + emoji 前缀 ⊕ 💧 ✦ 🌙 🪐 🔷 🐦 🎨）
- 上游 Key（紫色 ID chip）
- 类型（业务化：消费 / 限流 / 客户端错误 / 服务端错误 / 网络异常）
- 用时/首字（两 chip + 🌊 流式标签）
- 详情（账单 / Tokens / 重试次数）

**每行可展开**抽屉显示：
- Request ID、请求路径、上游 Key、流状态（首字 + 完成 ms）、HTTP 状态、重试次数
- Token 用量拆解：输入 / 输出 / 缓存命中
- **计费公式**：`输入计费 X tokens · 输出 Y tokens · 上游原始成本 $A · 专属倍率 × M → 账单成本 $B`
- 错误（如有）：完整 error_message

## 🧰 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python 3.11 · FastAPI 0.115 · SQLAlchemy 2.0 (async) · httpx · APScheduler · Alembic |
| 数据库 | SQLite (开发) / PostgreSQL 15 (生产) |
| 前端 | Vue 3.5 · Vite · Pinia · Vue Router · Naive UI 2.41 · ECharts 5 · vue-echarts |
| 鉴权 | bcrypt (cost 12) + JWT (HS256, 24h session) |
| 加密 | Fernet（上游 Key + 下游 Token 都加密存储） |
| 部署 | Docker / Docker Compose / 单端口 8011 服务前端 SPA + API |

## 🚀 快速开始

```bash
# 1. 克隆
git clone git@github.com:d100000/FireworksRouter.git
cd FireworksRouter

# 2. 后端依赖
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. 生成 .env（默认密码 admin）
python scripts/bootstrap.py
#   或自定义密码（≥ 8 位）:
#   python scripts/bootstrap.py "your-strong-password"

# 4. 构建前端
cd frontend && npm install && npm run build && cd ..

# 5. 启动（启动时自动 alembic upgrade head）
uvicorn app.main:app --host 0.0.0.0 --port 8011
```

打开 [http://127.0.0.1:8011/](http://127.0.0.1:8011/) → 输入初始密码 `admin` → 进入 Dashboard。

### 添加上游 Fireworks Key

UI：「上游 Key 池」→「添加 Key」 / 「批量导入」

或 API：
```bash
ADMIN_TOKEN=$(grep '^ADMIN_TOKEN=' .env | cut -d= -f2)
curl -X POST http://127.0.0.1:8011/admin/upstream-keys \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"key":"fw_xxxxxxxxxx","name":"primary","priority":10}'
```

入库时自动：
1. `GET /v1/accounts` 发现 `account_id`
2. `GET /v1/accounts/{id}/quotas` 解析 `monthly-spend-usd` 配额作为「余额」
3. `suspendState=UNSUSPENDED` 且余额 ≥ 阈值 → 标记 `active`，进入调度池

### 颁发下游 API Key

UI：「API Keys」→「新建 API Key」（创建后弹窗显示完整 token，可一次复制；已创建的 Key 列表里也能用「复制」按钮再次取出明文）

或 API：
```bash
curl -X POST http://127.0.0.1:8011/admin/api-keys \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"label":"production-app","unlimited_quota":true,"stream_enabled":true}'
# → 返回 {"token": "sk-fwr-xxxxxxxx", ...}
```

### 用 OpenAI SDK 调用

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8011/v1",
    api_key="sk-fwr-xxxxxxxx",
)

resp = client.chat.completions.create(
    model="gpt-oss-120b",  # 本地 public_name；后端自动改写为 fireworks_path
    messages=[{"role": "user", "content": "你好"}],
    stream=True,
)
for chunk in resp:
    print(chunk.choices[0].delta.content or "", end="")
```

### 用 Anthropic SDK 调用

```python
from anthropic import Anthropic

client = Anthropic(
    base_url="http://127.0.0.1:8011",
    api_key="sk-fwr-xxxxxxxx",
)

msg = client.messages.create(
    model="gpt-oss-120b",
    max_tokens=100,
    messages=[{"role": "user", "content": "你好"}],
)
print(msg.content[0].text)
```

## 📡 完整 API 端点（约 50 个）

完整 OpenAPI 文档：`http://127.0.0.1:8011/docs`

### OpenAI 兼容（路径别名 `/openai/v1/*` 和 `/api/v1/*` 自动兼容）
```
POST /v1/chat/completions
POST /v1/completions
POST /v1/embeddings
POST /v1/images/generations
POST /v1/audio/{transcriptions,translations,speech}
POST /v1/rerank
GET  /v1/models  [/{model:path}]
```

### Anthropic 兼容
```
POST /v1/messages
```

### 管理 — 鉴权
```
POST /admin/auth/login
POST /admin/auth/logout
POST /admin/auth/change-password
```

### 管理 — 上游 Key
```
GET    /admin/upstream-keys
POST   /admin/upstream-keys
POST   /admin/upstream-keys/batch
GET    /admin/upstream-keys/{id}
PATCH  /admin/upstream-keys/{id}
DELETE /admin/upstream-keys/{id}
POST   /admin/upstream-keys/{id}/probe
POST   /admin/probe-now
GET    /admin/upstream-keys/{id}/metrics            # 24h sparkline
GET    /admin/upstream-keys/{id}/recent-requests    # 最近调用明细
GET    /admin/upstream-keys/{id}/error-breakdown    # 错误码分布
GET    /admin/upstream-keys/{id}/model-states       # per-(Key, Model) 冷却态
```

### 管理 — API Key（下游 sk-fwr-）
```
GET    /admin/api-keys
POST   /admin/api-keys
PATCH  /admin/api-keys/{id}
DELETE /admin/api-keys/{id}
POST   /admin/api-keys/{id}/rotate
GET    /admin/api-keys/{id}/reveal                  # 解密拿完整 token
```

### 管理 — 模型
```
GET    /admin/models
POST   /admin/models
PATCH  /admin/models/{id}
DELETE /admin/models/{id}
POST   /admin/models/sync                # 从 Fireworks 拉最新模型清单
POST   /admin/models/batch-status        # 批量启用/禁用
```

### 管理 — 日志 / 统计
```
GET /admin/logs/requests        # 含 7 维筛选：period/status/stream/model/upstream_key_id/api_key_id/request_id
GET /admin/logs/probes
GET /admin/stats/overview
GET /admin/stats/today
GET /admin/stats/top            # dimension=api_key|model|upstream
GET /admin/stats/timeseries
GET /admin/stats/keys-health    # 稳定性 Top/Bottom
GET /admin/stats/request-trace  # 散点图数据
GET /admin/stats/flow-sankey    # 桑基图数据
```

### 管理 — 系统设置
```
GET   /admin/settings
PATCH /admin/settings
```

### 公开
```
GET /system/info        # 不需鉴权，登录前可探测可用调度策略等
GET /healthz
GET /readyz
```

## 🗂️ 项目结构

```
.
├── app/                              # 后端
│   ├── main.py                       # FastAPI 入口 + lifespan + APIError 处理器
│   ├── config.py                     # pydantic-settings
│   ├── db.py                         # SQLAlchemy async 引擎
│   ├── crypto.py                     # Fernet 加密
│   ├── api/                          # HTTP 路由
│   │   ├── deps.py                   # 鉴权依赖 + APIError
│   │   ├── admin_auth.py             # 单密码登录 + 改密
│   │   ├── admin.py                  # 上游/下游 Key、日志、统计
│   │   ├── admin_models.py           # 模型管理
│   │   ├── admin_metrics.py          # per-key 监控
│   │   ├── admin_settings.py         # 系统设置 KV
│   │   └── system.py                 # 公开元信息
│   ├── gateway/                      # OpenAI/Anthropic 兼容网关
│   │   ├── router.py                 # /v1/* OpenAI 端点
│   │   ├── anthropic.py              # /v1/messages 协议翻译
│   │   ├── proxy.py                  # 流式转发 + 失败切换 + 计费
│   │   └── errors.py                 # 统一错误结构 + 兜底解析
│   ├── services/
│   │   ├── fireworks.py              # Fireworks 客户端
│   │   ├── scheduler.py              # 7 种调度策略
│   │   ├── cooldown.py               # 错误码差异化退避 + 关键词触发
│   │   ├── metrics.py                # 5min 桶聚合 + 物化字段 worker
│   │   ├── balance.py                # 余额探针
│   │   ├── upstream.py               # 上游 Key 入库
│   │   ├── models.py                 # 模型映射 + 同步
│   │   ├── settings.py               # KV 设置
│   │   └── session.py                # bcrypt + session JWT
│   ├── models/                       # SQLAlchemy 模型
│   │   ├── upstream_key.py           # 含物化字段
│   │   ├── api_key.py                # 含 token_encrypted
│   │   ├── request_log.py            # 含 api_key_preview
│   │   ├── model.py / probe_history.py / key_model_state.py /
│   │   ├── key_metric_bucket.py / system_setting.py
│   └── tasks/                        # APScheduler 任务
├── alembic/                          # 数据库迁移（v1 → v4）
├── frontend/                         # Vue 3 SPA
│   └── src/
│       ├── views/                    # 8 个页面
│       │   ├── Login.vue             # 单密码登录
│       │   ├── AppLayout.vue         # 主布局 + 改密码弹窗
│       │   ├── Dashboard.vue         # KPI + 趋势 + Key 健康
│       │   ├── UpstreamKeys.vue      # 余额条 + sparkline + 双圆点 + 详情抽屉
│       │   ├── ApiKeys.vue           # 含「复制完整 token」
│       │   ├── Models.vue            # 模型映射 + 同步 + 批量启用
│       │   ├── RequestLogs.vue       # newAPI 风格筛选 + 可展开
│       │   ├── RequestTrace.vue      # 散点 + 桑基
│       │   ├── ProbeLogs.vue
│       │   └── Settings.vue          # 7 种策略 + 冷却参数 + 探针
│       ├── components/
│       │   ├── KpiCard.vue           # 紧凑 KPI 卡（10 色预设）
│       │   ├── StatusDot.vue         # 圆点状态指示
│       │   ├── BalanceBar.vue        # 余额数字 + 进度条 + 百分比
│       │   ├── Sparkline.vue         # 纯 SVG 24h sparkline
│       │   └── HealthSignal.vue      # 双圆点 + sparkline 组合
│       ├── stores/                   # Pinia (auth / theme)
│       ├── styles/globals.css        # Teal 主题 + Glass + Mesh
│       ├── api/                      # axios 客户端
│       └── router/
├── scripts/
│   ├── bootstrap.py                  # 生成 .env（含 ADMIN_PASSWORD_HASH）
│   └── issue_token.py                # CLI 颁发 API Key
├── docs/
│   └── PRD.md                        # 产品需求文档
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## 🧪 测试覆盖

| 测试场景 | 结果 |
| --- | --- |
| OpenAI chat completion（非流式） | ✅ |
| OpenAI chat completion（流式 + usage） | ✅ |
| Anthropic /v1/messages（非流式） | ✅ |
| Anthropic /v1/messages（流式 5 种 event） | ✅ |
| 路径别名 /openai/v1/* | ✅ |
| 路径别名 /api/v1/* | ✅ |
| 缺 model 参数 → 400 | ✅ |
| 未注册模型 → 404 + model_not_found | ✅ |
| 无效 token → 401 + authentication_error | ✅ |
| 全 Key 不可用 → 503 + service_unavailable | ✅ |
| 错误响应格式（无 detail 包裹） | ✅ |
| 改密码 → 旧密码失败 / 新密码成功 | ✅ |
| 日志多维筛选（period+model+stream） | ✅ |
| Key 列表查询零额外开销 | ✅ |

## 🗺️ 路线图

- ✅ **v1 (M1-M3)**：基础网关 + 用户系统 + 模型映射 + Alembic
- ✅ **v2**：单密码管理端 + 监控仪表盘 + 调度轨迹散点 + 桑基图
- ✅ **v3**：物化字段 DB 优化 + 余额进度条 + sparkline + 双圆点
- ✅ **v4**：API Key token_encrypted（可复制完整 token）+ 改密码
- ✅ **v5**：newAPI 风格调用日志（可展开计费公式 + 7 维筛选 + 彩色 chip）
- ✅ **v6**：错误处理重构（OpenAI 标准 + 错误码差异化退避 + 关键词触发自动禁用）+ 路径别名 + Anthropic 协议翻译
- ⏳ Gemini 协议 `/v1beta/models/{model}:generateContent`
- ⏳ 告警通知（飞书/钉钉 webhook）
- ⏳ Prometheus metrics 端点
- ⏳ 多上游 Provider（DeepSeek / Together / Groq 直连）

## 🤝 鸣谢

设计参考：
- [one-api](https://github.com/songquanpeng/one-api) — 统一错误结构 + `GeneralErrorResponse` 兜底解析
- [new-api](https://github.com/QuantumNous/new-api) — Anthropic 协议路径 + 关键词触发禁用 + Channel 错误分类
- [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) — `fill_first` 策略命名 + per-(Auth, Model) 冷却思路
- [sub2api](https://github.com/Wei-Shaw/sub2api) — Teal + Glass-morphism UI 设计语言

## 📄 许可证

MIT
