# pg-mcp 上手指南（二开必读）

> 面向第一次接触本项目 / 二次开发的工程师。
> 生成日期：2026-10-08 · 对应代码版本：`pyproject.toml` v0.2.1
> 本文档为**新增**文件，未修改 `CLAUDE.md` 或任何源码。

---

## 0. 先读这三条（否则一定踩坑）

1. **真实入口是 `python -m pg_mcp`，不是 `main.py`**。根目录 `main.py` 是个恒返回 42 的 demo，但 README、`claude_desktop_config.json`、`Dockerfile` 三处都指向它。
2. **本项目不是 git 仓库**。`w5/pg-mcp/` 与父目录 `D:\git\zuoye\pg-mcp\` 都没有 `.git`。无 diff、无回滚、无 blame。动手前先自行 `git init` 或备份。
3. **规格文档在父目录**，不在本项目内：`D:\git\zuoye\pg-mcp\specs\w5\`（8 篇 PRD/设计/评审）。`.specify/memory/constitution.md` 只是**未填写的模板**。

---

## 1. 项目定位

把「自然语言 → 安全 SQL → 执行 → 结果可信度评分」封装成一个 MCP Server。

| 项 | 内容 |
|---|---|
| 项目代号 | pg-mcp |
| 目标用户 | 数据分析师、产品经理、集成 MCP 的开发者 |
| 对外接口 | 单一 MCP Tool `query(question, database?, return_type?)` |
| 关键默认值 | 置信度阈值 70 · 最大 10000 行 · 超时 30s · 熔断 5 次失败 |
| 项目性质 | 训练营作业（`D:\git\zuoye`），w1~w7 为不同周次任务，共用 speckit 流程 + Codex 交叉评审 |

### 文档链（父目录 `specs/w5/`）

| 文件 | 作用 |
|---|---|
| `0001-pg-mcp-prd.md` | 需求基线（v1.1），功能/非功能/验收标准 |
| `0002-pg-mcp-design.md` | 技术设计（分层、组件、数据流、安全设计） |
| `0003-pg-mcp-design-review.md` | 设计评审 |
| `0004-pg-mcp-impl-plan.md` | 实现计划（分 Phase） |
| `0005-pg-mcp-impl-plan-review.md` | 计划评审 |
| `0006-pg-mcp-code-review.md` | ★ **代码评审，已知缺陷全清单** |
| `0007-pg-mcp-test-plan.md` | 测试计划 |
| `0008-pg-mcp-test-plan-review.md` | ★ 测试计划评审，测试缺口清单 |

---

## 2. 技术栈

| 层 | 实际使用 | 与 `CLAUDE.md` 的差异 |
|---|---|---|
| Python | **3.14**（`.python-version`、`requires-python`） | CLAUDE.md 写 3.12+；ruff/mypy 均配 `py311` |
| MCP 框架 | `fastmcp>=2.14.1` | 一致 |
| PG 驱动 | `asyncpg>=0.31.0` | 一致 |
| SQL 解析 | **`sqlglot>=28.5.0`** | ⚠️ CLAUDE.md 通篇写 `pglast`，**实际零使用** |
| LLM | `openai>=2.14.0`（`AsyncOpenAI` + `chat.completions.create`） | ⚠️ 无 `base_url` 参数 |
| 配置 | `pydantic-settings`，按 `env_prefix` 分组 | 一致 |
| 可观测 | `prometheus-client`（模块在，未接线） | 一致 |
| 测试 | pytest + pytest-asyncio（`asyncio_mode=auto`）+ pytest-cov（`fail_under=80`） | 一致 |
| 包管理 | uv（`uv.lock`，非 git 仓库） | — |

**外部服务**：PostgreSQL 12+、OpenAI 兼容 API。

---

## 3. 目录结构与模块职责

```
w5/pg-mcp/
├── main.py                  ⚠️ 占位 dummy（恒返回 42），勿用
├── src/pg_mcp/
│   ├── __main__.py          ✅ 真实入口 → pg_mcp.server.mcp
│   ├── server.py            FastMCP 实例 + lifespan + query tool
│   ├── config/settings.py   7 组 Pydantic Settings
│   ├── models/
│   │   ├── query.py         QueryRequest / QueryResponse / QueryResult
│   │   ├── schema.py        DatabaseSchema / TableInfo / ColumnInfo
│   │   └── errors.py        ErrorCode + 异常层次
│   ├── services/            ★ 核心业务
│   │   ├── orchestrator.py      查询流程编排（559 行，最大）
│   │   ├── sql_generator.py     LLM 生成 SQL
│   │   ├── sql_validator.py     SQLGlot 安全校验
│   │   ├── sql_executor.py      asyncpg 只读执行
│   │   └── result_validator.py  LLM 结果置信度评分
│   ├── cache/schema_cache.py    TTL 缓存 + 可选后台刷新
│   ├── db/
│   │   ├── pool.py              连接池创建/关闭
│   │   └── introspection.py     pg_catalog 内省（428 行）
│   ├── resilience/
│   │   ├── circuit_breaker.py   ✅ 已接线
│   │   └── rate_limiter.py      ❌ 死代码
│   ├── observability/
│   │   ├── logging.py           ✅ 已接线
│   │   ├── metrics.py           ❌ 未接线
│   │   └── tracing.py           ❌ 死代码
│   └── prompts/                 两段 LLM 提示词模板
├── tests/
│   ├── unit/                248 个用例，纯 mock，可独立跑
│   ├── integration/         19 个，需真库 + 真 API key，无 skipif
│   └── e2e/                 27 个，同上
├── fixtures/                3 个规模递增测试库 SQL + Makefile
└── Dockerfile / docker-compose.yml / .env(.example)
```

代码规模：`src/` 约 **5566 行**。

---

## 4. 关键入口与数据流

**唯一真实入口**：`pyproject.toml:27` → `pg-mcp = "pg_mcp.__main__:main"` → `server.py:249` 的 `FastMCP("pg-mcp", lifespan=lifespan)`。

```
MCP Client
  └─ server.py:253  query(question, database, return_type)
       │
       ├─ [启动期] server.py:42  lifespan
       │    1. Settings()                    读 .env
       │    2. configure_logging()           结构化日志
       │    3. create_pool(单库)             建连接池
       │    4. schema_cache.load()           启动时全量内省
       │    5. MetricsCollector()            （创建后未使用）
       │    6. 组装 SQLGenerator / SQLValidator / SQLExecutor / ResultValidator
       │    7. CircuitBreaker + MultiRateLimiter（后者未使用）
       │    8. QueryOrchestrator(...)        ← orchestrator 内部又自建一个熔断器
       │
       └─ [请求期] QueryOrchestrator.execute_query()   orchestrator.py:104
            1. _resolve_database()         校验库名 / 单库自动选择
            2. schema_cache.get()          取 Schema（miss 则 load）
            3. _generate_sql_with_retry()  LLM 生成 → 校验 → 失败带错误反馈重试
            4. return_type == "sql" ?      提前返回
            5. sql_executor.execute()      asyncpg 只读事务 + statement_timeout
            6. _validate_results_safely()  LLM 给 confidence（失败不阻断，默认 100）
            7. 组装 QueryResponse → to_dict()
```

两条 LLM 调用（生成 + 验证）共用**同一个** `CircuitBreaker`，但该实例建在 orchestrator 内部（`orchestrator.py:99`），`server.py:181` 那个是冗余的。

---

## 5. 配置要点

配置来自 `.env`，分组前缀：`DATABASE_` / `OPENAI_` / `SECURITY_` / `VALIDATION_` / `CACHE_` / `RESILIENCE_` / `OBSERVABILITY_`。

### ⚠️ 本地 `.env` 与 `.env.example` 的关键差异

| 键 | `.env`（实际生效） | `.env.example` |
|---|---|---|
| `DATABASE_NAME` | `adms-cloud` | `blog_small` |
| `DATABASE_USER` | `root` | `postgres` |
| `OPENAI_MODEL` | `deepseek-flash` | `gpt-5.2-mini` |

**换端点**：`OpenAIConfig` 现已支持 `base_url`（默认 `None` = OpenAI 官方），`sql_generator.py` 与 `result_validator.py` 均已透传。`.env` 里填 `OPENAI_BASE_URL` 即可切换到 OpenAI 兼容端点，例如 DeepSeek：

```bash
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
```

#### ⚠️⚠️ 嵌套配置默认**不读 `.env`**（本项目最大的配置陷阱）

`Settings.model_config` 有 `env_file=".env"`，但 7 个嵌套配置类（`DatabaseConfig` / `OpenAIConfig` / `SecurityConfig` / `ValidationConfig` / `CacheConfig` / `ResilienceConfig` / `ObservabilityConfig`）的 `model_config` 原本**只有 `env_prefix`，没有 `env_file`**。pydantic-settings 对嵌套模型走 `default_factory`，因此它们**只读 OS 环境变量，不读 `.env` 文件**。

后果：

- `.env` 里 36 个配置项**静默失效**，服务器全部走硬编码默认值（`localhost:5432/postgres`、`gpt-4o-mini` ……）
- 而 `OPENAI_API_KEY` 没有合法默认值 → `Settings()` 直接抛 `ValidationError` → **服务器根本起不来**
- README 教你「编辑 `.env`」——**那条路是坏的**；能工作的只有 `claude_desktop_config.json` 的 `env` 块（真实 OS 环境变量）

**当前状态**：已给 `OpenAIConfig` 与 `DatabaseConfig` 补上 `env_file=".env"` + `extra="ignore"`（缺后者会被其他前缀的键顶成 `extra_forbidden`），**故 `OPENAI_` 与 `DATABASE_` 两组现已从 `.env` 生效**；其余 5 组（`SECURITY_` / `VALIDATION_` / `CACHE_` / `RESILIENCE_` / `OBSERVABILITY_`）仍不读 `.env`，属已知存量问题。

> 另一条可用路径：`claude_desktop_config.json` 的 `env` 块走的是**真实 OS 环境变量**，对**全部 7 组**都生效（已实测）。若想绕开逐组激活，走这条路最省事。

#### DeepSeek 接入实测结论（2026-10-08 验证）

| 项 | 结论 |
|---|---|
| `OPENAI_BASE_URL` | 必须用 **OpenAI 兼容**端点 `https://api.deepseek.com/v1`。**不能用 `/anthropic`**——那是 Anthropic Messages 协议端点（Claude Code 走它），而本项目用 `openai` SDK，协议不匹配 |
| `OPENAI_MODEL=deepseek-flash` | ✅ 有效，实测生成成功 |
| `response_format: {"type":"json_object"}` | ✅ DeepSeek 接受，`result_validator` 无需容错改造 |
| `OPENAI_MAX_TOKENS` | 字段上限 `le=4096`，填 32000 会导致 `Settings()` 抛 `ValidationError`（README 文档写 32000，与代码不符） |

**配置事实来源**：OS 环境变量 > `.env` > 代码默认值。`claude_desktop_config.json` 的 `env` 块优先级高于 `.env`，**不要在两处重复配置同一项**，否则占位符会静默覆盖真实值。

打开某一组时会连锁暴露该组在 `.env` 里从未被校验过的非法值，例如 `OPENAI_MAX_TOKENS=32000` 超过字段上限 `le=4096`（已修正为 4096）、`SECURITY_BLOCKED_FUNCTIONS` 无法解析成 list（未修）。**逐组激活、逐组验证**，不要一次性全开。

### 安全提示

`.env` 含疑似真实凭据（`adms-cloud` 库 + `root` 用户）。`.gitignore:56` 已忽略 `.env`，但因**本项目尚无 git 仓库**，该文件目前裸放在磁盘上。若后续 `git init`，请先确认它不会被误提交。

---

## 6. ⚠️ 二开/维护最容易踩的坑

### 🔴 P0 — 会让你白忙半天

#### 坑 1｜`main.py` 是假的，三处文档都在误导

`main.py:3-8` 与 PostgreSQL 毫无关系：

```python
mcp = FastMCP("special mcp server to add two numbers")

@mcp.tool
def add(a: int, b: int) -> int:
    return 42          # a + b 根本没实现
```

指向它的地方：

| 位置 | 内容 |
|---|---|
| `README.md:98` | 「运行服务器」→ `uv run python main.py` |
| `claude_desktop_config.json:10` | `"main.py"` |
| `Dockerfile:96` | `CMD ["python", "main.py"]`（容器里跑的是假服务） |

**真实入口**：`python -m pg_mcp`（`src/pg_mcp/__main__.py`）。

#### 坑 2｜项目不是 git 仓库

无 `.git`（只有 `.gitignore` / `.gitmodules`）。代码评审文档提到的 commit `cc978ba` 在本地无法复现。**动手前请自行初始化仓库或做备份。**

#### 坑 3｜当前 `.venv` 跑不了测试

`.venv` 只装了运行时依赖（196 个包，含 fastmcp / asyncpg / sqlglot），**没有 pytest / ruff / mypy**，也没有 pip：

```
$ .venv/Scripts/python.exe -m pytest tests/unit
No module named pytest
```

先执行 `uv sync --all-extras` 补齐 dev 依赖。

另外 `tests/integration/test_full_flow.py`、`tests/e2e/test_mcp.py` 会直接 `async with lifespan(mcp)` 打**真实数据库 + 真实 LLM**，且**没有 `skipif` 保护**，无服务时必然失败。只有 `tests/unit/` 是纯 mock、可独立跑。

---

### 🟠 P1 — 已核实的真实缺陷

#### 坑 4｜`EXPLAIN` 绕过全部安全检查

`sql_validator.py:152-163`：

```python
if isinstance(statement, exp.Command):
    cmd_name = str(statement.this).upper() if statement.this else ""
    if cmd_name == "EXPLAIN":
        if not self.allow_explain:
            raise SecurityViolationError("EXPLAIN statements are not allowed")
        # EXPLAIN is read-only and safe ...
        # Even "EXPLAIN DELETE" is safe as it won't actually delete data.
        return None          # ← 后续函数/表/列/子查询检查全部跳过
```

**实测确认**：sqlglot 28.5.0 把 `EXPLAIN ANALYZE DELETE FROM users` 降级为 `Command`（解析时打印 `contains unsupported syntax. Falling back to parsing as a 'Command'`）。而 PostgreSQL 的 `EXPLAIN ANALYZE DELETE` **会真正执行删除**——代码注释里那句「Even "EXPLAIN DELETE" is safe」是错的。

**当前不可利用**，因为 `server.py:157` 传的是 `allow_explain=False`。但这距离开关只有一个布尔值，且注释在诱导后人打开它。

#### 坑 5｜`max_rows` 拦不住内存爆炸

`sql_executor.py:104-122`：

```python
records = await asyncio.wait_for(connection.fetch(sql), timeout=timeout)  # 全量拉进内存
total_count = len(records)
if len(records) > max_rows:
    records = records[:max_rows]        # ← 已经太晚了
```

`fetch()` 是全量拉取，**没有下推 `LIMIT`、没有 server-side cursor**。`SELECT * FROM 亿行表` 会 OOM。

附带语义冲突：`total_count` 是「已取回行数」而非表内真实总数，但 `QueryResult.row_count` 的校验器又强制它等于 `len(rows)`。

#### 坑 6｜三个「生产就绪」子系统是死代码

README 与 PRD 承诺「限流、熔断、链路追踪」，实际：

| 模块 | 状态 | 证据 |
|---|---|---|
| `CircuitBreaker` | ✅ 真用 | `orchestrator.py:363` `allow_request()`、`:426/:438/:463` `record_failure/success()` |
| `MultiRateLimiter` | ❌ 死代码 | 仅 `server.py:187` 赋值给 `_rate_limiter`，全仓无第二处引用 |
| `observability/tracing.py` | ❌ 死代码 | 仅被自身 `__init__.py` 导出，无运行时调用 |
| `MetricsCollector` | ❌ 未接线 | `services/` 内零 metrics 调用，所有计数器恒为 0 |

另：`metrics.py` 的 `reset_all_metrics()` 会重跑注册逻辑，prometheus_client 将抛 `Duplicated timeseries`。

#### 坑 7｜设计承诺的多数据库支持未实现

设计稿是 `Settings.databases: list[DatabaseConfig]` + 每库一个 executor；实际 `settings.py:198` 是**单个** `database`，`server.py:197` 硬编码取主库 executor。

`_resolve_database()` 会校验库名并接受请求指定的库，但**执行时永远走同一个 executor**——请求方以为查了 B 库，实际查的是 A 库。`specs/w5/0006-pg-mcp-code-review.md` 已列为 High Severity，至今未修。

---

### 🟡 P2 — 会让你困惑的不一致

| # | 现象 | 位置 |
|---|---|---|
| 8 | `QueryResponse.to_dict()` **定义了两次**，第二个覆盖第一个，「保证 tokens_used 存在」的逻辑成死代码，实际被 `exclude_none` 掉 | `models/query.py:160` 与 `:214` |
| 9 | **两个同名不同实现的 `ErrorDetail`**，import 时极易拿错 | `models/query.py:139`（pydantic）vs `models/errors.py:39`（普通类） |
| 10 | `CLAUDE.md` 技术栈表基本失真：`pglast`→实为 `sqlglot`；Python `3.12+`→实为 `3.14`；`gpt-5.2-mini`→settings 默认 `gpt-4o-mini`；异常签名示例也与实现不符 | `CLAUDE.md` |
| 11 | `ValidationConfig.max_question_length` 从未强制，超长 question 直接进 LLM（成本放大） | `orchestrator.py:104` |
| 12 | openai 超时捕获写的是内建 `TimeoutError`，抓不到 `openai.APITimeoutError` → `LLMTimeoutError` 几乎不触发 | `sql_generator.py` / `result_validator.py` |
| 13 | `SQLGenerator.generate()` 从不返回 token 用量，但 `QueryResponse.tokens_used` 字段一直在 | `orchestrator.py:396` 有注释自认 |
| 14 | `Dockerfile:92` HEALTHCHECK 用 `import psutil`，但 `psutil` **不在依赖里** → 健康检查恒失败；compose 侧 healthcheck 用 `curl`，`python:3.14-slim` 里也没有 curl | `Dockerfile` / `docker-compose.yml` |
| 15 | Docker 容器**不会自动建测试库**（fixtures 挂载到 `docker-entrypoint-initdb.d` 的那段被注释了） | `docker-compose.yml` |
| 16 | `db/introspection.py` 存在 N+1 查询（每列额外一次 `_is_column_unique`），55 表大库启动明显变慢；`SchemaCache` 的 `max_size` 配置无淘汰逻辑 | `introspection.py` / `schema_cache.py` |
| 17 | `SQLValidator.validate()` 返回 `tuple[bool, str]`，但 `CLAUDE.md` 测试示例写的是 `validator.validate(sql).is_valid` | `sql_validator.py:102` |
| 18 | `sql_executor.py:91` 的 `timeout = timeout or default`：显式传 `timeout=0` 会退化成默认值 | `sql_executor.py:91` |

---

## 7. 常用命令

```bash
# 补齐全套依赖（必做，否则跑不了测试）
uv sync --all-extras

# 运行真实服务（注意：不是 main.py）
uv run python -m pg_mcp

# 测试
uv run pytest tests/unit/          # 纯 mock，可独立跑
uv run pytest                      # 全量；integration/e2e 需真库 + 真 API key
uv run pytest --cov=src --cov-report=html

# 质量检查
uv run mypy src
uv run ruff check --fix .
uv run ruff format .

# 造测试数据（需本地 PG）
cd fixtures && make create-small    # blog_small, 7 表
make create-medium                  # ecommerce_medium, 25 表
make create-large                   # saas_crm_large, 55+ 表
```

---

## 8. 找路表

| 我想… | 去看 |
|---|---|
| 理解需求边界 | `specs/w5/0001-pg-mcp-prd.md`（父目录） |
| 改查询流程 | `services/orchestrator.py:104` `execute_query` |
| 加/改安全检查 | `services/sql_validator.py`（注意坑 4） |
| 改 LLM 接入 / 换模型 | `services/sql_generator.py:45`、`config/settings.py:46`（注意坑 3） |
| 改结果置信度逻辑 | `services/result_validator.py`、`orchestrator.py:480` |
| 改 Schema 内省 | `db/introspection.py`（注意坑 16） |
| 改配置项 | `config/settings.py`（注意坑 7） |
| 跑起来验证 | `python -m pg_mcp`（**不是** `main.py`） |
| 造测试数据 | `fixtures/Makefile` |
| 已知缺陷全清单 | `specs/w5/0006-pg-mcp-code-review.md` |
| 测试缺口清单 | `specs/w5/0008-pg-mcp-test-plan-review.md` |

---

## 9. 待办（继承自 Codex 代码评审，尚未修复）

按 `specs/w5/0006-pg-mcp-code-review.md` 的优先级：

- **P1** 多数据库支持（坑 7）
- **P1** 恢复安全控制：`blocked_tables` / `blocked_columns` / `allow_explain` 配置化并接线
- **P2** 接线限流 + 重试退避（`resilience/retry.py` 至今缺失）
- **P2** 接线指标与链路追踪 + 健康检查端点
- **P3** 修 `to_dict` 重复定义、合并 `ErrorDetail`、清理未使用配置
- **P4** 补 mock 化集成测试，消除对真实服务的硬依赖
