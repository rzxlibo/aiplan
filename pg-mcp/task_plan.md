# task_plan.md — 接通多数据库选择 + 表/列访问限制 + EXPLAIN 门控

## 需求摘要

设计文档 `specs/w5/0002-pg-mcp-design.md` 承诺了多数据库与安全控制，但实现未启用：

- 配置层无承载能力（`Settings` 只有单数 `database`；`SecurityConfig` 缺 3 个安全字段）
- 装配层把三处能力硬编码为失效值（`blocked_tables=None, blocked_columns=None, allow_explain=False`）
- 执行期固定使用主库 executor，无法按库路由

结果：`blocked_tables` / `blocked_columns` 恒为空集（`sql_validator.py:245` 直接 return None），敏感对象零保护；多库能力起不来。

**事实校正**：当前不存在"静默串库"——`_resolve_database` 对库名做硬校验，库不存在直接报错。真实形态是多库能力不可用，以及"若只补 pools 不补执行器选择才会串库"。

## 方案决策（已获用户批准）

| 决策点 | 采纳 | 理由 |
|---|---|---|
| 多库形态 | `databases: list[DatabaseConfig]`，空则回落 `[database]` | 复用既有 `create_pools` 签名；`.env` 不可读，必须向后兼容 |
| 策略粒度 | 全局一份（照设计原样） | YAGNI；避免 `SQLValidator` 变 N 实例 + 多一层路由 |
| 执行期路由 | orchestrator 持 `sql_executors: dict`，按库名取 | 修掉"永远打主库"的根因 |
| EXPLAIN | 配置开关默认关闭；开启时一律拒绝 `ANALYZE` | 兑现承诺成本低；`ANALYZE` 会真实执行语句 |

### 假设（用户已确认）

- A1 目标是真正接通，不是删承诺
- A2 必须向后兼容 `DATABASE_*` 单库配置
- A3 不新增 MCP tool 参数
- A4 一次请求只打一个库，不做跨库联邦

## 任务拆解

| # | 任务 | 文件 | 状态 |
|---|---|---|---|
| 1 | 写失败测试：多库路由到正确 executor | `tests/unit/test_orchestrator.py` | done |
| 2 | 写失败测试：装配层键集合 == 配置库集合 | `tests/unit/test_server_wiring.py` (新建) | done |
| 3 | 写失败测试：黑名单生效 + EXPLAIN 门控 | `tests/unit/test_sql_validator.py` | done |
| 4 | 写失败测试：databases 解析 / 回落 / 重名 | `tests/unit/test_config.py` | done |
| 5 | `SecurityConfig` 补 3 字段 + `Settings.databases` + `effective_databases` + 重名校验 | `src/pg_mcp/config/settings.py` | done |
| 6 | lifespan 接线：`create_pools`、validator 读配置、传 executors 字典 | `src/pg_mcp/server.py` | done |
| 7 | orchestrator 按库路由 executor | `src/pg_mcp/services/orchestrator.py` | done |
| 8 | validator 拒绝 `ANALYZE`，修正错误注释 | `src/pg_mcp/services/sql_validator.py` | done |
| 9 | 全量回归：pytest + ruff | — | done |

## 执行结果（2026-10-08）

- 全量：`3 failed, 313 passed`。3 个失败为**既有环境问题**，与本次改动无关：
  `TestDatabaseConfig/TestOpenAIConfig/TestObservabilityConfig::test_default_values`
  断言的是类默认值，但 `DatabaseConfig` / `OpenAIConfig` / `ObservabilityConfig`
  都是读 `.env` 的 BaseSettings，真机上 `.env` 覆盖为 `adms-cloud` /
  `deepseek-flash` / `text`。已用 `model_fields_set` 证实取值来自 `.env`
  （shell 环境为空）。**未修改**这三个无关测试。
- 新增/调整的 26 个用例全部通过，其中 `test_server_wiring.py` 8/8。
- `ruff check src tests`：从 44 降到 **4 个既有错误**（`db/pool.py` ×2、
  `models/query.py` ×1、`server.py:230` ×1），本次改为零新增。
  期间修正了自引入的 38 个 RUF002/RUF003（中文注释里的全角标点）、
  C420、RUF043 —— 仓库既有约定是「中文注释 + ASCII 标点」。
- `ruff format --check` 在 `db/introspection.py`、`db/pool.py`、`server.py`、
  `services/sql_validator.py` 报差异，但差异全部位于**未触碰的既有代码行**
  （如 `ALLOWED_TOP_LEVEL` 字面量、shutdown 段的 `asyncio.wait_for`），
  按"外科手术式修改"未动。

### 行为变更（需知晓）

`test_explain_analyze_allowed` 被**反转为** `test_explain_analyze_rejected_even_when_enabled`。
既有测试固化了旧行为（`allow_explain=True` 时放行 `EXPLAIN ANALYZE`），
该行为已被批准的设计决策推翻。

---

## 第二轮（2026-10-08 追加）· 配置生效性与凭据泄漏

起因：用户要求在 `.env` 配置安全控制，执行时发现**配置写了却不生效**。

| # | 任务 | 文件 | 状态 |
|---|---|---|---|
| 10 | 写失败测试：SecurityConfig 须读 `.env` | `tests/unit/test_config.py` | done |
| 11 | 写失败测试：password 不得出现在 repr | `tests/unit/test_config.py` | done |
| 12 | `SecurityConfig` 补 `env_file` + `enable_decoding=False` | `src/pg_mcp/config/settings.py:88` | done |
| 13 | `DatabaseConfig.password` 改 `SecretStr` | `src/pg_mcp/config/settings.py:28` | done |
| 14 | 连接层适配 `SecretStr` | `src/pg_mcp/db/pool.py:37` | done |
| 15 | `log_format` 默认值 `text` → `json`（对齐设计文档 `0002:291`） | `src/pg_mcp/config/settings.py:206` | done |
| 16 | 4 个断言"默认值"的测试逐点隔离（`_env_file=None`） | `test_config.py`、`test_server_wiring.py` | done |
| 17 | 全量回归 + lint | — | done |

### 关键决策与偏差记录

1. **`enable_decoding=False` 是必需的，不在原批准范围**。只加 `env_file=".env"` 会让服务
   **直接起不来**：`.env` 里 `SECURITY_BLOCKED_TABLES=a,b` 是逗号分隔，pydantic-settings
   默认按 JSON 解码，抛 `SettingsError`。为不加这行则服务崩溃，先加后报。
2. **全局 `conftest` 隔离方案被否决**。原推荐用 autouse fixture 把测试 CWD 切到临时目录，
   实测撞车：**44 个 e2e/integration 测试依赖真实 `.env` 的 `OPENAI_API_KEY`**
   （`OpenAIConfig` 校验器要求非空），切走后直接 `ValidationError`。
   改用逐点 `_env_file=None` 隔离，只需改 4 个测试。
3. **`env_file` 缺陷只修了 `SecurityConfig`**（用户批准范围）。其余 4 个类
   （`ValidationConfig` / `CacheConfig` / `ResilienceConfig` / `ObservabilityConfig`）
   同一缺陷保留，仅补记 findings。

### 执行结果（第二轮）

- 全量：**318 passed, 0 failed**（第一轮遗留的 3 个既有失败 + 2 个 `.env` 编辑所致失败全部转绿）
- `ruff check src tests`：仍为 **4 个既有错误**，零新增
- 新增测试 2 个；新增/修改测试文件 3 个

### 遗留（明确不做）

- 其余 4 个配置类的 `env_file` 缺陷
- `.mcp.json` 的 `env` 块仍为空 → 走 Claude Code 时 `SECURITY_*` 只能靠 `.env`（现在已可读）

---

## 第三轮（2026-10-08 追加）· 弹性/可观测性接入 + 模型与配置修正

起因：用户指出两件事 —— (1) 弹性与可观测性模块仅停留在设计层面；
(2) 响应/模型缺陷（重复 to_dict、未使用配置字段）导致行为偏离实施方案。

### 研究结论（逐项验证到调用点）

- `server.py` 里 `_metrics` / `_circuit_breaker` / `_rate_limiter` **创建后零引用**（grep 仅命中
  声明与创建共 8 行）
- 设计文档 `:805` 等三处写了 `metrics: MetricsCollector | None = None` 的注入签名，
  实现把参数整个删掉了
- `retry_delay` / `backoff_factor` 零引用 → 重试是**无延迟裸循环**
- `tracing.py` 全套接口 src 内零引用，orchestrator 自己 `uuid.uuid4()`
- 死配置 6 个：`allow_write_operations`、`retry_delay`、`backoff_factor`、
  `max_question_length`、`min_confidence_score`、`CacheConfig.max_size`
- `models/query.py` 有两个 `to_dict`（F811），后定义的 `exclude_none=True` 生效且漏了
  `mode="json"`；设计文档 `:1592` 的意图正是 `model_dump(mode="json", exclude_none=True)`
- `SchemaCache` 用裸 dict + 手写 TTL，**没有任何容量上限**

### 任务与结果

| # | 任务 | 状态 |
|---|---|---|
| 1 | `models/query.py` 删重复 `to_dict` 并补 `mode="json"`；`QueryRequest` 上限提为常量 | done |
| 2 | 死配置：接入 4 个、删除 2 个；`ResilienceConfig` 新增 `query_limit` / `llm_limit` | done |
| 3 | `SchemaCache` 内联淘汰尊重 `max_size` | done |
| 4 | 重试指数退避 + `llm_limiter` + tracing request_id + 删服务层死熔断器 | done |
| 5 | 可观测性：4 个服务注入 `metrics` + 埋点 | done |
| 6 | `max_question_length` 接入 `server.query()` → `QUESTION_TOO_LONG` | done |

### 关键决策与偏差记录

1. **`cachetools` 只是传递依赖**（在 `uv.lock` 里有、`pyproject.toml` 未声明）。原计划改用
   `TTLCache`，实际改为**内联淘汰**（`_evict_if_full`），避免为 5 行逻辑新增依赖或依赖传递项。
2. **metrics 埋点用装饰器 `track_llm_call` 而非纯构造注入**。设计只规定了构造注入；
   要在 `generate()` / `validate()` 里加计时，纯注入需要把整个方法体重新缩进（大 diff）。
   装饰器 + 构造注入两者并行：注入负责"能拿到"，装饰器负责"自动上报"。
3. **`max_question_length` 接入点在 `server.query()`**，不在模型层 —— 模型类看不到配置。
   模型层原有的硬编码 `max_length=10000` 提为 `MAX_QUESTION_LENGTH_CEILING = 50000`，
   与 `ValidationConfig` 自身的 `le=50000` 对齐，消除"配置上限高于模型上限"的矛盾。
4. **`tokens_used` 在错误路径仍为 `None`**，故 `server.py` 的 `"tokens_used" not in result`
   补丁**保留**：它与 `exclude_none=True` 配套，不是冗余。
5. `increment_sql_rejected(reason=...)` 的 label **限定为 `ErrorCode` 的枚举值**
   （`security_violation` / `sql_parse_error`），绝不塞原始错误文本 —— 否则标签基数爆炸，
   还可能把 SQL 片段与表名泄进指标。

### 执行结果（第三轮）

- 全量：**336 passed, 0 failed**
- `ruff check src tests`：**3 个既有错误**（`db/pool.py` ×2、`server.py` UP041），零新增
- 新增/修改测试：`test_config` / `test_models` / `test_orchestrator` /
  `test_schema_cache` / `test_server_wiring`

### 遗留（明确不做）

- 其余 3 个配置类（`ValidationConfig` / `CacheConfig` / `ResilienceConfig`）的 `env_file` 缺陷
- `set_schema_cache_age` / `set_db_connections_active` 两个指标仍未埋点（本轮只埋了被证明
  影响请求流程的那几个）
- 表/列黑名单的三个绕过（`SELECT *`、目录表、字符串字面量）仍未修


## 验收标准

1. 配 2 库时 `query(database="B")` 的 SQL 由 B 的 executor 执行
2. 配 2 库时不传 `database` → 明确报错并列出可选库
3. 配 `blocked_tables=["users"]` 后生成含 `users` 的 SQL → `SecurityViolationError`
4. EXPLAIN 默认被拒；开关开启后 `EXPLAIN ANALYZE ...` 仍被拒
5. 装配测试通过（pools/executors 键集合一致）

## 明确不做（YAGNI 边界）

per-DB 策略 · 跨库联邦查询 · 新增 explain tool 参数 · 为 executor 提取聚合类 ·
修 `create_pools` docstring 名不副实的 "concurrently" · 清理 `SQLExecutor.db_config` 死参数

## 残留风险（须写进文档）

接通 `allow_explain` 后，EXPLAIN 的内部语句仍不做表/列黑名单校验。裸 `EXPLAIN` 不执行语句、
泄漏的是执行计划而非数据，且默认关闭时风险为零——但必须在文档显式记录，
不能保留 `sql_validator.py:159-163` 那句"EXPLAIN DELETE 安全"的错误注释。
