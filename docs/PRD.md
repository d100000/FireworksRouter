# FireworkRouter 产品需求文档（PRD）

| 项目 | FireworkRouter — Fireworks.ai OpenAI 兼容中转分发系统 |
| --- | --- |
| 版本 | v1.0 (Draft) |
| 日期 | 2026-05-22 |
| 作者 | bobdong |
| 状态 | 草案 / 待评审 |
| 参考 | [Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api)、[one-api](https://github.com/songquanpeng/one-api)、[new-api](https://github.com/Calcium-Ion/new-api) |

---

## 1. 项目背景与目标

### 1.1 背景

[Fireworks.ai](https://fireworks.ai/) 提供高性价比、低延迟的开源/闭源大模型推理服务（DeepSeek、Qwen、Llama、Kimi、MiniMax、GLM、FLUX 等），是 OpenAI/Claude 之外重要的推理供应商。但在企业 / 多人 / 多应用共享场景下存在以下问题：

1. **账户隔离差**：100+ 个 Fireworks 账户的 Key 分散在不同人手上，无法统一调度。
2. **余额黑盒**：单个 Key 余额耗尽后请求直接失败，缺乏统一的健康监控。
3. **下游接入成本高**：业务侧已大量使用 OpenAI SDK，需要 OpenAI 兼容层。
4. **配额/计费不可控**：无法按用户、模型、应用做配额隔离与二次计费。
5. **可观测性缺失**：缺乏对调用量、Token 消耗、错误率、成本的统一视图。

### 1.2 目标

构建一个基于 Python 的、**OpenAI API 兼容**的中转网关系统，专门用于聚合、调度、计费、监控 Fireworks.ai 的多账户 API Key。

**核心目标**

- **G1**：100% 兼容 OpenAI 客户端 SDK（无需改造业务代码）
- **G2**：支持 ≥100 个 Fireworks 上游 Key 的池化管理与自动调度
- **G3**：每个上游 Key 的余额、健康状态实时可见，余额不足自动禁用
- **G4**：下游用户通过本系统颁发的 `sk-` 开头 Key 调用，做到用户隔离、模型白名单、配额控制
- **G5**：完整的调用日志、统计、计费、告警

### 1.3 非目标

- ❌ 不做 Anthropic Claude / OpenAI 原生 / Google Gemini 等其他厂商的转发（专注 Fireworks）
- ❌ 不实现自建支付（一期不涉及面向 C 端的充值场景，二期再说）
- ❌ 不替代 Fireworks 平台本身的能力（如模型部署、微调、数据集管理）

---

## 2. 名词定义

| 术语 | 英文 | 说明 |
| --- | --- | --- |
| 上游 Key | Upstream Key / Channel Key | 真实的 Fireworks API Key（`fw_xxx`），由本系统统一持有 |
| 下游 Key / 分发 Key | User Token | 本系统颁发给最终用户的 OpenAI 风格 Key（`sk-fwr-xxx`） |
| 渠道 | Channel | 一组上游 Key 的逻辑分组，可配置可用模型、优先级 |
| 渠道组 | Channel Group | 多个渠道的集合，便于按业务/客户分流 |
| 用户分组 | User Group | 用户的等级（如普通/高级/企业），决定可访问的渠道组与价格倍率 |
| 模型映射 | Model Mapping | 暴露给下游的模型名 ↔ Fireworks 真实模型路径的映射关系 |
| 网关 | Gateway | 处理 `/v1/*` 兼容请求并转发至 Fireworks 的核心模块 |
| 调度器 | Scheduler | 在多个候选上游 Key 中选择一个执行请求的模块 |
| 探针 | Probe | 周期性检测上游 Key 健康度与余额的后台任务 |

---

## 3. 用户角色与权限

| 角色 | 权限 |
| --- | --- |
| **超级管理员（Root）** | 全部权限。可创建管理员、修改任何系统设置、查看所有日志。系统初始化时创建唯一一个。 |
| **管理员（Admin）** | 管理上游 Key、渠道、模型、用户、价格；查看全量日志统计。不能删除其他管理员、不能修改系统底层配置。 |
| **运营（Operator）** | 只读 + 用户管理、充值。无法触碰上游 Key、渠道等技术资源。 |
| **普通用户（User）** | 仅管理自己的下游 Key、查看自己的用量与账单、修改自己的资料。 |

权限通过 RBAC 实现，菜单与 API 双重校验。

---

## 4. 系统架构

### 4.1 架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│                  下游 OpenAI 兼容客户端 (SDK / cURL)              │
└────────────────────────┬─────────────────────────────────────────┘
                         │ Bearer sk-fwr-xxx
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│ FastAPI 网关层                                                    │
│  ├ 鉴权中间件 (User Token 校验、IP 白名单)                        │
│  ├ 限流中间件 (用户/Key/全局 三层)                                │
│  ├ 模型路由 (模型映射、模型白名单校验)                            │
│  ├ 配额预扣 (基于估算的 token 数预扣额度)                         │
│  ├ 调度器 (从可用上游 Key 中选一)                                 │
│  └ 转发代理 (httpx AsyncClient + SSE 流式)                        │
└────────────────┬────────────────────────────┬────────────────────┘
                 │                            │
                 ▼                            ▼
       ┌─────────────────┐         ┌──────────────────────┐
       │  PostgreSQL     │         │  Fireworks.ai 上游   │
       │  - users        │         │  api.fireworks.ai    │
       │  - upstream_keys│         └──────────────────────┘
       │  - channels     │
       │  - logs         │
       │  - billing      │         ┌──────────────────────┐
       └─────────────────┘         │   Redis 7            │
                                   │   - 限流计数         │
       ┌─────────────────┐         │   - Key 健康/余额缓存│
       │ APScheduler /   │         │   - 分布式锁         │
       │ arq Worker      │◀───────▶│   - SSE buffer       │
       │  - 余额探针      │         └──────────────────────┘
       │  - 健康检查      │
       │  - 日志归档      │
       │  - 模型同步      │
       └─────────────────┘
```

### 4.2 技术栈

| 层 | 选型 | 理由 |
| --- | --- | --- |
| 语言 | Python 3.11+ | 用户指定。生态成熟、开发效率高、异步 I/O 足够支撑 IO 密集的转发场景 |
| Web 框架 | **FastAPI** + Uvicorn (workers) + Gunicorn | 异步、OpenAPI 自动文档、性能优秀 |
| HTTP 客户端 | **httpx** AsyncClient | 原生 async、支持 HTTP/2、支持 SSE 流式转发 |
| ORM | **SQLAlchemy 2.0** (async) + Alembic | 异步 ORM 事实标准，迁移工具成熟 |
| 数据库 | **PostgreSQL 15+** | 事务、JSONB 字段、强类型 |
| 缓存/限流/锁 | **Redis 7** | 限流计数、Key 状态缓存、分布式锁、Pub/Sub |
| 定时任务 | **APScheduler**（单机）/ **arq**（分布式） | 余额探针、模型同步等周期任务 |
| 加密 | `cryptography` (Fernet) | 上游 Key 加密存储 |
| 密码哈希 | `passlib[bcrypt]` | 用户密码 |
| JWT | `python-jose` | 后台鉴权 |
| 日志 | `loguru` + JSON 格式输出 | 结构化日志 |
| 监控 | Prometheus client + OpenTelemetry | metrics / traces |
| 前端 | **Vue 3** + Vite + **Naive UI** / Element Plus + Pinia | 与 sub2api 一致的现代栈 |
| 部署 | Docker / Docker Compose / K8s Helm Chart | 一键部署 |

---

## 5. 功能需求

### 5.1 用户系统

#### 5.1.1 注册与登录
- 支持邮箱 + 密码注册（一期）
- 可选：邀请码注册（管理员可在系统设置中强制要求）
- 可选：第三方 OAuth（GitHub、Linux DO 等）（二期）
- 密码使用 bcrypt 哈希存储，cost factor ≥ 12
- 登录返回 JWT access_token（默认 2h）+ refresh_token（默认 7d）
- 登录失败连续 5 次锁定 15 分钟
- 强制邮箱验证（可在系统设置中开关）
- 集成 Cloudflare Turnstile 防刷（可选）

#### 5.1.2 用户分组
- 系统预置：`default`（默认）、`vip`（高级）、`enterprise`（企业）三档
- 管理员可新增/修改分组
- 每个分组配置：
  - 价格倍率（rate_multiplier）
  - 可访问的渠道组列表
  - 单用户并发上限
  - 默认初始额度
  - 单次最大 max_tokens 限制

#### 5.1.3 用户额度
- 用户额度以"美元"为基本单位，内部存为 `int64` 微元（1 美元 = 1,000,000 微元）避免浮点误差
- 管理员可手动充值/扣减
- 每次调用后实时扣费
- 余额低于阈值时邮件提醒（阈值用户可自配）
- 余额 ≤ 0 拒绝新请求（流式请求保留 grace 完成当前已开始的）

#### 5.1.4 用户管理（管理员侧）
- 列表（分页 + 搜索：邮箱/用户名/分组/状态）
- 详情：基本信息、额度、近 30 天用量、近 100 条调用日志
- 操作：编辑（分组、状态）、充值、踢出登录、删除（软删除）
- 批量：导出 CSV、批量充值

---

### 5.2 上游 Key 池管理 ⭐（核心模块）

#### 5.2.1 数据字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | int64 PK | |
| name | string(64) | 备注名（如"主账号-001"） |
| key_encrypted | bytes | Fernet 加密存储的 `fw_xxx` |
| key_hash | string(64) | SHA-256，用于查重 |
| key_preview | string(16) | 脱敏后前 4 + 后 4，如 `fw_3oum...ghX` |
| account_id | string | Fireworks 账户 ID（首次连通性测试时回填） |
| channel_id | int64 FK | 所属渠道 |
| status | enum | `active`、`disabled`（手动）、`auto_disabled`（余额）、`unhealthy`（故障）、`testing` |
| priority | int | 优先级（数值越大越优先） |
| weight | int | 加权随机权重，默认 100 |
| concurrency_limit | int | 单 Key 最大并发数，0=不限 |
| qps_limit | int | 每秒请求数上限，0=不限 |
| rpm_limit | int | 每分钟请求数上限 |
| tpm_limit | int | 每分钟 Token 数上限 |
| model_whitelist | jsonb | 允许使用的模型 ID 列表（空=全部允许） |
| model_blacklist | jsonb | 禁用的模型 ID 列表 |
| balance_usd | numeric(12,6) | 最近一次探测的余额（美元） |
| balance_updated_at | timestamp | 余额最后更新时间 |
| suspend_state | string | Fireworks 返回的 suspendState |
| consecutive_failures | int | 连续失败次数 |
| total_requests | int64 | 累计请求数 |
| total_tokens | int64 | 累计 Token 数 |
| total_cost_usd | numeric(12,6) | 累计花费 |
| last_used_at | timestamp | 最近一次成功调用时间 |
| auto_disable_reason | string | 自动禁用原因 |
| disabled_at | timestamp | 禁用时间 |
| notes | text | 管理员备注 |
| created_at / updated_at | timestamp | |

#### 5.2.2 添加方式

1. **单条添加**：表单输入 name + key + 渠道 + 各项限制
2. **批量粘贴**：textarea 粘贴多行 `fw_xxx`（每行一个，可附 `,name` 后缀），后端自动逐条建立连接性测试后入库
3. **CSV / Excel 导入**：列：`key, name, channel, priority, weight, model_whitelist, notes`
4. **API 导入**：管理员 API 可程序化导入

入库流程：
1. 加密存储
2. 检测 `key_hash` 是否重复（重复直接拒绝）
3. 调用 `GET https://api.fireworks.ai/v1/accounts` 自动发现该 Key 对应的 `account_id`（返回数组，通常取第一个 `accounts/<id>`）并回写到 `account_id` 字段
4. 调用 `GET https://api.fireworks.ai/v1/accounts/{account_id}?readMask=*` 获取账户元信息（状态、邮箱、创建时间）
5. 调用一次余额探测（见 5.7）
6. 调用一次模型列表同步（见 5.4）
7. 标记 `active`，加入调度池

#### 5.2.3 操作

- 启用 / 禁用 / 强制健康检查
- 编辑限制项
- 调整优先级、权重、并发
- **测试连通性按钮**：实时调用一次 `models.list` 验证可用性
- **查询余额按钮**：实时触发余额探测
- **批量操作**：批量启用 / 禁用 / 调权重 / 删除 / 改渠道
- **导出**：导出当前列表（不含明文 Key）
- **复制原始 Key**：超级管理员才能查看明文，操作记录审计日志

#### 5.2.4 列表筛选

- 状态、渠道、余额范围、最后使用时间、连续失败次数
- 排序：按余额、优先级、最后使用、累计花费

#### 5.2.5 健康检查

- 连续失败次数 `consecutive_failures` ≥ `auto_unhealthy_threshold`（默认 5）时自动置为 `unhealthy`
- `unhealthy` Key 不会被调度，但会被探针每 N 分钟重试，恢复后回到 `active`
- 任何 HTTP 4xx（除 429）均计入失败；5xx 与网络错误计入失败
- 429 单独计数，触发 `rpm_limit` 软退避而非失败

---

### 5.3 下游 API Key（分发 Key）

#### 5.3.1 数据字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | int64 PK | |
| user_id | int64 FK | 所属用户 |
| name | string(64) | 备注 |
| token | string(64) | 明文形如 `sk-fwr-<32 base62>` |
| token_hash | string(64) | SHA-256 索引用 |
| status | enum | `active` / `disabled` / `expired` |
| expires_at | timestamp NULL | 过期时间，NULL=永不 |
| remaining_quota | numeric(12,6) | 剩余额度（NULL = 跟随用户额度） |
| used_quota | numeric(12,6) | 已用额度 |
| unlimited_quota | bool | 是否不限额（仅消耗用户余额） |
| max_tokens_per_request | int | 单次请求 max_tokens 上限 |
| allowed_models | jsonb | 允许的模型列表（空=用户分组允许的全部） |
| allowed_ips | jsonb | IP 白名单（CIDR），空=不限 |
| allowed_origins | jsonb | Referer/Origin 白名单（用于浏览器端） |
| rpm_limit | int | 每分钟请求限制 |
| concurrency_limit | int | 并发上限 |
| stream_enabled | bool | 是否允许流式请求 |
| created_at / last_used_at | timestamp | |

#### 5.3.2 用户操作

- 创建：表单选项（名称、可选模型多选、过期时间、IP/Referer 白名单、独立配额）
- 复制 token（只在创建后**一次性显示**明文，后续只显示前缀）
- 启用 / 禁用 / 重置（生成新 token）
- 删除
- 查看：该 Key 的近 30 天用量曲线、调用日志（仅自己的）

#### 5.3.3 校验流程（请求进入网关时）

1. 解析 `Authorization: Bearer sk-fwr-xxx` → 计算 hash → 查询 token 表
2. 校验状态、过期、IP、Referer
3. 校验请求体 `model` 是否在 `allowed_models` 内
4. 校验请求体 `max_tokens` ≤ `max_tokens_per_request`
5. 校验 `stream` 与 `stream_enabled`
6. 校验用户 / Key 配额 ≥ 预估成本（见 5.8）
7. 校验 RPM / 并发
8. 全部通过 → 进入调度

---

### 5.4 模型管理

#### 5.4.1 模型数据字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | int64 PK | |
| public_name | string | 暴露给下游的模型名（如 `kimi-k2-instruct-0905`）|
| fireworks_path | string | Fireworks 真实路径（如 `accounts/fireworks/models/kimi-k2-instruct-0905`）|
| category | enum | `chat` / `completion` / `embedding` / `image` / `audio` / `rerank` / `vision` |
| context_length | int | 上下文窗口长度 |
| max_output_tokens | int | 最大输出 token |
| input_price_per_1m | numeric(12,6) | 每百万输入 token 价格（美元） |
| output_price_per_1m | numeric(12,6) | 每百万输出 token 价格（美元） |
| cached_input_price_per_1m | numeric(12,6) | 缓存命中价（可选） |
| supports_streaming | bool | 是否支持流式 |
| supports_tools | bool | 是否支持函数调用 |
| supports_vision | bool | 是否支持图像输入 |
| supports_reasoning | bool | 是否支持思考链 |
| status | enum | `active` / `disabled` |
| sort_order | int | 列表排序 |
| description | text | 简介 |
| created_at / updated_at | timestamp | |

#### 5.4.2 同步策略

- 定时任务（默认每日 04:00）调用任一可用上游 Key 的 `GET https://api.fireworks.ai/v1/models` 拉取最新列表
- 新增模型自动入库，状态为 `disabled`（避免突然出现未定价的模型）
- 已下线模型标记 `disabled` 但保留历史
- 价格信息需手动维护或从 https://fireworks.ai/models 抓取（一期手动）

#### 5.4.3 模型映射 / 伪装

- 支持将一个 `public_name`（如 `gpt-4o-mini`）映射到 Fireworks 的某个真实模型（如 `accounts/fireworks/models/qwen3-32b`）
- 用于让现存的 OpenAI 业务无感切换
- 在请求体中替换 `model` 字段；响应中根据配置决定是否回写原 `public_name`

#### 5.4.4 模型分类视图

- chat、vision、embedding、image、audio、rerank 分 Tab
- 每个模型展示：可用渠道数量、近 24h 调用量、平均延迟、成本

---

### 5.5 渠道与调度

#### 5.5.1 渠道（Channel）

一个渠道是一组上游 Key 的逻辑分组，配置：

| 字段 | 说明 |
| --- | --- |
| name | 渠道名 |
| description | 描述 |
| status | active / disabled |
| priority | 调度优先级 |
| supported_models | 该渠道支持的模型列表（空=全部） |
| extra_params | 注入到上游请求的额外参数（如固定 `service_tier`、`reasoning_effort`） |
| base_url | 默认为 `https://api.fireworks.ai/inference/v1`，可指向自建反代 |
| proxy_url | HTTP/HTTPS/SOCKS5 代理（用于解决国内访问问题） |
| timeout_ms | 超时时间 |
| retry_on_failure | 失败时是否切换其他 Key 重试 |

#### 5.5.2 渠道组（Channel Group）

- 多个渠道的集合
- 用户分组绑定渠道组：一个用户分组可对应一个或多个渠道组
- 调度顺序：渠道组内按优先级 → 渠道内按调度策略

#### 5.5.3 调度策略

每个渠道可独立配置，候选策略：

| 策略 | 说明 |
| --- | --- |
| `weighted_random` | 按 `weight` 加权随机（默认） |
| `round_robin` | 严格轮询 |
| `priority` | 严格按 `priority` 降序，前者不可用才用后者 |
| `least_used` | 选择 `last_used_at` 最早的 |
| `most_balance` | 选择 `balance_usd` 最高的（适合余额差异大场景） |
| `session_sticky` | 按 `prompt_cache_key` / `user` 字段一致性哈希，相同会话粘到同一 Key（提升 prompt cache 命中率） |

#### 5.5.4 调度算法（简化版）

```
def pick_upstream(model, user, request):
    # 1. 找用户分组绑定的渠道组
    channel_groups = user.user_group.channel_groups

    # 2. 在每个渠道组内，按渠道优先级排序
    for cg in channel_groups:
        for channel in cg.channels.order_by(priority desc):
            # 3. 渠道必须支持该模型
            if model.public_name not in channel.supported_models: continue

            # 4. 候选 Key:active 状态、未达并发上限、未达 RPM、模型不在 blacklist、在 whitelist
            candidates = filter_keys(channel)
            if not candidates: continue

            # 5. 按渠道策略选一个
            key = strategy.pick(candidates)
            return key, channel

    raise NoAvailableKey()
```

#### 5.5.5 失败重试与切换

- 单次请求失败（5xx / 网络 / 429 后端饱和）：
  - 立即从候选池中**剔除该 Key** 并选下一个
  - 最多重试 `max_retry`（默认 3）次
  - 全部失败返回上游错误 + 内部 `x-fwr-retry-count` 响应头
- 流式请求一旦开始（已收到首字节），不再重试

---

### 5.6 调用网关（核心转发）

#### 5.6.1 暴露的 OpenAI 兼容端点

Base URL：`https://your-domain/v1`

| 方法 | 路径 | 说明 | 流式 |
| --- | --- | --- | --- |
| POST | `/v1/chat/completions` | 对话补全 | ✅ SSE |
| POST | `/v1/completions` | 文本补全 | ✅ SSE |
| POST | `/v1/embeddings` | 文本嵌入 | ❌ |
| POST | `/v1/images/generations` | 图像生成（FLUX 等） | ❌ |
| POST | `/v1/audio/speech` | 文本转语音 | ❌（块流） |
| POST | `/v1/audio/transcriptions` | 语音转文本 | ❌ |
| POST | `/v1/rerank` | 重排序 | ❌ |
| GET | `/v1/models` | 可用模型列表（仅返回当前用户可访问的） | ❌ |

兼容头：`Authorization: Bearer sk-fwr-xxx`、`Content-Type: application/json`。

#### 5.6.2 请求处理流水线

```
[Receive]
  → [Auth & RateLimit]
  → [Parse Body]
  → [Model Resolve & ACL]
  → [Quota Pre-check]
  → [Pick Upstream]
  → [Acquire Concurrency Semaphore]
  → [Rewrite Body (model path)]
  → [Forward to Fireworks]
     ↓
  [Stream Mode] → 边收边转，缓冲累计 token
  [Non-Stream]  → 整体接收
     ↓
  → [Token Accounting & Billing]
  → [Release Semaphore]
  → [Persist Log]
  → [Return to Client]
```

#### 5.6.3 流式（SSE）转发要点

- 使用 `httpx.AsyncClient.stream("POST", url, ...)` + `StreamingResponse`
- 透传 `Content-Type: text/event-stream`、`Cache-Control: no-cache`
- 处理客户端断开：`request.is_disconnected()` 检查，及时取消上游
- 流尾从最后一个 chunk 的 `usage` 字段（Fireworks 流式默认带 `stream_options.include_usage`）提取真实 token
- 若上游未返回 usage，则用 tiktoken / Fireworks tokenizer 估算
- 错误处理：流式开始后出错只能向客户端发送 `data: {"error": ...}` chunk

#### 5.6.4 请求/响应改写

- **请求改写**：将下游传入的 `model` 替换为 `model.fireworks_path`；可选注入渠道的 `extra_params`
- **响应改写**：可配置是否将响应中的 `model` 字段改写回 `public_name`（默认开启，避免暴露真实路径）
- **流式改写**：仅对首块的 `model` 字段做替换

#### 5.6.5 错误透传与映射

| 上游状态 | 处理 |
| --- | --- |
| 200 | 正常转发 |
| 400/422 | 透传给客户端（请求错误） |
| 401/403 | **标记 Key 异常** → 自动禁用 → 切换重试 |
| 402 / suspendState=DELINQUENT | **标记 Key 余额不足** → `auto_disabled` → 切换重试 |
| 429 | 按 Retry-After 软退避；触发 Key 的 RPM 节流 |
| 5xx | 失败计数 +1；切换重试 |
| 网络异常 / 超时 | 失败计数 +1；切换重试 |

#### 5.6.6 自定义响应头

- `X-Fwr-Upstream-Key-Id`: 实际使用的上游 Key ID（仅管理员可见，可关闭）
- `X-Fwr-Channel`: 渠道名
- `X-Fwr-Latency-Ms`: 网关额外延迟
- `X-Fwr-Cost-USD`: 本次调用成本
- `X-Fwr-Request-Id`: 请求唯一 ID（同步写入日志）

---

### 5.7 余额监控与自动禁用 ⭐（核心模块）

> 用户特别强调的功能：100+ Key 的余额实时监控 + 余额不足自动禁用。

#### 5.7.1 探测目标

- **余额（balance_usd）**：账户剩余可用美元
- **健康状态**：Key 是否被吊销 / 撤回 / 限频
- **账户状态（suspendState）**：Fireworks 账户暂停状态（如 `DELINQUENT`、`OVER_QUOTA`、`SUSPENDED`）

#### 5.7.2 探测方式（混合策略）

> **重要发现（来自实际 API 实测，2026-05-22）**：Fireworks 的"余额"在 API 层面体现为 `/v1/accounts/{account_id}/quotas` 中的 **`monthly-spend-usd`** 配额项，结构为 `{ maxValue, value, usage }`，其中 `value - usage` 即本月剩余可用美元。这是 Fireworks 账户上"花费上限 - 已花费"的体现，相当于其他平台的"剩余余额"。

采取**多层探测**：

1. **Level 1 — Account 状态探测（最轻、最优先）**
   - `GET https://api.fireworks.ai/v1/accounts/{account_id}?readMask=*`
   - 解析 `state` 与 `suspendState`：
     - `state=READY` 且 `suspendState=UNSUSPENDED` → 健康
     - `suspendState ∈ {DELINQUENT, OVER_QUOTA, SUSPENDED}` → **立即禁用**（auto_disabled，原因：欠费/超额/暂停）
     - 其他非健康 `state`（CREATING / UPDATING / DELETING） → 暂停调度但不计失败
2. **Level 2 — Quota 余额查询（核心）⭐**
   - `GET https://api.fireworks.ai/v1/accounts/{account_id}/quotas`
   - 在响应数组中筛出 `name` 以 `/quotas/monthly-spend-usd` 结尾的项
   - 计算 `remaining_usd = float(item.value) - float(item.usage)`，写入 `upstream_keys.balance_usd`
   - 同时记录：
     - `serverless-inference-rpm` → 同步到 `upstream_keys.rpm_limit`（若用户未自定义）
     - `eval-protocol-free-daily-credits` → 额外记录"每日免费额度"
   - 若 `remaining_usd <= min_balance_threshold_usd` → 自动禁用
3. **Level 3 — Probe 调用（兜底，可选）**
   - 当 Level 1/2 接口异常或返回不完整时，发送 `max_tokens=1, model=<最便宜模型>` 的 chat completion 探针请求
   - 返回 402/403/账户被禁用 → 标记不可用
   - 探针调用本身记账（成本 ≤ $0.0001），可在系统设置中关闭
4. **Level 4 — 实扣校准**
   - 每次成功调用后 `upstream_keys.balance_usd -= cost`（本地估算）
   - 与 Level 2 的真实 quota 数值在每次定时探测时对齐
   - 防止 quota 接口短暂不可用时的余额误判

#### 5.7.3 调度频率

- **常规探测**：默认每 **15 分钟** 跑一次（可配置 5 / 15 / 30 / 60 分钟）
- **使用即触发**：单 Key 累计花费 ≥ `incremental_check_threshold`（默认 $0.5）触发一次 Level 1 探测
- **错误触发**：任一调用返回 401/402/403 → 立即触发探测
- **新 Key 入库**：立即执行一次完整探测

#### 5.7.4 任务调度（APScheduler）

```python
# 伪代码
@scheduler.scheduled_job('interval', minutes=15, max_instances=1, coalesce=True)
async def probe_all_keys():
    keys = await get_keys_to_probe()  # 状态 in (active, unhealthy, auto_disabled)
    sem = asyncio.Semaphore(20)       # 并发上限 20，避免压垮 Fireworks 控制面
    async def _probe(k):
        async with sem:
            await probe_key(k)
    await asyncio.gather(*(_probe(k) for k in keys))
```

- 单次任务超时：5 分钟（100 个 Key × 平均 1s/Key + 安全边际）
- 使用 Redis 分布式锁防止多实例重复运行
- 探测结果写入 `upstream_keys.balance_usd / balance_updated_at / suspend_state`，并写入审计表 `key_probe_history`

#### 5.7.5 自动禁用规则

| 条件 | 动作 |
| --- | --- |
| `suspendState` ∈ {DELINQUENT, OVER_QUOTA, SUSPENDED} | `status = auto_disabled`、`auto_disable_reason = <state>` |
| `balance_usd < min_balance_threshold`（默认 $0.5） | `status = auto_disabled`、`auto_disable_reason = "low_balance"` |
| 探针 HTTP 401 / 403 | `status = auto_disabled`、`auto_disable_reason = "auth_failed"` |
| 探针连续 5 次失败 | `status = unhealthy`、`auto_disable_reason = "consecutive_failures"` |

#### 5.7.6 自动恢复

- `auto_disabled` 与 `unhealthy` 状态的 Key 仍会被探针扫描（频率减半）
- 一旦探测到 `suspendState` 健康且余额 ≥ 阈值，自动恢复为 `active`
- 管理员可禁用自动恢复（仅在手动操作后恢复）

#### 5.7.7 告警通知

- 任何 Key 进入 `auto_disabled` 状态触发：
  - 邮件通知（管理员）
  - Webhook 通知（飞书 / 钉钉 / Slack / 自定义 URL）
  - 管理后台站内信
- 全部 Key 不可用时升级告警（"分发系统已无可用上游 Key！"）

#### 5.7.8 余额视图

- 后台 Dashboard 显示：
  - 上游 Key 总余额（所有 active Key 求和）
  - 近 7/30 天余额下降趋势图
  - 余额 Top 10 / Bottom 10 排行
  - 距余额耗尽预估时间（基于近 7 天日均消耗推算）

---

### 5.8 计费与配额

#### 5.8.1 计费公式

```
单次成本（美元） =
    (input_tokens   * model.input_price_per_1m   / 1_000_000) +
    (output_tokens  * model.output_price_per_1m  / 1_000_000) +
    (cached_tokens  * model.cached_input_price_per_1m / 1_000_000)
对用户扣费 = 单次成本 * channel.rate_multiplier * user_group.rate_multiplier
```

#### 5.8.2 预扣 / 实扣

- **预扣**：请求开始前，按 `max_tokens` 上限估算最大成本，预扣用户额度
- **实扣**：调用完成后，按真实 `usage` 重新计算，多退少补
- 若预扣后用户余额不足，拒绝请求（429 + `insufficient_quota` error code）

#### 5.8.3 计费记录

每次调用产生一条 `billing_record`：

| 字段 | 说明 |
| --- | --- |
| id | PK |
| user_id | 用户 |
| user_token_id | 下游 Key |
| upstream_key_id | 上游 Key |
| channel_id | 渠道 |
| model_id | 模型 |
| request_id | 唯一 ID |
| input_tokens / output_tokens / cached_tokens | Token 数 |
| raw_cost_usd | 上游成本 |
| billed_cost_usd | 实际扣费（应用各种倍率后） |
| stream | 是否流式 |
| latency_ms | 总耗时 |
| ttft_ms | 首字延迟（流式） |
| status_code | HTTP 状态 |
| error_code | 业务错误码 |
| created_at | 时间 |

#### 5.8.4 配额维度

- 用户级配额（user.quota）
- Key 级配额（user_token.remaining_quota，可独立或跟随用户）
- 用户分组级配额（按分组限制日/月总额）
- 模型级配额（按模型限制单用户的日/月用量）

---

### 5.9 日志与统计

#### 5.9.1 调用日志

- 实时写入 `request_logs` 表
- 字段：见 5.8.3 的 `billing_record`（合并存储）
- 完整 prompt / response 可选记录（隐私敏感，默认关闭，仅管理员可开启）
- 保留策略：明细 30/90/365 天可配置；聚合数据永久保留

#### 5.9.2 仪表盘（管理员）

- **概览卡片**：今日请求数、今日 Token、今日成本、活跃用户数、可用 Key 数、上游总余额
- **趋势图（24h / 7d / 30d）**：请求数、Token、成本、错误率、平均延迟、TTFT
- **Top 榜**：Top 用户（按花费）、Top 模型（按调用量）、Top 上游 Key（按花费）
- **错误分布**：按错误码、按上游 Key
- **延迟分布**：p50 / p90 / p99 / p999

#### 5.9.3 用户侧仪表盘

- 我的余额、我的今日用量、我的近 30 天用量
- 我的 Key 列表 + 每个 Key 的用量
- 我的近 100 条调用日志（不含完整 prompt/response，除非用户自己开启）

#### 5.9.4 日志查询

- 多维筛选：时间范围、用户、Key、模型、上游 Key、状态码、模型
- 导出 CSV / JSON
- 单条详情：展开看完整请求/响应（若开启了记录）

---

### 5.10 系统设置

| 分类 | 项 | 说明 |
| --- | --- | --- |
| **基础** | site_name / logo / footer | 站点品牌 |
| | announcement | 公告（Markdown） |
| | registration_enabled | 是否开放注册 |
| | require_invitation_code | 是否需要邀请码 |
| | require_email_verification | 是否强制邮箱验证 |
| **限额** | default_user_quota_usd | 新用户初始额度 |
| | default_user_concurrency | 默认并发 |
| | default_token_rpm | 默认下游 Key RPM |
| **网关** | gateway_base_path | 默认 `/v1` |
| | global_max_retry | 失败重试次数（默认 3） |
| | default_upstream_timeout_ms | 默认上游超时 |
| | record_prompt_response | 是否记录完整请求/响应（敏感） |
| **探针** | probe_interval_minutes | 探测周期（默认 15） |
| | probe_concurrency | 并发数（默认 20） |
| | min_balance_threshold_usd | 自动禁用余额阈值（默认 0.5） |
| | probe_use_chat_completion | 是否启用兜底 chat probe |
| | probe_cheap_model | 兜底 probe 用的模型 |
| **告警** | alert_email_recipients | 收件人列表 |
| | alert_webhook_url | 飞书/钉钉/Slack Webhook |
| | alert_on_key_disabled | 单 Key 禁用是否告警 |
| | alert_on_all_keys_down | 全部 Key 不可用告警 |
| **邮件** | smtp_host / port / user / password / from_email / use_tls | SMTP 配置 |
| **安全** | jwt_secret | JWT 密钥（可在 .env，UI 只读） |
| | upstream_key_encryption_key | 上游 Key 加密密钥（同上） |
| | ip_blacklist / ip_whitelist | 全局 IP 名单 |
| | turnstile_site_key / secret | Cloudflare Turnstile |
| | cors_allowed_origins | CORS 白名单 |
| **代理** | global_proxy_url | 全局上游代理（解决国内访问） |

---

### 5.11 管理后台

#### 5.11.1 菜单结构

```
├─ 概览 Dashboard
├─ 用户管理
│   ├─ 用户列表
│   ├─ 用户分组
│   └─ 充值记录
├─ Key 管理
│   ├─ 上游 Key 池
│   ├─ 下游 Key（所有用户）
│   └─ Key 探测历史
├─ 渠道管理
│   ├─ 渠道
│   └─ 渠道组
├─ 模型管理
│   ├─ 模型列表
│   ├─ 模型映射
│   └─ 同步任务
├─ 日志与统计
│   ├─ 调用日志
│   ├─ 计费记录
│   └─ 异常日志
├─ 系统设置
│   ├─ 基础设置
│   ├─ 网关设置
│   ├─ 探针设置
│   ├─ 告警通知
│   ├─ 邮件 / SMTP
│   └─ 安全 / 代理
└─ 个人中心
    ├─ 我的资料
    ├─ 我的 Key
    └─ 我的用量
```

#### 5.11.2 UX 要点

- 暗色 / 浅色模式切换
- 多语言（中 / 英）i18n
- 上游 Key 默认脱敏，点击 `查看明文` 弹出二次确认（仅 Root）
- 大表格虚拟滚动 + 服务端分页（支持 100k+ 行）
- 实时监控页（WebSocket）显示当前 QPS、活跃流式连接数

---

## 6. API 接口规范

### 6.1 下游 OpenAI 兼容 API

#### 6.1.1 鉴权

```http
Authorization: Bearer sk-fwr-<token>
Content-Type: application/json
```

#### 6.1.2 端点

完全遵循 OpenAI 规范，请求/响应字段与 https://platform.openai.com/docs 一致。Fireworks 私有扩展字段（如 `reasoning_effort`、`thinking`、`prompt_cache_key`、`speculation`、`perf_metrics`）透传，不做剥离。

##### `POST /v1/chat/completions`

请求体示例：
```json
{
  "model": "deepseek-v4-pro",
  "messages": [{"role": "user", "content": "hello"}],
  "stream": true,
  "stream_options": {"include_usage": true},
  "temperature": 0.7,
  "max_tokens": 1024
}
```

##### `GET /v1/models`

返回当前用户分组允许访问且状态为 `active` 的模型列表。

```json
{
  "object": "list",
  "data": [
    {
      "id": "deepseek-v4-pro",
      "object": "model",
      "created": 1747900000,
      "owned_by": "fireworks",
      "context_length": 1048576,
      "supported_features": ["chat", "tools", "vision", "reasoning"]
    }
  ]
}
```

#### 6.1.3 错误格式（OpenAI 风格）

```json
{
  "error": {
    "message": "Insufficient quota.",
    "type": "insufficient_quota",
    "param": null,
    "code": "insufficient_quota"
  }
}
```

错误码列表：

| code | HTTP | 说明 |
| --- | --- | --- |
| `invalid_api_key` | 401 | 下游 Key 不存在或被禁用 |
| `expired_api_key` | 401 | 下游 Key 过期 |
| `ip_not_allowed` | 403 | IP 不在白名单 |
| `model_not_allowed` | 403 | 模型不在用户/Key 允许的列表 |
| `model_not_found` | 404 | 模型未配置 |
| `insufficient_quota` | 402 | 余额不足 |
| `rate_limit_exceeded` | 429 | 超过 RPM / 并发 |
| `no_available_upstream` | 503 | 无可用上游 Key |
| `upstream_error` | 502 | 上游错误（保留原因） |
| `upstream_timeout` | 504 | 上游超时 |

### 6.2 管理后台 API

Base：`/api/v1`，鉴权 `Authorization: Bearer <jwt>`。

| 资源 | 方法 + 路径 |
| --- | --- |
| 认证 | `POST /api/v1/auth/login`、`POST /api/v1/auth/refresh`、`POST /api/v1/auth/logout`、`POST /api/v1/auth/register` |
| 用户 | `GET/POST /api/v1/users`、`GET/PATCH/DELETE /api/v1/users/{id}`、`POST /api/v1/users/{id}/topup` |
| 用户分组 | `GET/POST /api/v1/user-groups`、`PATCH/DELETE /api/v1/user-groups/{id}` |
| 上游 Key | `GET/POST /api/v1/upstream-keys`、`POST /api/v1/upstream-keys/batch-import`、`PATCH /api/v1/upstream-keys/{id}`、`POST /api/v1/upstream-keys/{id}/test`、`POST /api/v1/upstream-keys/{id}/probe` |
| 下游 Key | `GET/POST /api/v1/user-tokens`、`PATCH/DELETE /api/v1/user-tokens/{id}`、`POST /api/v1/user-tokens/{id}/rotate` |
| 渠道 | `GET/POST/PATCH/DELETE /api/v1/channels` |
| 渠道组 | `GET/POST/PATCH/DELETE /api/v1/channel-groups` |
| 模型 | `GET/POST/PATCH/DELETE /api/v1/models`、`POST /api/v1/models/sync` |
| 日志 | `GET /api/v1/logs/requests`、`GET /api/v1/logs/billing`、`GET /api/v1/logs/probe` |
| 统计 | `GET /api/v1/stats/overview`、`GET /api/v1/stats/trend`、`GET /api/v1/stats/top` |
| 系统设置 | `GET/PATCH /api/v1/settings` |
| 公告 | `GET/POST/PATCH/DELETE /api/v1/announcements` |
| 健康 | `GET /healthz`、`GET /readyz`、`GET /metrics`（Prometheus） |

---

## 7. 数据库设计

> 完整 ERD 见附录 A，以下列出核心表清单。

| 表 | 说明 |
| --- | --- |
| `users` | 用户 |
| `user_groups` | 用户分组 |
| `user_tokens` | 下游 Key |
| `upstream_keys` | 上游 Fireworks Key |
| `channels` | 渠道 |
| `channel_groups` | 渠道组 |
| `channel_group_channels` | 多对多 |
| `user_group_channel_groups` | 多对多 |
| `models` | 模型 |
| `model_aliases` | 模型映射别名 |
| `request_logs` | 调用日志 |
| `billing_records` | 计费记录 |
| `topup_records` | 充值记录 |
| `key_probe_history` | Key 探测历史 |
| `announcements` | 公告 |
| `system_settings` | 系统设置（KV） |
| `audit_logs` | 管理员操作审计 |
| `invitation_codes` | 邀请码 |

索引规划：

- `request_logs(created_at, user_id)` 复合索引
- `request_logs(created_at, upstream_key_id)`
- `request_logs` 按月分区（PostgreSQL native partitioning）
- `user_tokens(token_hash)` 唯一索引
- `upstream_keys(key_hash)` 唯一索引
- `upstream_keys(status, channel_id)` 复合索引（调度查询）

---

## 8. 部署与运维

### 8.1 部署形态

#### 形态 A：Docker Compose（推荐 ≤ 1000 QPS）

```yaml
services:
  api:
    image: fireworkrouter:latest
    command: gunicorn -k uvicorn.workers.UvicornWorker -w 4 app.main:app
  worker:
    image: fireworkrouter:latest
    command: python -m app.worker
  postgres:
    image: postgres:15
  redis:
    image: redis:7
  caddy / nginx:
    # HTTPS 终止
```

#### 形态 B：K8s（高并发 / 多副本）

- API Deployment（HPA 基于 CPU + QPS）
- Worker StatefulSet（探针、调度任务）
- 外部托管 PostgreSQL / Redis

### 8.2 配置管理

- `.env` 文件 + `pydantic-settings`
- 环境变量优先于 DB 设置
- 关键变量：`DATABASE_URL`、`REDIS_URL`、`JWT_SECRET`、`UPSTREAM_KEY_FERNET_KEY`、`HTTP_PROXY`

### 8.3 备份与迁移

- PostgreSQL：每日 `pg_dump` + 异地存储
- Alembic 数据库迁移
- 上游 Key 的 Fernet 密钥**必须独立备份**（丢失则全部 Key 不可恢复）

### 8.4 监控

- Prometheus metrics：QPS、延迟分布、错误率、Key 池水位、余额总量
- Grafana Dashboard 模板（仓库自带）
- OpenTelemetry traces（可选，用于排查慢请求）

### 8.5 日志

- 结构化 JSON 日志输出到 stdout
- 推荐用 Loki / ELK 聚合
- 关键操作（创建/删除 Key、修改系统设置）写入 `audit_logs`

---

## 9. 安全要求

### 9.1 凭证保护

- 上游 Key 使用 Fernet 对称加密（密钥来自环境变量），DB 中无明文
- 明文仅在 Root 角色 + 二次密码验证后短时间内可查看
- 下游 Key 仅在创建时一次性显示，DB 仅存 hash

### 9.2 传输安全

- 强制 HTTPS（HSTS）
- 内部服务间通信使用私网

### 9.3 鉴权与会话

- bcrypt cost ≥ 12
- JWT access 短期（2h）+ refresh（7d，可旋转）
- 登录设备 / 会话列表，可强制下线

### 9.4 限流与防滥用

- 全局 / IP / 用户 / Key 四层限流
- 失败登录指数退避
- 注册 Turnstile
- 防 SSRF：proxy_url 校验，禁止访问内网地址

### 9.5 审计

- 所有写操作记录 actor / action / target / before / after
- 探测、自动禁用事件写入审计

### 9.6 合规

- GDPR 友好：用户可导出 / 删除自己的数据
- 默认不记录完整 prompt/response

---

## 10. 性能指标（SLO）

| 指标 | 目标 |
| --- | --- |
| 网关额外延迟 p50 | < 10 ms |
| 网关额外延迟 p99 | < 50 ms |
| 单实例 QPS | ≥ 500（非流式）/ ≥ 2000 活跃流式连接 |
| 余额探针：100 Key | 60 秒内完成 |
| 数据库写入吞吐 | ≥ 5000 logs/s（批量异步写入） |
| 可用性 | 99.9% |

---

## 11. 里程碑与排期

| 里程碑 | 内容 | 估时 |
| --- | --- | --- |
| **M0 项目初始化** | 项目骨架、CI/CD、Docker、基础认证 | 0.5 周 |
| **M1 核心转发** | OpenAI 兼容 `/chat/completions`（含 SSE 流式）、上游 Key CRUD、基础调度 | 1.5 周 |
| **M2 余额监控** | 探针任务、自动禁用、告警通知 | 1 周 |
| **M3 用户与计费** | 用户系统、下游 Key、配额、计费记录 | 1.5 周 |
| **M4 完整模型族** | embeddings、images、audio、rerank、模型同步 | 1 周 |
| **M5 多渠道与策略** | 渠道、渠道组、调度策略 6 种、失败重试 | 1 周 |
| **M6 管理后台 UI** | Vue 3 前端、Dashboard、所有 CRUD 页面 | 2 周 |
| **M7 运维强化** | Prometheus、Grafana 模板、审计、备份脚本、安全加固 | 1 周 |
| **M8 压测与上线** | 压测、性能优化、文档、灰度上线 | 1 周 |

**总计：约 10–11 周**（1 人全职）

---

## 12. 风险与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| Fireworks 无公开"余额查询" API | 探测准确性下降 | 多层探针 + 用户手动维护 + 调用实扣 |
| 上游 IP 限制（国内访问） | 部分 Key 不可用 | 渠道支持 `proxy_url`，按渠道选不同出口 |
| Fireworks 模型频繁更新 / 下线 | 模型映射失效 | 每日同步任务 + 下线后保留历史定价 |
| 流式连接长时间挂起耗尽 worker | 服务雪崩 | 单实例并发上限 + 全局超时 + 客户端断开检测 |
| 上游限流（429） | 调用大量失败 | 调度时引入 token bucket，RPM 节流；失败切 Key |
| Fireworks Key 泄露 | 安全事件 | Fernet 加密 + 审计 + Root 二次验证 + 内部 IP 限制 |
| 100+ Key 探测压力 | Fireworks 控制面限速 | 并发 ≤ 20、错峰、缓存 5 分钟 |
| 数据库日志写入瓶颈 | 网关延迟拉高 | 异步批量写入（asyncio.Queue + Worker）+ 月分区 |

---

## 13. 验收标准

### 13.1 功能验收
- [ ] 100 个 Fireworks Key 批量导入 ≤ 30 秒
- [ ] OpenAI Python SDK / Node SDK 直连无需改造，流式与非流式均可用
- [ ] 任一 Key 余额耗尽后 ≤ 15 分钟自动禁用，且后续请求不再分发到该 Key
- [ ] 单 Key 余额变化后管理后台可见 ≤ 1 分钟
- [ ] 单次请求中上游故障自动切换 Key，外部无感
- [ ] 用户的额度、调用日志、计费记录数据准确（与 Fireworks 后台对账误差 ≤ 1%）

### 13.2 性能验收
- [ ] 单实例 500 RPS 持续 10 分钟无错误
- [ ] 网关额外延迟 p99 < 50ms
- [ ] 100 Key 全量探测 < 60s

### 13.3 安全验收
- [ ] DB 中无任何上游 Key 明文
- [ ] 普通用户无法访问其他用户的 Key / 日志
- [ ] 渗透测试：通过 OWASP Top 10 基础检查

---

## 附录 A：核心 ERD（示意）

```
users ─< user_tokens
users ─> user_groups ─< user_group_channel_groups >─ channel_groups
                                                          │
                                                          ▼
                                                  channel_group_channels
                                                          │
                                                          ▼
                                                       channels ─< upstream_keys
                                                          │
                                                          ▼
                                                     (supports) models
request_logs ─> users / user_tokens / upstream_keys / channels / models
billing_records ─> request_logs (1:1)
key_probe_history ─> upstream_keys
audit_logs ─> users (actor)
```

## 附录 B：参考 Fireworks API 端点（已实测）

> 注意：Fireworks 把 **推理 API**（`/inference/v1/*`）与 **管理 API**（`/v1/accounts/*`）放在不同的路径前缀下，使用同一个 Bearer Token 鉴权。

### B.1 推理 API（`https://api.fireworks.ai/inference/v1/...`）

| Method | URL | 说明 |
| --- | --- | --- |
| POST | `/inference/v1/chat/completions` | Chat（✅ 实测可用） |
| POST | `/inference/v1/completions` | Completion |
| POST | `/inference/v1/embeddings` | Embedding |
| POST | `/inference/v1/images/generations` | Image |
| POST | `/inference/v1/audio/transcriptions` | STT |
| POST | `/inference/v1/audio/speech` | TTS |
| POST | `/inference/v1/rerank` | Rerank |
| GET | `/inference/v1/models` | 模型列表（✅ 实测可用） |

### B.2 管理 API（`https://api.fireworks.ai/v1/...`）

| Method | URL | 说明 |
| --- | --- | --- |
| GET | `/v1/accounts` | 列出当前 Key 可见的账户（✅ 实测返回 `{accounts: [...], nextPageToken, totalSize}`） |
| GET | `/v1/accounts/{account_id}?readMask=*` | 账户信息：`state` / `suspendState` / `email` / `createTime` / `notificationSettings`（✅ 实测） |
| GET | `/v1/accounts/{account_id}/users` | 账户内成员列表（角色、邮箱）（✅ 实测） |
| GET | `/v1/accounts/{account_id}/quotas` | **配额列表**（含 `monthly-spend-usd` ⭐余额来源、`serverless-inference-rpm`、`eval-protocol-free-daily-credits` 等）（✅ 实测） |
| GET | `/v1/accounts/{account_id}/quotas/{resource}` | 单个配额详情 |
| PATCH | `/v1/accounts/{account_id}/quotas/{resource}` | 更新配额 |

### B.3 关键字段速查（实测 2026-05-22）

**Account 响应：**
```json
{
  "name": "accounts/eienqmy8016a-ovbqkbf",
  "state": "READY",                 // CREATING / READY / UPDATING / DELETING
  "suspendState": "UNSUSPENDED",    // 健康值；不健康为 DELINQUENT/OVER_QUOTA/SUSPENDED
  "status": {"code": "OK", "message": ""},
  "accountType": "ACCOUNT_TYPE_UNSPECIFIED",
  "email": "...",
  "createTime": "...",
  "updateTime": "..."
}
```

**Quotas 响应（核心余额项）：**
```json
{
  "quotas": [
    {
      "name": "accounts/{id}/quotas/monthly-spend-usd",
      "maxValue": "50",      // 账户层面允许的上限
      "value": "50",          // 当前生效的限额
      "usage": 0,             // 本月已花费（美元）
      "updateTime": "..."
    },
    {
      "name": "accounts/{id}/quotas/serverless-inference-rpm",
      "maxValue": "6000",
      "value": "6000",
      "usage": 0
    },
    {
      "name": "accounts/{id}/quotas/eval-protocol-free-daily-credits",
      "maxValue": "1000",
      "value": "0",
      "usage": 0
    }
    // 还有 a100/b200/b300/h100/h200/training-* 等 GPU 配额，与本系统无关
  ]
}
```

**余额计算公式：** `balance_usd = float(monthly_spend_quota.value) - float(monthly_spend_quota.usage)`

## 附录 C：示例配置（`.env.example`）

```ini
# 基础
APP_ENV=production
APP_PORT=8011
APP_SECRET=please-change-me
LOG_LEVEL=INFO

# 数据库
DATABASE_URL=postgresql+asyncpg://fwr:fwr@postgres:5432/fwr
REDIS_URL=redis://redis:6379/0

# 鉴权
JWT_SECRET=please-change-me-64-bytes
JWT_ACCESS_TTL_MIN=120
JWT_REFRESH_TTL_DAY=7

# 上游 Key 加密
UPSTREAM_KEY_FERNET_KEY=base64-32-bytes-fernet

# 网关
GATEWAY_MAX_RETRY=3
GATEWAY_DEFAULT_TIMEOUT_MS=120000
GATEWAY_STREAM_BUFFER_SIZE=65536

# 探针
PROBE_INTERVAL_MINUTES=15
PROBE_CONCURRENCY=20
PROBE_MIN_BALANCE_USD=0.5
PROBE_USE_CHAT_COMPLETION=false
PROBE_CHEAP_MODEL=accounts/fireworks/models/qwen3-0-6b

# 上游代理
HTTP_PROXY=
HTTPS_PROXY=
NO_PROXY=localhost,127.0.0.1

# SMTP
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_USE_TLS=true

# 告警
ALERT_WEBHOOK_URL=
ALERT_EMAIL_RECIPIENTS=
```

---

**变更记录**

| 版本 | 日期 | 作者 | 变更 |
| --- | --- | --- | --- |
| v1.0 | 2026-05-22 | bobdong / Claude | 初稿 |
