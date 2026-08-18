# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

数据库查询工具：Web 端管理 PostgreSQL/MySQL 连接、查看元数据、执行 SQL，并支持自然语言生成 SQL（OpenAI）。
- `backend/`：FastAPI + Python 3.12 + SQLModel + SQLite（uv 管理依赖）
- `frontend/`：React 18 + TypeScript + Refine 5 + Ant Design 5 + Vite
- `fixtures/test.rest`：VSCode REST Client 接口测试
- `docs/`：架构文档（重点看 `ARCHITECTURE_REDESIGN.md`、`QUICK_REFERENCE.md`）
- 顶层 `Makefile` 是统一的开发命令入口

## 常用命令

优先用 Makefile（Windows 上无 make 时用 `cd backend && uv ...` 等价命令）：

```bash
make install            # 安装前后端依赖
make setup              # install + 迁移数据库
make dev                # 同时启动前后端（后端 :8000，前端 :5173）
make dev-backend        # uv run uvicorn app.main:app --reload --port 8000
make dev-frontend       # npm run dev

make test               # 全部测试
make test-backend       # cd backend && uv run pytest -v
make test-backend-coverage
make test-frontend      # vitest（单测很少，主要靠 .rest 手动测）

make lint               # ruff(后端) + eslint(前端)
make backend-check      # mypy app + ruff check app
make format             # ruff format + eslint --fix

make db-upgrade         # alembic upgrade head
make db-migrate MESSAGE="..."   # alembic revision --autogenerate
make backend-check      # mypy + ruff（后端）
make frontend-build     # tsc + vite build（前端验证主力）
make health             # 健康检查
```

> ⚠️ 前端 `make lint-frontend` / `make test-frontend` 当前**不可用**：frontend 缺 `eslint.config.js`，vitest 也无测试文件。前端改动验证用 `make frontend-build`（= `tsc && vite build`）+ 浏览器手动测。

后端环境：复制 `backend/.env.example` 为 `backend/.env`，至少配置 `OPENAI_API_KEY`（自然语言功能必需）。配置项见 `backend/app/config.py`（`db_query_data_dir`、`cors_origins`、`query_default_limit`、`metadata_cache_hours` 等）。
前端环境：`VITE_API_BASE_URL`（默认 `http://localhost:8000`）。⚠️ 必须是裸主机，**不要**带 `/api/v1` 后缀——代码里所有请求路径已硬编码 `/api/v1/dbs/...`，带上会拼成 `/api/v1/api/v1/...` 导致 404。

## 后端架构

分层：`api/v1`（HTTP 路由）→ `services`（业务编排）→ `adapters`（数据库实现）→ 目标 PG/MySQL。

**API 端点**（`app/api/v1/`）：
- `PUT /api/v1/dbs/{name}` 创建/更新连接（含连接测试 + URL 自动识别 db_type）
- `GET /api/v1/dbs` 列表；`GET /api/v1/dbs/{name}` 元数据（带 24h 缓存）；`DELETE /api/v1/dbs/{name}`
- `POST /api/v1/dbs/{name}/refresh` 强制刷新元数据
- `POST /api/v1/dbs/{name}/query` 执行 SQL；`GET /api/v1/dbs/{name}/history` 查询历史
- `POST /api/v1/dbs/{name}/query/natural` 自然语言生成 SQL（需 OpenAI）

### 关键：双套数据库代码路径（迁移中间态）

项目正按 `ARCHITECTURE_REDESIGN.md` 从旧 if-elif 结构迁移到适配器模式，**两条路径并存**：

- **新路径（适配器）**：`app/adapters/` — `base.py`（`DatabaseAdapter` ABC + `ConnectionConfig`/`QueryResult`/`MetadataResult` dataclass）、`registry.py`（工厂 + 按 `{db_type}:{name}` 缓存实例）、`postgresql.py`、`mysql.py`。查询执行已迁移：API → `services/query_wrapper.py` → `services/database_service.py`（Facade，内含 SQL 校验 + 计时）→ `registry.get_adapter()` → 具体 Adapter。
- **旧路径（if-elif）**：`services/connection_factory.py` + `db_connection.py`/`mysql_connection.py` + `mysql_metadata.py`/`mysql_query.py`。**元数据提取尚未迁移**：`databases.py` 仍走 `services/metadata.py::fetch_metadata` → 旧的 `connection_factory`。

⚠️ 因此改查询执行去 `adapters/` + `database_service.py`；改元数据提取则要去 `services/metadata.py` + `connection_factory.py`（那是旧代码，尚未适配化）。

### 数据模型与约定

- 应用状态存 **SQLite**（`~/.db_query/db_query.db`，SQLModel 实体见 `app/models/`：`DatabaseConnection`、`DatabaseMetadata`、`QueryHistory`），连接的是外部 PG/MySQL，两者不要混淆。
- **API 一律 camelCase**：Pydantic schema 用 `Field(alias="dbType")` 等（`app/models/schemas.py`），实体内部是 snake_case。
- SQL 校验用 **sqlglot**（`services/sql_validator.py`）：只允许 SELECT，自动补 `LIMIT 1000`，按 db_type 选 dialect。
- 元数据缓存：SQLite 中存 `metadata_json`，24h 过期（`DatabaseMetadata.is_stale`）。
- 查询历史每个库最多保留 50 条（`services/query.py::cleanup_old_queries`）。
- 自然语言：`services/nl2sql.py` 用 OpenAI `gpt-4o-mini`，prompt 内嵌元数据上下文，只生成 SELECT。
- 新增数据库类型的标准流程（5 步）见 `docs/QUICK_REFERENCE.md` 和 `app/adapters/README.md`。

### 后端测试

pytest + pytest-asyncio（`asyncio_mode = auto`）。模式：内存 SQLite 会话 + `MagicMock`/`AsyncMock` 模拟 asyncpg 池，`patch` 连接工厂。参考 `tests/unit/test_query.py`。`conftest.py` 的 `pytest_configure` 会提前导入全部模型注册 SQLModel metadata。

## 前端架构

- 入口 `src/main.tsx` → `App.tsx`：AntD `ConfigProvider` 注入 **MotherDuck 风格主题**（黑边框、Sunbeam Yellow #FFDE00、米色背景 #F4EFEA、全大写标签），项目 UI 改动需保持该风格。
- 主页面 `src/pages/Home.tsx`：三栏布局（左 DatabaseSidebar → 中间 Schema 侧栏 340px → 右主内容区）。QUERY EDITOR 用 `Tabs` 分 MANUAL SQL（Monaco `SqlEditor`）与 NATURAL LANGUAGE（`ChatAssistant` 多轮对话）；MANUAL 下 EXECUTE 旁有 **EXEC & EXPORT** 一键「执行+导出」下拉（CSV/JSON，>10000 行弹确认）；RESULTS 表格支持 CSV/JSON 导出（纯前端、RFC 4180）。
- **导出统一走共享工具 `src/utils/export.tsx`**（.tsx 而非 .ts，因含 `Modal.confirm` JSX）：`downloadCsv`/`downloadJson`、`exportResult(result, format, dbName)`（空结果拦截 + >10000 行确认 + 下载 + 提示）、`detectExportIntent(prompt)`（NL 关键词识别导出意图，默认 csv）。manual 页（Home.tsx）与 `ChatAssistant` 共用；ChatAssistant 查询出结果后主动询问导出（气泡按钮）并支持自然语言触发导出。完整设计思路见根目录 `FEATURE_EXPORT.md`。
- Refine dataProvider（`src/services/dataProvider.ts`）只服务 `databases` 资源；页面内多用 `apiClient`（`src/services/api.ts`，axios）直连后端。
- 组件：`DatabaseSidebar`、`MetadataTree`、`SqlEditor`（Monaco）、`ChatAssistant`、`ResultTable`。
- `src/pages/databases/`（create/list/show，Refine 资源页）与 `src/pages/queries/execute.tsx` 为早期实现，功能较 Home.tsx 精简（如 show.tsx 的 EXPORT 按钮未接线）。

## 面试/演示数据库

`backend/scripts/` 提供测试用 PostgreSQL 库脚本（`create_interview_db.sql`、`seed_interview_data*.sql`、`setup_interview_db.sh`），`fixtures/test.rest` 中的 MySQL 用例面向 `interview_db` 库。改 REST 用例时保持变量风格（`@baseUrl`、`@dbName`）。
