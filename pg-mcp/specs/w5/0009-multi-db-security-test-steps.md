# 0009 - 多数据库 + 安全控制 测试步骤

> 范围：本次改造的三项能力 —— 多数据库选择、表/列访问限制、EXPLAIN 门控。
> 所有测试值为 2026-10-08 在本机实测所得，非占位符。

---

## 一、该项目是否有访问界面

**结论：没有。**

| 访问面 | 类型 | 位置 | 是否"界面" |
|---|---|---|---|
| `query` 工具 | MCP / stdio | `.mcp.json` → `"type": "stdio"` | ❌ 客户端是 Claude Code / Claude Desktop，无浏览器 |
| `/metrics` | HTTP 9090 | `docker-compose.yml:110-112` | ❌ Prometheus 指标端点，非业务界面 |
| PostgreSQL | TCP 5432 | — | ❌ 仅用于核验数据 |

旁证：

- `docker-compose.yml:144`、`:173` 的 Prometheus / Grafana 均处于**注释**状态，未启用。
- 根目录 `main.py` 是与本服务**无关的遗留 demo**（`FastMCP("special mcp server to add two numbers")`，
  且 `add()` 恒返回 42）。真正的入口是 `src/pg_mcp/__main__.py`，由 `.mcp.json` 拉起。
- 因此本项目的"界面"就是 **MCP 工具契约**：`query(question, database, return_type)`。

> 这意味着：测试**不能**用 Playwright/浏览器做，只能走 MCP 工具调用 + 数据库侧核验。

---

## 二、环境实测事实

| 项 | 实测值 |
|---|---|
| PostgreSQL | `127.0.0.1:5432` 可连通 |
| 服务器上的库 | `adms-cloud`（151 张 public 表）、`adms-gz`（9）、`adms-test`（7）、`postgres` |
| `.env` 当前配置 | user=`root`、host=`localhost`、库=`adms-cloud` |
| `localhost` | **可用**（解析到 `::1`，实连成功）。仅 `DatabaseConfig.dsn` 形态会失败，见限制 5 |
| `fixtures/` 三个库 | `blog_small` / `ecommerce_medium` / `saas_crm_large` **未加载** |
| `users` 表 | 三个 `adms-*` 库**都不存在** |

### 跨库判别标尺

用「public schema 下有多少张表」作为确定性判别：**adms-cloud=151 / adms-gz=9 / adms-test=7**。
三个值互不相同，可无歧义地判断 SQL 实际打到了哪个库。

### 一个必须知道的陷阱

实测发现：让 LLM 生成「统计 users 表有多少行」时，它产出了
`SELECT COUNT(*) AS row_count FROM users;` —— **而 `users` 表在三个库里都不存在**，
校验依然返回 `is_valid: true`。

原因：`SQLValidator` 只做**语法**检查（只允许 SELECT、拦危险函数与黑名单），
不做**语义**校验（表是否存在）。所以：

> **断言必须落在 `data.rows` 上，不能断言 `generated_sql` 的形态。**

---

## 三、必须先处理的阻塞项

| 编号 | 问题 | 处理 |
|---|---|---|
| **B1** | 多库只能写 **JSON 形态**；`DATABASES__0__NAME` 这种嵌套写法会**静默失效**（回落单库，不报错） | 见下方"配置写法实测对照" |

> ✅ **更正记录**：本文档初版把「`DATABASE_HOST=localhost` 无法解析」列为阻塞项，**该结论错误，已撤回**。
> 实测 `localhost` 解析到 `::1`、连接正常，`.env` **无需修改**。当时的 `gaierror` 来自
> `DatabaseConfig.dsn` 字符串形态（该 property 未对密码做 URL 编码），而 `create_pool` 走的是
> kwargs，不受影响。详见"已知限制 5"。

### B1 配置写法实测对照

```bash
# ✅ 可用
DATABASES='[{"name":"adms-cloud","host":"127.0.0.1"},{"name":"adms-gz","host":"127.0.0.1"}]'

# ❌ 静默失效：解析结果仍是单库 adms-cloud，且不报任何错
DATABASES__0__NAME=adms-cloud
DATABASES__1__NAME=adms-gz
```

> ⚠️ 这是**安全相关配置的静默丢失**，与本项目要修的缺陷是同一类问题。
> 建议加一条启动守卫：检测到 `DATABASES__` 前缀的环境变量就直接报错。
> **本项尚未实现，需你决策。**

---

## 四、测试用例

### P0（核心，必须全过）

| 用例编号 | 用例名称 | 优先级 | 测试类型 | 前置条件 | 测试步骤 | 预期结果 |
|---|---|---|---|---|---|---|
| TC_DB_002 | 多库未指定库名 | P0 | 异常 | `DATABASES` 配 3 个库 | 1. 重启 MCP<br>2. 调用 `query(question="public schema 下有多少张表")`，不传 `database` | 1. `success=false`<br>2. `error.code="database_error"`<br>3. `details.available_databases` 含全部 3 个库 |
| TC_DB_003 | 指定库精确路由 | P0 | 正常 | 同上 | 1. 调用 `query(question="public schema 下有多少张表", database="adms-gz", return_type="result")` | 1. `success=true`<br>2. `data.rows[0]` 值为 **9**（不是 151） |
| TC_DB_004 | 请求不存在的库 | P0 | 异常 | 同上 | 1. 调用 `query(question="...", database="adms-nope")` | 1. `success=false`<br>2. `details.requested_database="adms-nope"`<br>3. 列出 3 个可用库 |
| TC_DB_005 | 三库连续切换不串库 | P0 | 正常 | 同上 | 1. 依次请求 `adms-cloud` / `adms-gz` / `adms-test` | 1. 三次 `success=true`<br>2. 行数依次为 **151 / 9 / 7**<br>3. 无一次返回上一个库的结果 |
| TC_SEC_001 | 黑名单表被拦 | P0 | 安全 | `SECURITY_BLOCKED_TABLES=users` | 1. 重启 MCP<br>2. `query(question="统计 users 行数", return_type="result")` | 1. `success=false`<br>2. `error.code="security_violation"`<br>3. 消息含表名 `users` |
| TC_EXP_001 | EXPLAIN 默认关闭 | P0 | 安全 | 不设 `SECURITY_ALLOW_EXPLAIN` | 1. `query(question="给出 users 的查询执行计划")` | 1. `success=false`<br>2. `error.code="security_violation"`<br>3. 消息含 `EXPLAIN statements are not allowed` |

### P1（主要功能）

| 用例编号 | 用例名称 | 优先级 | 测试类型 | 前置条件 | 测试步骤 | 预期结果 |
|---|---|---|---|---|---|---|
| TC_DB_001 | 单库回落（向后兼容） | P1 | 正常 | `DATABASES` **留空**，仅 `DATABASE_NAME=adms-cloud` | 1. 重启 MCP<br>2. `query(question="public schema 下有多少张表", return_type="result")` | 1. `success=true`<br>2. 行数 = **151** |
| TC_SEC_002 | 黑名单列被拦 | P1 | 安全 | `SECURITY_BLOCKED_COLUMNS=password` | 1. 重启 MCP<br>2. `query(question="列出所有用户的 password")` | 1. `success=false`<br>2. `error.code="security_violation"` |
| TC_SEC_003 | 黑名单大小写不敏感 | P1 | 边界 | `SECURITY_BLOCKED_TABLES=users` | 1. `query(question="统计 USERS 表行数")` | 1. 同样被拦（校验器内部转小写） |
| TC_SEC_004 | 黑名单 schema 限定名 | P1 | 边界 | `SECURITY_BLOCKED_TABLES=public.users` | 1. `query(question="统计 public.users 行数")` | 1. 被拦 |
| TC_EXP_002 | 开启后裸 EXPLAIN 放行 | P1 | 正常 | `SECURITY_ALLOW_EXPLAIN=true` | 1. 重启 MCP<br>2. `query(question="给出查询执行计划，不要实际执行")` | 1. `success=true`（或 `success=false` 但错误来自**执行层**而非校验层）<br>2. **不得**是 `security_violation` |
| TC_EXP_003 | 开启后 ANALYZE 仍被拒 | P1 | 安全 | `SECURITY_ALLOW_EXPLAIN=true` | 1. `query(question="用 EXPLAIN ANALYZE 分析这条查询")` | 1. `success=false`<br>2. 消息含 `EXPLAIN ANALYZE is not allowed` |
| TC_EXP_004 | 括号式 ANALYZE 仍被拒 | P1 | 安全 | 同上 | 1. 手工构造 `EXPLAIN (ANALYZE true, BUFFERS true) SELECT 1` 走 `SQLValidator.validate_or_raise` | 1. 抛 `SecurityViolationError` |
| TC_CFG_001 | 重名库启动即失败 | P1 | 异常 | `DATABASES='[{"name":"dup"},{"name":"dup"}]'` | 1. 启动 MCP | 1. `ValidationError`，消息含 `Duplicate database names`<br>2. **不得**静默覆盖 |
| TC_CFG_002 | JSON 形态解析 | P1 | 正常 | 见 B2 的 ✅ 写法 | 1. 启动后观察日志 `Created SQL executor for database '...'` | 1. 三条日志，库名正确 |

### P2（边界 / 接口 / 可观测）

| 用例编号 | 用例名称 | 优先级 | 测试类型 | 前置条件 | 测试步骤 | 预期结果 |
|---|---|---|---|---|---|---|
| TC_SEC_005 | 限定列名黑名单 | P2 | 边界 | `SECURITY_BLOCKED_COLUMNS=users.password` | 1. 构造 `SELECT users.password FROM users` 校验 | 1. 被拦 |
| TC_SEC_006 | 多语句注入 | P2 | 安全 | 默认 | 1. 构造 `SELECT 1; DROP TABLE t` 校验 | 1. 被拦（Multiple statements not allowed） |
| TC_SEC_007 | 危险函数 | P2 | 安全 | 默认 | 1. 构造 `SELECT pg_sleep(10)` 校验 | 1. 被拦（Function 'pg_sleep' is blocked） |
| TC_CFG_003 | 嵌套形态静默失效 | P2 | 安全 | `DATABASES__0__NAME=adms-gz` | 1. 启动 MCP<br>2. 查看 effective 库列表 | 1. **当前**：静默回落单库（**缺陷**）<br>2. **期望**：启动即报错 |
| TC_API_001 | 非法 return_type | P2 | 接口 | — | 1. `query(question="x", return_type="csv")` | 1. `error.code="INVALID_PARAMETER"` |
| TC_API_002 | 空 question | P2 | 接口 | — | 1. `query(question="")` | 1. `success=false`<br>2. `error.code="invalid_request"` |
| TC_OBS_001 | 指标端点 | P2 | 接口 | `OBSERVABILITY_METRICS_ENABLED=true` | 1. `curl -f http://127.0.0.1:9090/metrics` | 1. HTTP 200<br>2. 返回 Prometheus 文本格式 |

**分布**：P0 = 6（26%）· P1 = 9（39%）· P2 = 7（30%）· 共 22 条。

---

## 五、Claude 执行的具体步骤

### A 组 · 离线层（不需数据库，已执行过）

```bash
# A1 单元测试（含装配层回归）
uv run python -m pytest tests/unit/test_server_wiring.py -v

# A2 多库路由 + EXPLAIN 门控 + 配置层
uv run python -m pytest \
  "tests/unit/test_orchestrator.py::TestMultiDatabaseExecutorRouting" \
  "tests/unit/test_sql_validator.py::TestExplainStatements" \
  "tests/unit/test_config.py::TestMultiDatabaseSettings" -v

# A3 全量
uv run python -m pytest tests/ -q

# A4 lint
.venv/Scripts/ruff.exe check src tests --output-format concise
```

> ⚠️ 本机 venv 的 `pytest.exe` / `ruff.exe` 入口脚本生成失败，
> **必须**用 `uv run python -m pytest` 与 `.venv/Scripts/ruff.exe`，
> 直接 `uv run pytest` 会报 `program not found`。

**判定**：A1/A2 全绿；A3 允许 3 个既有失败
（`TestDatabaseConfig/TestOpenAIConfig/TestObservabilityConfig::test_default_values`，
因 `.env` 覆盖类默认值，与本次改造无关）。

### B 组 · 真机端到端（需先解决 B1/B2）

```text
B0. 改 .env：DATABASE_HOST=localhost → 127.0.0.1
B1. 在 .env 追加（JSON 形态，勿用 DATABASES__N__）：
      DATABASES=[{"name":"adms-cloud","host":"127.0.0.1","port":5432,"user":"root","password":"<真实密码>"},
                 {"name":"adms-gz","host":"127.0.0.1","port":5432,"user":"root","password":"<真实密码>"},
                 {"name":"adms-test","host":"127.0.0.1","port":5432,"user":"root","password":"<真实密码>"}]
B2. 【关键】重启 MCP 服务 —— 运行中的实例是改造前的构建。
      在 Claude Code 中重连 MCP（或重启会话）后，才能测到新代码。
      判定：重连后调 query(database="adms-gz") 不再返回 not found。
B3. 逐条执行第四节用例，用两个工具核验：
      · Claude 侧：mcp__pg-mcp__query(...)
      · 数据侧：uv run python -c "..."  连 127.0.0.1:5432 直接查 information_schema
```

**B2 的改造前后对照**（已在本次会话实测得到"改造前"一侧）：

| 阶段 | 调用 | 实测结果 |
|---|---|---|
| 改造前（当前运行实例） | `query(database="adms-gz", return_type="sql")` | ❌ `Database 'adms-gz' not found`，`available_databases: ["adms-cloud"]` |
| 改造前基线（不传库名） | `query(question="统计 users 表有多少行", return_type="sql")` | ✅ `SELECT COUNT(*) AS row_count FROM users;`，`is_valid: true` |
| 改造后（重启后预期） | `query(database="adms-gz", return_type="result")` | ✅ `data.rows` = 9 |

---

## 六、已知限制（不阻塞，须知晓）

1. **裸 EXPLAIN 的内部语句不做表/列黑名单校验**。`sqlglot 28.5.0` 无法可靠解析 EXPLAIN
   （降级为 `Command`），故只拦 `ANALYZE`，不解析内层 SQL。默认关闭时风险为零。
   已记入 `sql_validator.py` 注释；README 无安全章节，未另写。
2. **`DatabaseConfig.name` 既是逻辑键、又是物理库名**（`dsn` 用 `name` 拼）。
   因此不支持「逻辑别名 ≠ 物理库名」。`fixtures/README.md:480-502` 设想的
   `name: blog_db` + `database: blog_small` 写法**当前实现不支持**。
3. **`SQLValidator` 是全局单实例**，三个库共用一份黑名单与 EXPLAIN 开关。
   无法表达「A 库禁 users、B 库不禁」。这是本次批准的 YAGNI 取舍。
4. **校验只做语法、不做语义**（见第二节陷阱）。表不存在不会被拦，会一路走到执行层报错。
5. **`DatabaseConfig.dsn` 未对密码做 URL 编码**（`settings.py` 的 property 直接拼串）。
   当前 `.env` 密码含 1 类特殊字符，实测该 `dsn` 直接 `gaierror`；URL 编码后即可连通。
   影响面：`dsn` / `safe_dsn` 在 `src/` 中**零引用**（`create_pool` 用 kwargs），
   仅 `tests/unit/test_config.py:63` 用到，因此**当前不阻塞运行**，属潜在陷阱
   —— 谁将来接线 `dsn` 就会踩到。
6. **`SQLExecutor.db_config` 是存了从不读的死参数**；`db/pool.py:create_pools` 的
   docstring 声称 "creates pools concurrently" 但实现是串行 for 循环。均为既有问题，本次未动。

---

## 七、手动截图步骤

### 7.0 先认清：本项目没有产品界面

可截图的只有这 4 类画面，每张图"证明什么"要先明确，否则截了也没说服力：

| 图号 | 画面来源 | 证明什么 |
|---|---|---|
| S1–S4 | 终端 | 实现的功能有测试覆盖且全绿 |
| **S5** | Claude Code 工具调用卡片 | **改造前的缺陷**（配多库之前才能拍到） |
| S6–S8 | Claude Code 工具调用卡片 | 改造后功能真的生效 |
| S9 | 浏览器 | 唯一能用浏览器打开的面 —— 且只是 Prometheus 指标端点，非产品界面 |

### 7.1 终端准备（必做，否则中文全是乱码）

Windows 终端先切 UTF-8，再执行任何命令：

```bat
chcp 65001
```

> 实测不切会出现 `public表= 151` 变 `public��= 151` 这类乱码，截图不可用。
> 另外把终端窗口拉宽到 ≥120 列，并关掉换行截断。

### 7.2 截图顺序（**顺序错了会白跑一趟**）

```
S5 →（改 .env 加 DATABASES）→ 重启 MCP → S6、S7
   →（改 .env 加黑名单）→ 再重启 MCP → S8
S1–S4、S9 随时可做
```

**S5 必须最先截**：一旦配好 `DATABASES` 并重启，"库里找不到"的报错就再也复现不出来了。

---

### S1 · 装配层（最关键的一张）

证明：多库配置真的抵达了运行时组件，而不是像改造前那样被硬编码丢掉。

```bash
uv run python -m pytest tests/unit/test_server_wiring.py -v
```

**屏幕应出现**：8 个 `PASSED`，末尾 `8 passed`。
其中 `test_one_executor_per_configured_database`、`test_each_executor_is_bound_to_its_own_pool`
是核心 —— 建议连用例名一起截进去。

### S2 · 执行期按库路由

证明：请求哪个库就打哪个库，不再固定打主库。

```bash
uv run python -m pytest "tests/unit/test_orchestrator.py::TestMultiDatabaseExecutorRouting" -v
```

**屏幕应出现**：`2 passed`，含 `test_executes_on_executor_of_requested_database`。

### S3 · EXPLAIN 门控

证明：默认拒绝，且开启后仍拒绝会真实执行语句的 `ANALYZE`。

```bash
uv run python -m pytest "tests/unit/test_sql_validator.py::TestExplainStatements" -v
```

**屏幕应出现**：`7 passed`，注意含这两条：
- `test_explain_analyze_rejected_even_when_enabled`
- `test_explain_without_analyze_options_allowed`

### S4 · 配置层

```bash
uv run python -m pytest "tests/unit/test_config.py::TestMultiDatabaseSettings" "tests/unit/test_config.py::TestSecurityConfig" -v
```

**屏幕应出现**：全绿，含 `test_duplicate_database_names_rejected`、`test_effective_databases_falls_back_to_single_database`。

### S4b · 全量（建议也截，但必须如实标注）

```bash
uv run python -m pytest tests/ -q
```

**屏幕应出现**：`3 failed, 313 passed`。

> ⚠️ **不要只截聚焦的那几条来暗示"全绿"**。这 3 个失败要一并截进去并注明来源：
> `TestDatabaseConfig / TestOpenAIConfig / TestObservabilityConfig::test_default_values`
> 断言的是类默认值，但这三个类都读 `.env`，真机 `.env` 覆盖成了
> `adms-cloud` / `deepseek-flash` / `text`。**与本次改造无关，且未修改这三个测试。**

---

### S5 · 改造前缺陷复现（**必须先做**）

1. 确认 `.env` 里**没有** `DATABASES`（保持单库现状）
2. 在 Claude Code 里让我调用：

```text
mcp__pg-mcp__query(question="统计 users 表有多少行", database="adms-gz", return_type="sql")
```

**屏幕应出现**（工具调用卡片里的 JSON）：

```json
{"success":false,"error":{"code":"database_error",
 "message":"Database 'adms-gz' not found",
 "details":{"requested_database":"adms-gz","available_databases":["adms-cloud"]}},
 "confidence":0,"tokens_used":0}
```

**截这张图时要连参数一起截**（`database="adms-gz"` 可见），否则看不出"请求了却找不到"。
配合一句话说明：`adms-gz` 在服务器上真实存在（见 S7 的数据库侧核验），服务却只认一个库。

### S6–S7 · 改造后：多库真的生效

1. 编辑 `.env`，追加（**必须 JSON 形态**）：

```ini
DATABASES=[{"name":"adms-cloud","host":"localhost","port":5432,"user":"root","password":"<你的真实密码>"},{"name":"adms-gz","host":"localhost","port":5432,"user":"root","password":"<你的真实密码>"},{"name":"adms-test","host":"localhost","port":5432,"user":"root","password":"<你的真实密码>"}]
```

> 密码含特殊字符时**不要**用 `DatabaseConfig.dsn` 形式（见已知限制 5），
> 但 JSON 里各字段是分开的，不受影响。

2. **重启 MCP**：用 `/mcp` 面板重连，或重启 Claude Code。
   不重启测到的还是旧构建 —— 这一步漏了，S6 会重现 S5 的报错。

3. 依次调用，**每调一次截一张**：

```text
mcp__pg-mcp__query(question="public schema 下有多少张表", database="adms-cloud", return_type="result")  → 期望 151
mcp__pg-mcp__query(question="public schema 下有多少张表", database="adms-gz",    return_type="result")  → 期望 9
mcp__pg-mcp__query(question="public schema 下有多少张表", database="adms-test",  return_type="result")  → 期望 7
```

**屏幕应出现**：三次 `success: true`，`data.rows[0]` 依次为 **151 / 9 / 7**。

> **为什么用这三个数**：151 / 9 / 7 三值互不相同（已实测），是判断 SQL 究竟打到哪个库的
> 确定性标尺。若三次返回同一个数，说明仍在串库 —— 那正是要抓的缺陷。
> ⚠️ **断言看 `data.rows`，别看 `generated_sql`**：LLM 会捏造不存在的表（实测生成过
> `SELECT COUNT(*) FROM users`，而三个库里都没有 `users`），语法校验照样放行。

### S8 · 黑名单生效

1. `.env` 追加：

```ini
SECURITY_BLOCKED_TABLES=users
```

2. **再次重启 MCP**
3. 调用：

```text
mcp__pg-mcp__query(question="统计 users 表有多少行", return_type="result")
```

**屏幕应出现**：`success: false` + `error.code: "security_violation"`，消息含表名 `users`。

> 对照点：改造前 `blocked_tables` 被硬编码成 `None`，这条永远拦不住。

### S9 · 浏览器可打开的唯一个面

浏览器访问：

```text
http://127.0.0.1:9090/metrics
```

**屏幕应出现**：Prometheus 文本格式的指标。

若无响应，说明 `.env` 里 `OBSERVABILITY_METRICS_ENABLED` 不是 `true`（或 `OBSERVABILITY_METRICS_PORT` 不是 9090），
改完需重启 MCP。

> 这张图**不能**用来证明业务功能，只能证明服务起来了、指标端点在跑。
> 不要把它当作"系统界面"放进材料 —— 本项目确实没有界面。

---

### 7.3 截图清单自检

- [ ] S5 是在配 `DATABASES` **之前**拍的
- [ ] S5 的参数 `database="adms-gz"` 在画面内
- [ ] S6/S7 三张的 `data.rows` 值互不相同（151 / 9 / 7）
- [ ] S8 的 `error.code` 是 `security_violation`
- [ ] S1–S4 的用例名（不只 `N passed`）在画面内
- [ ] S4b 的 3 个既有失败已如实截入并标注原因
- [ ] 所有终端截图均为 UTF-8（无 `��` 乱码）
