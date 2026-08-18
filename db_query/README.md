# Database Query Tool（数据库查询工具）

基于 Web 的数据库查询工具：管理 PostgreSQL / MySQL 连接、浏览元数据、执行 SQL，并支持自然语言生成 SQL。

## 项目结构

```
w2/db_query/
├── backend/          # FastAPI 后端（Python 3.12+）
├── frontend/         # React 前端（TypeScript, Refine 5）
├── fixtures/         # REST Client 测试文件
│   ├── test.rest     # API 测试请求
│   └── README.md     # 测试指南
└── Makefile          # 开发命令入口
```

## 环境要求

- **Python 3.12+** 与 [uv](https://docs.astral.sh/uv/) —— 后端包管理器
- **Node.js `^20.19.0 || >=22.12.0`** 与 npm —— Vite 7 / `@vitejs/plugin-react` 5 所需

## 快速开始

### 初始设置

```bash
# 安装全部依赖
make install

# 初始化数据库与环境
make setup
# 然后编辑 backend/.env 填入 OPENAI_API_KEY

# 启动开发服务器
make dev
```

> **`backend/.env` 需要真实的 `OPENAI_API_KEY`。** 占位符 `sk-your-api-key-here` 可以让应用正常启动，但自然语言生成 SQL 会失败，直到填入真实 Key。手动 SQL 不受影响。

#### Windows（无 make）

Windows 默认没有 `make`，可直接使用等价命令：

```bash
# 后端
cd backend && uv sync --extra dev
cd backend && uv run uvicorn app.main:app --reload --port 8000

# 前端
cd frontend && npm install
cd frontend && npm run dev

# 数据库迁移
cd backend && uv run alembic upgrade head
```

### 开发命令

```bash
# 查看所有可用命令
make help

# 仅启动后端
make dev-backend

# 仅启动前端
make dev-frontend

# 运行测试
make test

# 格式化代码
make format

# 运行 lint
make lint
```

## 切换大模型

默认使用 OpenAI 官方 `gpt-4o-mini`。系统兼容任何 OpenAI 兼容协议的模型服务，通过 `backend/.env` 配置即可切换，无需改代码。

### 操作步骤

1. 编辑 `backend/.env`，设置以下两项：

```bash
# 兼容端点地址（留空 = OpenAI 官方）
OPENAI_BASE_URL=
# 模型名称（默认 gpt-4o-mini）
OPENAI_MODEL=gpt-4o-mini
```

2. 重启后端使配置生效：

```bash
cd backend && uv run uvicorn app.main:app --reload --port 8000
```

3. 在"自然语言"Tab 输入描述生成 SQL，验证返回结果。

### 常见兼容端点示例

| 服务 | OPENAI_BASE_URL | OPENAI_MODEL |
|---|---|---|
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Moonshot | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 本地 Ollama | `http://localhost:11434/v1` | `llama3.1` |
| One-API 网关 | 网关地址 | 路由模型 |

> 生成结果会经 sqlglot 本地校验（仅允许 SELECT 并自动补 LIMIT 1000），切换模型后安全性仍受后端兜底保护。

## 接口测试

### 使用 REST Client（VSCode）

1. 安装 [REST Client 扩展](https://marketplace.visualstudio.com/items?itemName=humao.rest-client)
2. 打开 `fixtures/test.rest`
3. 在任意 HTTP 请求上方点击 "Send Request"
4. 在 VSCode 面板中查看响应

详细测试指南见 `fixtures/README.md`。

### 使用 Makefile

```bash
# 检查后端是否运行
make health

# 打开 API 文档
make docs
```

## 故障排查

### Windows 下 `uv sync` 报 "Access denied"

uv 会向系统临时目录写入 console-script 启动器，可能被杀毒软件 / Windows Defender 拦截（`os error -2147024891`）。使用可写临时目录重试：

```bash
cd backend && UV_LINK_MODE=copy TMP="D:/tmp" TEMP="D:/tmp" UV_TMP_DIR="D:/tmp" uv sync --extra dev
```

### `alembic upgrade head` 报 "Can't locate revision identified by '002'"

本地已存在的数据库（`~/.db_query/db_query.db`）可能在 `alembic_version` 表中记录了本仓库不存在的迁移脚本版本。先对齐到当前 head，再升级：

```bash
cd backend && uv run python -c "import sqlite3,os; p=os.path.expanduser('~/.db_query/db_query.db'); c=sqlite3.connect(p); c.execute(\"UPDATE alembic_version SET version_num='001'\"); c.commit(); c.close()"
cd backend && uv run alembic upgrade head
```

### 自然语言查询总是失败

`backend/.env` 中缺少真实的 `OPENAI_API_KEY` 时，后端无法生成 SQL。替换占位符后重启后端即可。

## 开发进度

✅ **Phase 1 完成**：所有搭建与基础任务已完成。

- 后端项目结构初始化
- 前端项目结构初始化
- 核心基础设施（FastAPI、数据库、模型）就绪
- 数据模型按 camelCase API 约定定义
- 常用开发任务 Makefile
- REST Client 接口测试文件

✅ **Phase 2 完成**：核心功能 —— 数据库连接管理、元数据浏览、SQL 查询执行、查询历史。

✅ **Phase 3 完成**：自然语言生成 SQL 与 CSV/JSON 导出（见 `PHASE3_IMPLEMENTATION.md`）。

## 下一步

在 `backend/.env` 设置真实的 `OPENAI_API_KEY`，运行 `make dev`，并通过 `fixtures/test.rest` 验证各 API 端点。
