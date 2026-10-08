# findings.md — 调查发现

## 根因证据链（Phase 1 研究）

| # | 断点 | 位置 | 事实 |
|---|---|---|---|
| 1 | 配置层 | `settings.py:213` | 只有 `database: DatabaseConfig`（单数），无 `databases: list` |
| 1b | 配置层 | `settings.py:88-123` | `SecurityConfig` 无 `blocked_tables` / `blocked_columns` / `allow_explain` |
| 2 | 死代码 | `db/pool.py:50` | `create_pools(list[DatabaseConfig])` 已实现，全项目零调用（仅 `db/__init__.py` 导出） |
| 2b | 半成品 | `sql_validator.py:77-95` | `SQLValidator` 构造已支持三参数，黑名单检查逻辑完整（`:236-283`） |
| 3 | 装配层 | `server.py:99-109` | `_pools` 只写入一个键 |
| 3b | 装配层 | `server.py:161-169` | 循环建 `sql_executors` 字典，但 `_pools` 仅一项 → 字典注定单条目 |
| 3c | 装配层 | `server.py:197` | `sql_executor=sql_executors[_settings.database.name]` 写死主库 |
| 4 | 执行期 | `orchestrator.py:92,198` | `self.sql_executor` 单例，`execute()` 从不按 `database_name` 选择 → 即便 pools 多项也只打主库 |
| 5 | 策略掏空 | `server.py:153-158` | 硬编码 `None / None / False` |
| 5b | 策略掏空 | `sql_validator.py:245,266` | 集合为空 → `_check_blocked_tables` / `_check_blocked_columns` 直接 `return None` |

## 设计文档 vs 实现

| 设计文档位置 | 承诺 | 实现 |
|---|---|---|
| `0002-pg-mcp-design.md:228-236` | `blocked_tables` / `blocked_columns` / `allow_explain` 在 `SecurityConfig` | 缺失 |
| `0002-pg-mcp-design.md:294` | `databases: list[DatabaseConfig]` | 单数 |
| `0002-pg-mcp-design.md:301-306` | `validate_databases`（非空 + 名称唯一） | 缺失 |
| `0002-pg-mcp-design.md:622-623` | validator 从 config 读黑名单 | 从构造参数读，且传的是 None |

## 关键约束与坑

1. **`SQLExecutor.db_config` 是死参数** —— `grep db_config src/` 显示只在 `sql_executor.py:50` 赋值，
   全文件无读取。多库下"每个 executor 拿自己的 db_config"这一顾虑不成立，不要顺手改（不在本次范围）。
2. **`_set_session_params`（`sql_executor.py:155-199`）只读 `self.security_config`** —— 用
   `safe_search_path` 与 `readonly_role`。所以每库差异只体现在 pool，不在 session 参数。
3. **sqlglot 28.5.0 解析不了 EXPLAIN** —— 降级为 `exp.Command`，内部结构不可靠。
   检测 `ANALYZE` 必须回退到原始 SQL 文本做词法判断，且要覆盖
   `EXPLAIN ANALYZE ...` 与 `EXPLAIN (ANALYZE true, ...)` 两种写法。
4. **`.env` / `.env.example` 读取被权限拦截** —— 无法确认现网已有哪些键值，
   因此 `databases` 必须回落 `[database]`，不能让既有配置失效。
5. **装配层零测试覆盖** —— bug 藏在 `server.py` lifespan，现有测试只测 orchestrator 单元。
   这是同类问题会复发的根本原因，必须补 `test_server_wiring.py`。
6. **`create_pools` docstring 说 "concurrently" 但实现是串行 for 循环** ——
   措辞不实，非本任务范围，记录备查。

## 既有测试现状

- `tests/unit/test_orchestrator.py:64,90` 已覆盖 `_resolve_database` 的多库分支
  （用 mock pools 手工构造多库），**但 server 层从不产生多项 pools**，所以测试绿灯掩盖了真实缺陷。
- `specs/w5/0008-pg-mcp-test-plan-review.md:146` 已点名"多数据库选择逻辑：缺失"。

---

## 第二轮发现（2026-10-08）

### 1. `env_file` 只配了 2 个类（同一缺陷 ×5）

| 读 `.env` | 只认进程环境变量 |
|---|---|
| `DatabaseConfig:19`、`OpenAIConfig:56`、`Settings:213` | `SecurityConfig:91`、`ValidationConfig:140`、`CacheConfig:165`、`ResilienceConfig:177`、`ObservabilityConfig:197` |

后果：`.env` 里 **21 个键**（`SECURITY_*` 7 个 + `VALIDATION_*` 2 + `CACHE_*` 3 +
`RESILIENCE_*` 5 + `OBSERVABILITY_*` 4）**被静默忽略**。用户以为配好了，实际一行都没生效。

项目自己记着这个坑：`claude_desktop_config.json` 的 `_comment` 写着
"以下 5 组尚未支持 .env，故必须在此提供"。而 Claude Code 走的 `.mcp.json` 里
`"env": {}` **是空的** → 走 Claude Code 时这些配置全部拿不到。

本次**只修了 `SecurityConfig`**（用户批准范围）。

### 2. 只加 `env_file` 会让服务起不来

`.env` 里列表项是逗号分隔（`SECURITY_BLOCKED_TABLES=a,b`），而 pydantic-settings
对复杂类型默认做 **JSON 解码**，直接抛：
`SettingsError: error parsing value for field "blocked_tables" from source "DotEnvSettingsSource"`

修法：在同一个 `model_config` 里加 `enable_decoding=False`，把原始字符串交给已有的
`parse_comma_separated` 校验器。**不改这行则整个服务启动即崩。**

### 3. `DatabaseConfig.password` 是裸 `str` → 两条凭据泄漏路径

`print(model)` / `repr(model)` 会明文打印密码；**pytest 断言失败的 diff 更隐蔽**——
`assert settings.databases == []` 失败时，assertion rewriting 会把
`DatabaseConfig(... password='<真实密码>' ...)` 整串打进 traceback。

**2026-10-08 我因此两次把真实密码写进对话记录。** 已改 `SecretStr`
（照抄项目自己的 `OpenAIConfig.api_key` 写法），并确立约定：
**涉及 `DatabaseConfig` 的断言必须先投影成标量**（`[d.name for d in ...]`）。

### 4. `log_format` 默认值三处不一致

| 来源 | 值 |
|---|---|
| `settings.py:206` 实现 | `text` |
| `specs/w5/0002-pg-mcp-design.md:291` 设计文档 | `json` |
| `docker-compose.yml` | `${OBSERVABILITY_LOG_FORMAT:-json}` |
| `tests/unit/test_config.py` 断言 | `json` |

实现漂移，已改为 `json`。

### 5. `DatabaseConfig.dsn` 未对密码做 URL 编码

`settings.py` 的 `dsn` property 直接拼串。当前 `.env` 密码含 1 类特殊字符，
实测该 DSN `gaierror [Errno 11003]`，URL 编码后即连通。
`dsn` / `safe_dsn` 在 `src/` 中**零引用**（`create_pool` 走 kwargs），故不阻塞，
属潜在陷阱。已在 property docstring 中记录。

### 6. 测试卫生：断言"默认值"却存在真实 `.env`

5 个用例断言类默认值，但配置文件就在项目根。**本会话期间 `.env` 被改过一次**
（10577 → 10610 字节，+33，非我所改），导致其中 2 个用例由绿转红。
逐点用 `_env_file=None` 隔离后全部转绿。

**否决的方案**：全局 autouse fixture 切 CWD —— 实测 44 个 e2e/integration 用例
依赖真实 `.env` 的 `OPENAI_API_KEY`，切走后 `ValidationError`。

### 7. 死配置清单（定义后被全项目零读取）

| 配置项 | 位置 |
|---|---|
| `allow_write_operations` | `settings.py:93` |
| `SQLExecutor.db_config` | `sql_executor.py:50` |
| `DatabaseConfig.dsn` / `safe_dsn` | `settings.py`（仅测试引用） |

---

## 第三轮发现（2026-10-08）

### 8. 弹性与可观测性：创建后零引用

`grep -nE "_metrics|_rate_limiter|_circuit_breaker" src/pg_mcp/server.py` 只命中 8 行，
**全部是声明与创建**：

```
36/37/38: 声明三个模块级全局
71/72:    global 语句
136:      _metrics = MetricsCollector()
180:      _circuit_breaker = CircuitBreaker(...)
186:      _rate_limiter = MultiRateLimiter(query_limit=10, llm_limit=5)
```

| 组件 | 设计意图 | 实现现状 |
|---|---|---|
| Metrics | `metrics: MetricsCollector \| None = None` 注入（设计 `:805`/`:1001`/`:1313`） | 参数被删；9 指标 8 方法全空。`/metrics` 端点活着但业务指标恒为 0 |
| Rate limiter | 设计 `:2126` 完整设计 | 零调用 → 无限流 |
| Circuit breaker | — | server 层那个是死的；生效的是 orchestrator 内部自建的**第二个实例** |
| Retry backoff | 设计 `:276` 有 `db_retry_delay` | `max_retries` 生效但无 sleep → 裸循环 |
| Tracing | `tracing.py` 全套 | src 内零引用；orchestrator 自用 `uuid.uuid4()` |

`/metrics` 能 curl 通是因为 `prometheus_client.start_http_server` 起的是默认 registry——
端点活着、数据为空，表面上"像是接好了"。

### 9. 重复 `to_dict` 的真实影响

- `models/query.py:160`：`exclude_none=False` + 补 `tokens_used=0`
- `models/query.py:214`：`exclude_none=True` ← **后定义覆盖前者，实际生效**
- 设计文档 `:1592`：`model_dump(mode="json", exclude_none=True)` → 方向对，但漏了 `mode="json"`
- 实测：失败响应只返回 `['confidence','error','success']`；成功响应缺 `error`
- `server.py` 的 `"tokens_used" not in result` 补丁，正是为这套混乱打的补丁

### 10. `SchemaCache` 完全没有容量上限

`self._cache: dict[str, DatabaseSchema]` + 手写 `_cache_timestamps` 做 TTL；
`CacheConfig.max_size` 从未被读取。已改为内联淘汰（`_evict_if_full`）。

**未采用 `TTLCache` 的原因**：`cachetools` 只在 `uv.lock` 里（传递依赖），
`pyproject.toml` 未声明 —— 依赖传递项不是好做法。

### 11. `QUESTION_TOO_LONG` 是"半成品意图"的铁证

`ErrorCode.QUESTION_TOO_LONG` 早已定义但**全项目零使用**，同时
`ValidationConfig.max_question_length` 也是死的 —— 说明原意就是校验问题长度并返回该错误码，
只是没实现。已在 `server.query()` 接通。
（模型层另有硬编码 `max_length=10000`，与配置的 `le=50000` 矛盾，已提为常量对齐。）
