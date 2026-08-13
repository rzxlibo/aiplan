# 对话式 AI 助手 + 导出触发 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 NATURAL LANGUAGE tab 升级为多轮对话式 AI 助手：自动生成 SQL 并执行、结果内联展示，结果后固定提示导出 CSV/JSON。

**Architecture:** 两步式。后端 `nl2sql` 接收多轮 `messages` 历史构造 prompt，模型返回「自然回复 + ```sql 代码块」，后端解析成 `{reply, sql}` 并走 sqlglot 校验；前端新增 `ChatAssistant` 组件串起生成→执行→结果气泡→导出提示条，导出复用抽出的 `utils/export.ts`。后端保持无状态，前端持有会话（内存级）。

**Tech Stack:** FastAPI + Python 3.12 + pytest/pytest-asyncio（后端）；React 18 + TS + AntD 5 + Vite（前端）；OpenAI SDK（兼容端点）。

## Global Constraints

- 后端 SQL 兜底：仅 SELECT + 自动补 `LIMIT 1000`（sqlglot，`validate_and_transform_sql`），自动执行无写入风险。
- 导出格式**仅 CSV/JSON**，不新增格式；沿用 RFC 4180 转义逻辑。
- 会话**内存级**，刷新即清，无后端存储改动。
- 发给后端的对话历史**最多最近 20 条**。
- **Commit 策略（用户已确认）**：本 feature 分支上**允许 AI 逐任务执行 `git commit`**；`push`/`merge` 仍由用户确认（公司红线 R-4 仅约束 push/merge 前人工把关，提交动作已获用户豁免）。每个 Commit 步骤由实施者直接 `git add` + `git commit`。
- 前端保持 MotherDuck 主题（黑边框、Sunbeam Yellow `#FFDE00`、米色 `#F4EFEA`、全大写标签）。
- 后端测试：pytest + pytest-asyncio（`asyncio_mode = auto`），模拟 `AsyncOpenAI` 用 `patch.object(..., new=AsyncMock(...))`。
- 前端验证：`npm run build`（= `tsc && vite build`）+ `npm run lint`；无组件单测基础设施（vitest 未配置），用浏览器 + `.rest` 手动验证。

---

### Task 1: 后端 schemas —— ChatMessage + messages 字段 + reply 字段

**Files:**
- Modify: `backend/app/models/schemas.py`
- Create: `backend/tests/unit/test_schemas.py`

**Interfaces:**
- Consumes: 无（前置改造）。
- Produces: `ChatMessage(role: Literal["user","assistant"], content: str)`；`NaturalLanguageInput(prompt: str, messages: list[ChatMessage] = [])`；`GeneratedSqlResponse(reply: str, sql: str)`。Task 2/3 依赖这些类型。

- [ ] **Step 1: 写失败测试 `backend/tests/unit/test_schemas.py`**

```python
"""Unit tests for API schemas."""

from app.models.schemas import ChatMessage, NaturalLanguageInput, GeneratedSqlResponse


def test_chat_message_schema():
    msg = ChatMessage(role="user", content="show users")
    assert msg.role == "user"
    assert msg.content == "show users"


def test_natural_language_input_messages_default_empty():
    req = NaturalLanguageInput(prompt="show users")
    assert req.messages == []


def test_natural_language_input_with_messages():
    req = NaturalLanguageInput(
        prompt="show orders",
        messages=[ChatMessage(role="user", content="show users")],
    )
    assert req.prompt == "show orders"
    assert len(req.messages) == 1


def test_generated_sql_response_reply_field():
    resp = GeneratedSqlResponse(reply="Here you go", sql="SELECT 1")
    assert resp.reply == "Here you go"
    assert resp.sql == "SELECT 1"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd backend && uv run pytest tests/unit/test_schemas.py -v`
Expected: FAIL — `ImportError`（`ChatMessage` 不存在）或 `TypeError: unexpected keyword argument 'reply'`。

- [ ] **Step 3: 修改 `backend/app/models/schemas.py`**

在文件顶部确认 `from typing import Literal` 存在；在 Natural Language Schemas 段（第 102-113 行附近）改为：

```python
# Natural Language Schemas
class ChatMessage(BaseModel):
    """A single message in the conversation history."""

    role: Literal["user", "assistant"]
    content: str


class NaturalLanguageInput(BaseModel):
    """Input schema for natural language to SQL conversion."""

    prompt: str = Field(..., min_length=5, max_length=500)
    messages: list[ChatMessage] = Field(default_factory=list)


class GeneratedSqlResponse(BaseModel):
    """Response schema for generated SQL."""

    reply: str
    sql: str
```

（若 `Literal` 未导入，在文件顶部 `from typing import Literal`。）

- [ ] **Step 4: 跑测试验证通过**

Run: `cd backend && uv run pytest tests/unit/test_schemas.py -v`
Expected: 4 passed。

- [ ] **Step 5: 准备提交**

```bash
git add backend/app/models/schemas.py backend/tests/unit/test_schemas.py
git commit -m "feat(schema): 自然语言输入支持多轮消息历史，响应改 reply/sql"
```

---

### Task 2: nl2sql 多轮生成 + 回复解析 + 异常收敛

**Files:**
- Modify: `backend/app/services/nl2sql.py`
- Modify: `backend/tests/unit/test_nl2sql.py`

**Interfaces:**
- Consumes: Task 1 的契约（`reply`/`sql`）；既有 `settings`、`validate_and_transform_sql`。
- Produces: `NaturalLanguageToSQLService.generate_sql(prompt: str, metadata: dict, db_type: DatabaseType = POSTGRESQL, messages: list[dict[str,str]] | None = None) -> dict[str,str]`（键 `reply`、`sql`）；`_build_prompt(user_prompt, metadata, db_type, messages=None)`；`_parse_response(raw) -> {"reply","sql"}`；`_sanitize_error(msg, max_len=500) -> str`。Task 3 调用 `generate_sql`。

- [ ] **Step 1: 更新 + 新增失败测试 `backend/tests/unit/test_nl2sql.py`**

先按下列改动更新现有测试（因为输出结构变化）：

1. `TestGenerateSql.test_generate_sql_basic_query`：mock content 改为 `"Here are the users.\n```sql\nSELECT * FROM public.users LIMIT 100\n```"`；断言 `result["sql"] == "SELECT * FROM public.users LIMIT 100"`、`result["reply"] == "Here are the users."`；删掉 `assert "Show me all users" in result["explanation"]`。
2. `TestGenerateSql.test_generate_sql_with_chinese_prompt`、`test_generate_sql_with_join`：mock content 同样改为「自然句 + ```sql 块」；`test_generate_sql_with_join` 断言 JOIN 不变。
3. `test_generate_sql_removes_markdown` / `test_generate_sql_removes_generic_markdown`：content 保持纯代码块，断言 `result["sql"]` 提取正确且 `"```" not in result["sql"]`。

在文件末尾新增两个测试类：

```python
class TestMultiTurn:
    """Test multi-turn history and reply parsing."""

    @pytest.mark.asyncio
    async def test_generate_sql_parses_reply_and_sql(self, nl2sql_service, sample_metadata):
        """自然句 + SQL 代码块被正确拆成 reply 和 sql。"""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content="Here are the active users.\n```sql\nSELECT id, name FROM public.users WHERE active = true LIMIT 100\n```"
                )
            )
        ]

        with patch.object(
            nl2sql_service.client.chat.completions,
            "create",
            new=AsyncMock(return_value=mock_response),
        ):
            result = await nl2sql_service.generate_sql("show active users", sample_metadata)

        assert result["reply"] == "Here are the active users."
        assert result["sql"] == "SELECT id, name FROM public.users WHERE active = true LIMIT 100"

    @pytest.mark.asyncio
    async def test_generate_sql_includes_history(self, nl2sql_service, sample_metadata):
        """历史消息进入 prompt，当前输入作为最后一条 user 消息。"""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="```sql\nSELECT * FROM public.orders LIMIT 100\n```"))
        ]

        history = [
            {"role": "user", "content": "show all users"},
            {"role": "assistant", "content": "SELECT * FROM public.users LIMIT 100"},
        ]

        with patch.object(
            nl2sql_service.client.chat.completions,
            "create",
            new=AsyncMock(return_value=mock_response),
        ) as mock_create:
            await nl2sql_service.generate_sql("now show orders", sample_metadata, messages=history)

        sent = mock_create.call_args.kwargs["messages"]
        roles = [m["role"] for m in sent]
        assert roles == ["system", "user", "assistant", "user"]
        assert sent[1]["content"] == "show all users"
        assert sent[2]["content"] == "SELECT * FROM public.users LIMIT 100"
        assert sent[-1]["content"] == "now show orders"

    @pytest.mark.asyncio
    async def test_generate_sql_sanitizes_html_error(self, nl2sql_service, sample_metadata):
        """拦截页 HTML 不应整段透传给调用方。"""
        html_error = (
            "<!DOCTYPE html><html><body><title>Error - Request Blocked</title>"
            "<p>Some long body text</p></body></html>"
        )
        with patch.object(
            nl2sql_service.client.chat.completions,
            "create",
            new=AsyncMock(side_effect=Exception(html_error)),
        ):
            with pytest.raises(Exception) as exc_info:
                await nl2sql_service.generate_sql("show users", sample_metadata)

        msg = str(exc_info.value)
        assert "Failed to generate SQL" in msg
        assert "<html" not in msg
        assert "<title>" not in msg

    def test_build_prompt_includes_history(self, nl2sql_service, sample_metadata):
        """_build_prompt 组装 system + 历史 + 当前 user。"""
        messages = nl2sql_service._build_prompt(
            user_prompt="show orders",
            metadata=sample_metadata,
            messages=[
                {"role": "user", "content": "show users"},
                {"role": "assistant", "content": "SELECT * FROM public.users LIMIT 100"},
            ],
        )
        roles = [m["role"] for m in messages]
        assert roles == ["system", "user", "assistant", "user"]
        assert messages[1]["content"] == "show users"
        assert messages[2]["content"] == "SELECT * FROM public.users LIMIT 100"
        assert messages[3]["content"] == "show orders"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd backend && uv run pytest tests/unit/test_nl2sql.py -v`
Expected: 新测试 FAIL（`generate_sql` 无 `messages` 参数 / 返回无 `reply` 键）；部分旧测试因 mock 格式变化 FAIL。

- [ ] **Step 3: 实现 `backend/app/services/nl2sql.py`**

文件顶部 `import logging`、`import re`。改动如下：

1. `__init__` 加显式超时：

```python
self.client = AsyncOpenAI(
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url or None,
    timeout=30.0,
)
self.model = settings.openai_model
```

2. `_build_prompt` 签名加 `messages=None`，并在 system 消息后插入历史：

```python
def _build_prompt(
    self,
    user_prompt: str,
    metadata: dict,
    db_type: DatabaseType = DatabaseType.POSTGRESQL,
    messages: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """..."""
    # ...（schema 上下文拼接、syntax_rules 逻辑保持原样）...

    # system_message 追加一条输出格式规则：
    # 在 rules 里追加:
    # 9. First output one sentence of natural language explanation,
    #    then the SQL wrapped in a ```sql code block.

    msgs: list[dict[str, str]] = [{"role": "system", "content": system_message}]
    for m in messages or []:
        msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": user_prompt})
    return msgs
```

3. 新增两个私有方法：

```python
@staticmethod
def _parse_response(raw: str) -> dict[str, str]:
    """从模型输出中提取回复文本与 SQL。

    格式约定：一句自然语言说明 + ```sql 代码块。找不到代码块时整段视为 SQL。
    """
    match = re.search(r"```(?:sql)?\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    if match:
        sql = match.group(1).strip()
        reply = (raw[: match.start()] + raw[match.end():]).strip()
    else:
        sql = raw.strip()
        reply = ""
    return {"reply": reply, "sql": sql}

@staticmethod
def _sanitize_error(msg: str, max_len: int = 500) -> str:
    """剥离 HTML 标签、压缩空白并截断，避免整段拦截页抛给前端。"""
    msg = re.sub(r"<[^>]+>", " ", msg)
    msg = re.sub(r"\s+", " ", msg).strip()
    if len(msg) > max_len:
        msg = msg[:max_len] + "...(truncated)"
    return msg
```

4. `generate_sql` 改为多轮 + 解析 + 校验 + 异常收敛：

```python
async def generate_sql(
    self,
    user_prompt: str,
    metadata: dict,
    db_type: DatabaseType = DatabaseType.POSTGRESQL,
    messages: list[dict[str, str]] | None = None,
) -> dict[str, str]:
    """...返回 {'reply', 'sql'}..."""
    try:
        msgs = self._build_prompt(user_prompt, metadata, db_type, messages)

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=msgs,
            temperature=0.1,
            max_tokens=500,
        )

        generated = response.choices[0].message.content.strip()
        parsed = self._parse_response(generated)

        try:
            parsed["sql"] = validate_and_transform_sql(
                parsed["sql"], limit=1000, db_type=db_type
            )
        except SqlValidationError as e:
            logger.error(f"Generated SQL failed validation: {e}")
            raise Exception(f"Generated SQL is not valid (SELECT only): {e}")

        logger.info(f"Generated SQL for prompt: {user_prompt[:50]}...")
        return parsed

    except Exception as e:
        message_str = self._sanitize_error(str(e))
        logger.error(f"Failed to generate SQL: {message_str}")
        raise Exception(f"Failed to generate SQL: {message_str}") from e
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd backend && uv run pytest tests/unit/test_nl2sql.py -v`
Expected: 全部通过（含新增 4 个）。

- [ ] **Step 5: 准备提交**

```bash
git add backend/app/services/nl2sql.py backend/tests/unit/test_nl2sql.py
git commit -m "feat(nl2sql): 多轮消息历史、回复解析为 reply/sql、异常信息收敛"
```

---

### Task 3: 路由自然语言端点接入多轮 + 更新 API 测试

**Files:**
- Modify: `backend/app/api/v1/queries.py`
- Modify: `backend/tests/unit/test_api_queries.py`

**Interfaces:**
- Consumes: Task 1 的 `NaturalLanguageInput.messages`、`GeneratedSqlResponse.reply`；Task 2 的 `generate_sql(prompt, metadata, db_type, messages=...)`。
- Produces: `POST /api/v1/dbs/{name}/query/natural` 接收 `{prompt, messages}`，返回 `{reply, sql}`。

- [ ] **Step 1: 更新失败测试 `backend/tests/unit/test_api_queries.py` 的 `TestNaturalLanguageToSql`**

改动如下（第 332-433 行）：

1. `test_natural_language_to_sql`（第 335 行）：mock 返回值改为 `{"reply": "Here you go", "sql": "SELECT * FROM public.users LIMIT 100"}`；断言 `data["sql"]`、`data["reply"] == "Here you go"`；删掉 `"explanation" in data`。`assert_called_once_with` 改为与实际调用匹配：

```python
mock_generate.assert_called_once_with(
    "Show me all users",
    sample_metadata,
    DatabaseType.POSTGRESQL,
    messages=[],
)
```

（文件顶部需 `from app.models.database import DatabaseType`，若无则加。）

2. `test_natural_language_to_sql_chinese`（第 417 行）：mock 返回值 `"explanation"` 键改 `"reply"`，断言 `"reply" in data`、`"sql" in data`。

3. 其余（database not found / no metadata / generation error / 短/长 prompt）保持不变。

新增一个带历史消息的用例：

```python
@patch("app.api.v1.queries.nl2sql_service.generate_sql")
def test_natural_language_to_sql_with_history(self, mock_generate, client, sample_connection, sample_metadata):
    """带历史消息的自然语言请求透传给 generate_sql。"""
    mock_generate.return_value = {
        "reply": "Here are the orders.",
        "sql": "SELECT * FROM public.orders LIMIT 100",
    }
    response = client.post(
        "/api/v1/dbs/test_db/query/natural",
        json={
            "prompt": "now show orders",
            "messages": [
                {"role": "user", "content": "show users"},
                {"role": "assistant", "content": "SELECT * FROM public.users LIMIT 100"},
            ],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["reply"] == "Here are the orders."
    assert data["sql"] == "SELECT * FROM public.orders LIMIT 100"

    mock_generate.assert_called_once()
    kwargs = mock_generate.call_args.kwargs
    assert kwargs["messages"] == [
        {"role": "user", "content": "show users"},
        {"role": "assistant", "content": "SELECT * FROM public.users LIMIT 100"},
    ]
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd backend && uv run pytest tests/unit/test_api_queries.py -k "NaturalLanguage" -v`
Expected: FAIL（route 仍返回 `explanation` / 未透传 messages）。

- [ ] **Step 3: 实现 `backend/app/api/v1/queries.py`**

改 `natural_language_to_sql`（第 171-182 行）的生成与返回部分：

```python
    # Generate SQL
    try:
        result = await nl2sql_service.generate_sql(
            input_data.prompt,
            metadata,
            connection.db_type,
            messages=[m.model_dump() for m in input_data.messages],
        )
        return GeneratedSqlResponse(
            reply=result["reply"],
            sql=result["sql"],
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
```

> 注意：`detail` 从 `f"Failed to generate SQL: {str(e)}"` 改为 `str(e)`，避免与 nl2sql 内部已带的 `"Failed to generate SQL: "` 前缀重复。现有断言 `"Failed to generate SQL" in detail` 仍满足。

- [ ] **Step 4: 跑测试验证通过**

Run: `cd backend && uv run pytest tests/unit/test_api_queries.py -v`
Expected: 全部通过（含新增 history 用例）。

- [ ] **Step 5: 准备提交**

```bash
git add backend/app/api/v1/queries.py backend/tests/unit/test_api_queries.py
git commit -m "feat(api): 自然语言端点透传多轮消息历史，响应改 reply/sql"
```

---

### Task 4: 更新 REST 测试用例

**Files:**
- Modify: `fixtures/test.rest`

**Interfaces:**
- Consumes: Task 3 的请求/响应契约。

- [ ] **Step 1: 修改 `fixtures/test.rest` 中 natural 端点用例**

找到 `POST /api/v1/dbs/{{dbName}}/query/natural` 用例，请求体改为带 `messages`，响应断言/说明改 `reply`/`sql`：

```rest
### Natural Language to SQL
POST {{baseUrl}}/api/v1/dbs/{{dbName}}/query/natural
Content-Type: application/json

{
  "prompt": "显示所有用户的前100条记录",
  "messages": []
}
```

> 说明文字注明：返回 `{ "reply": "自然语言说明", "sql": "生成的SQL" }`，`sql` 已由后端 sqlglot 校验（仅 SELECT + LIMIT 1000）。多轮对话时把前面的 user/assistant 消息放入 `messages`。

- [ ] **Step 2: 验证（可选，需后端运行 + 真实 Key）**

Run: 启动后端后在 VSCode REST Client 点 Send Request，确认 200 且返回 `reply`/`sql`。
若本机未配真实 Key，标记为「依赖外部 Key，CI/手动时验证」。

- [ ] **Step 3: 准备提交**

```bash
git add fixtures/test.rest
git commit -m "docs(rest): 更新自然语言端点用例为多轮 messages 与 reply/sql 响应"
```

---

### Task 5: 前端共享类型 + 导出工具

**Files:**
- Create: `frontend/src/types/query.ts`
- Create: `frontend/src/utils/export.ts`

**Interfaces:**
- Consumes: 无（从 Home.tsx 抽出现有逻辑）。
- Produces: `QueryResult` 类型（`frontend/src/types/query.ts`）；`downloadCsv(result: QueryResult, dbName: string): void`、`downloadJson(result: QueryResult, dbName: string): void`（`frontend/src/utils/export.ts`）。Task 6/7 依赖。

- [ ] **Step 1: 创建 `frontend/src/types/query.ts`**

```ts
/** Query result type shared across the app. */

export interface QueryResult {
  columns: Array<{ name: string; dataType: string }>;
  rows: Array<Record<string, any>>;
  rowCount: number;
  executionTimeMs: number;
  sql: string;
}
```

- [ ] **Step 2: 创建 `frontend/src/utils/export.ts`**

把 Home.tsx 第 158-222 行的导出逻辑迁移至此（RFC 4180 转义、Blob 下载、文件名带库名+时间戳）：

```ts
/** CSV/JSON export helpers (pure frontend). */

import { QueryResult } from "../types/query";

function makeFilename(dbName: string, ext: string): string {
  const timestamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, -5);
  return `${dbName}_${timestamp}.${ext}`;
}

export function downloadCsv(result: QueryResult, dbName: string): void {
  const headers = result.columns.map((col) => col.name);
  const csvRows = [headers.join(",")];

  result.rows.forEach((row) => {
    const values = headers.map((header) => {
      const value = row[header];
      if (value === null || value === undefined) return "";
      const s = String(value);
      if (s.includes(",") || s.includes('"') || s.includes("\n")) {
        return `"${s.replace(/"/g, '""')}"`;
      }
      return s;
    });
    csvRows.push(values.join(","));
  });

  const blob = new Blob([csvRows.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = makeFilename(dbName, "csv");
  link.click();
  URL.revokeObjectURL(url);
}

export function downloadJson(result: QueryResult, dbName: string): void {
  const blob = new Blob([JSON.stringify(result.rows, null, 2)], {
    type: "application/json;charset=utf-8;",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = makeFilename(dbName, "json");
  link.click();
  URL.revokeObjectURL(url);
}
```

- [ ] **Step 3: 验证类型**

Run: `cd frontend && npm run build`
Expected: tsc 无报错，构建成功。

- [ ] **Step 4: 准备提交**

```bash
git add frontend/src/types/query.ts frontend/src/utils/export.ts
git commit -m "feat(frontend): 抽取共享 QueryResult 类型与 CSV/JSON 导出工具"
```

---

### Task 6: ChatAssistant 对话组件

**Files:**
- Create: `frontend/src/components/ChatAssistant.tsx`

**Interfaces:**
- Consumes: Task 5 的 `QueryResult`、`downloadCsv`/`downloadJson`；既有 `apiClient`（`frontend/src/services/api.ts`）。
- Produces: `ChatAssistant({ databaseName: string })`，内部自动「生成→执行→渲染结果→导出提示」。Task 7 在 Home.tsx 引用。

- [ ] **Step 1: 创建 `frontend/src/components/ChatAssistant.tsx`**

```tsx
/** Multi-turn natural-language chat assistant with inline query results. */

import React, { useRef, useState } from "react";
import { Alert, Button, Input, Space, Table, Typography, message } from "antd";
import {
  DeleteOutlined,
  LoadingOutlined,
  SendOutlined,
} from "@ant-design/icons";
import { apiClient } from "../services/api";
import { QueryResult } from "../types/query";
import { downloadCsv, downloadJson } from "../utils/export";

const { TextArea } = Input;
const { Text } = Typography;

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sql?: string;
  result?: QueryResult;
  error?: string;
  status?: "generating" | "executing" | "done" | "error";
}

interface ChatAssistantProps {
  databaseName: string;
}

const MAX_HISTORY = 20;

/** 把 assistant 消息序列化为「自然回复 + SQL 代码块」，作为历史发给模型。 */
function toHistoryContent(m: ChatMessage): string {
  if (m.sql) {
    return `${m.content}\n\n\`\`\`sql\n${m.sql}\n\`\`\``;
  }
  return m.content;
}

export const ChatAssistant: React.FC<ChatAssistantProps> = ({ databaseName }) => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    setTimeout(() => {
      if (listRef.current) {
        listRef.current.scrollTop = listRef.current.scrollHeight;
      }
    }, 50);
  };

  const updateMessage = (id: string, patch: Partial<ChatMessage>) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch } : m)));
  };

  const handleSend = async () => {
    const prompt = input.trim();
    if (!prompt || sending) return;

    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: prompt,
    };
    const assistantMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "",
      status: "generating",
    };
    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setInput("");
    setSending(true);
    scrollToBottom();

    try {
      // 历史 = 当前这轮之前的所有消息（messages 闭包未包含刚追加的两条）
      const history = messages.map((m) => ({
        role: m.role,
        content: m.role === "assistant" ? toHistoryContent(m) : m.content,
      }));

      const genRes = await apiClient.post<{ reply: string; sql: string }>(
        `/api/v1/dbs/${databaseName}/query/natural`,
        { prompt, messages: history.slice(-MAX_HISTORY) }
      );
      const { reply, sql } = genRes.data;
      updateMessage(assistantMsg.id, { content: reply, sql, status: "executing" });

      const queryRes = await apiClient.post<QueryResult>(
        `/api/v1/dbs/${databaseName}/query`,
        { sql }
      );
      updateMessage(assistantMsg.id, { result: queryRes.data, status: "done" });
      message.success(
        `Query executed - ${queryRes.data.rowCount} rows in ${queryRes.data.executionTimeMs}ms`
      );
    } catch (error: any) {
      updateMessage(assistantMsg.id, {
        status: "error",
        error:
          error.response?.data?.detail || error.message || "Request failed",
      });
    } finally {
      setSending(false);
      scrollToBottom();
    }
  };

  const handleClear = () => setMessages([]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      handleSend();
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          助手会自动生成 SQL 并执行，结果内联显示。历史仅保留本次会话。
        </Text>
        <Button
          size="small"
          icon={<DeleteOutlined />}
          onClick={handleClear}
          disabled={messages.length === 0}
        >
          清空对话
        </Button>
      </div>

      <div
        ref={listRef}
        style={{
          flex: 1,
          overflowY: "auto",
          maxHeight: 420,
          border: "1px solid #E4D6C3",
          borderRadius: 2,
          padding: 12,
          marginBottom: 12,
          background: "#FFFFFF",
        }}
      >
        {messages.length === 0 && (
          <Text type="secondary" style={{ fontSize: 13 }}>
            用自然语言提问，例如：「显示所有用户」或「Show me active users」。
          </Text>
        )}

        {messages.map((m) => (
          <div
            key={m.id}
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: m.role === "user" ? "flex-end" : "flex-start",
              marginBottom: 12,
            }}
          >
            <div
              style={{
                maxWidth: "92%",
                padding: "8px 12px",
                border: "2px solid #000000",
                borderRadius: 2,
                background: m.role === "user" ? "#FFDE00" : "#FFFFFF",
              }}
            >
              {m.role === "assistant" && m.content && (
                <div style={{ fontSize: 13, marginBottom: m.sql ? 8 : 0, whiteSpace: "pre-wrap" }}>
                  {m.content}
                </div>
              )}
              {m.role === "assistant" && m.status === "generating" && (
                <Text type="secondary"><LoadingOutlined /> 正在生成 SQL…</Text>
              )}
              {m.role === "assistant" && m.sql && (
                <pre
                  style={{
                    background: "#1E1E1E",
                    color: "#D4D4D4",
                    padding: 10,
                    borderRadius: 2,
                    fontSize: 12,
                    overflowX: "auto",
                    margin: "8px 0 0",
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-all",
                  }}
                >
                  {m.sql}
                </pre>
              )}
              {m.role === "assistant" && m.status === "executing" && (
                <div style={{ marginTop: 8 }}>
                  <Text type="secondary"><LoadingOutlined /> 正在执行查询…</Text>
                </div>
              )}
              {m.role === "assistant" && m.result && (
                <>
                  <div style={{ marginTop: 8 }}>
                    <Table
                      columns={m.result.columns.map((col) => ({
                        title: col.name,
                        dataIndex: col.name,
                        key: col.name,
                        ellipsis: true,
                      }))}
                      dataSource={m.result.rows}
                      rowKey={(_r, i) => i?.toString() || "0"}
                      pagination={{
                        pageSize: 50,
                        showSizeChanger: true,
                        showTotal: (total) => `Total ${total} rows`,
                        pageSizeOptions: [10, 20, 50, 100],
                      }}
                      size="middle"
                      bordered
                    />
                  </div>
                  {m.result.rows.length > 0 && (
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        需要导出为 CSV/JSON？
                      </Text>
                      <Button size="small" onClick={() => downloadCsv(m.result!, databaseName)}>
                        导出CSV
                      </Button>
                      <Button size="small" onClick={() => downloadJson(m.result!, databaseName)}>
                        导出JSON
                      </Button>
                    </div>
                  )}
                </>
              )}
              {m.role === "assistant" && m.status === "error" && m.error && (
                <Alert
                  message="操作失败"
                  description={m.error}
                  type="error"
                  showIcon
                  style={{ marginTop: 8 }}
                />
              )}
            </div>
          </div>
        ))}
      </div>

      <Space direction="vertical" style={{ width: "100%" }} size={8}>
        <TextArea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="用自然语言描述你的查询…"
          rows={3}
          style={{ fontSize: 14, borderWidth: 2, borderRadius: 2 }}
          disabled={sending}
        />
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            Cmd/Ctrl + Enter 发送
          </Text>
          <Button
            type="primary"
            icon={sending ? <LoadingOutlined /> : <SendOutlined />}
            onClick={handleSend}
            loading={sending}
            disabled={!input.trim() || sending}
            style={{ height: 40, paddingLeft: 20, paddingRight: 20, fontWeight: 700 }}
          >
            SEND
          </Button>
        </div>
      </Space>
    </div>
  );
};
```

- [ ] **Step 2: 验证类型与构建**

Run: `cd frontend && npm run build`
Expected: tsc 无报错，构建成功。

- [ ] **Step 3: 手动冒烟（需后端 + 真实 Key）**

启动前后端，进入 Home 的 NATURAL LANGUAGE tab，输入「显示所有用户」，确认：生成→自动执行→结果表格出现在气泡内→出现「导出CSV/导出JSON」提示条→点击可下载。多轮再问一次，确认历史生效（模型能参考上一句）。
> 若无真实 Key，此步标记为待外部验证，先以 `npm run build` 通过为准。

- [ ] **Step 4: 准备提交**

```bash
git add frontend/src/components/ChatAssistant.tsx
git commit -m "feat(frontend): 新增多轮对话 AI 助手组件，内联结果与导出提示"
```

---

### Task 7: Home.tsx 集成 + 清理旧组件

**Files:**
- Modify: `frontend/src/pages/Home.tsx`
- Delete: `frontend/src/components/NaturalLanguageInput.tsx`

**Interfaces:**
- Consumes: Task 5 的 `QueryResult`/`downloadCsv`/`downloadJson`；Task 6 的 `ChatAssistant`。
- Produces: 无（最终态）。

- [ ] **Step 1: 修改 `frontend/src/pages/Home.tsx`**

1. import 调整：
   - 移除 `import { NaturalLanguageInput } from "../components/NaturalLanguageInput";`
   - 新增 `import { ChatAssistant } from "../components/ChatAssistant";`
   - 新增 `import { QueryResult } from "../types/query";`、`import { downloadCsv, downloadJson } from "../utils/export";`
   - 删除文件内第 35-41 行的本地 `interface QueryResult {...}`（改用共享类型）。

2. 删除不再使用的 state 与处理函数（第 52-53 行 `generatingSql`/`nlError`；第 117-137 行 `handleGenerateSQL`）。

3. NATURAL LANGUAGE tab children（第 587-595 行）改为——注意 `key={selectedDatabase}`：切换数据库时强制重建组件，达到"清空对话、避免串库"（§6）：

```tsx
children: (
  <div style={{ padding: "12px 0" }}>
    <ChatAssistant
      key={selectedDatabase}
      databaseName={selectedDatabase}
    />
  </div>
),
```

4. 导出按钮改造（第 139-222 行）——保留 `>10000` 行确认，实际下载改调 utils。`handleExportCSV` 改为：

```tsx
const handleExportCSV = () => {
  const result = queryResult; // 捕获本次渲染值，供 Modal.confirm onOk 闭包使用
  if (!result || result.rows.length === 0) {
    message.warning("No data to export");
    return;
  }
  if (result.rows.length > 10000) {
    Modal.confirm({
      title: "Large Dataset Warning",
      icon: <ExclamationCircleOutlined />,
      content: `You are about to export ${result.rowCount.toLocaleString()} rows. This may take a while and consume memory. Continue?`,
      onOk: () => downloadCsv(result, selectedDatabase),
    });
  } else {
    downloadCsv(result, selectedDatabase);
  }
};
```

`handleExportJSON` 同理，`onOk: () => downloadJson(result, selectedDatabase)`。

5. 删除文件内 `exportToCSV`、`exportToJSON`（第 158-222 行，逻辑已迁至 utils）。

- [ ] **Step 2: 删除旧组件**

Run: `rm frontend/src/components/NaturalLanguageInput.tsx`

- [ ] **Step 3: 验证构建与 lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: tsc 无报错、eslint 0 错误。

- [ ] **Step 4: 全量回归 + 手动验证**

Run: `cd backend && uv run pytest -v`（确认后端无回归；基线存在的无关失败可忽略——对比 `git stash` 验证过与本次改动无关）
Run: 浏览器手动过一遍：MANUAL SQL 执行 + CSV/JSON 导出按钮正常；NATURAL LANGUAGE 对话 + 自动执行 + 气泡导出正常；切换数据库后对话被清空。

- [ ] **Step 5: 准备提交**

```bash
git add frontend/src/pages/Home.tsx
git rm frontend/src/components/NaturalLanguageInput.tsx
git commit -m "feat(frontend): Home 集成对话助手，导出改调共享工具，移除旧自然语言输入组件"
```

---

## 自审记录

**1. Spec 覆盖：**
- 对话式助手（§2）→ Task 6；自动执行（§3/§5.3）→ Task 6 `handleSend`；完整多轮记忆（§2）→ Task 2 + Task 6 历史序列化；前端固定导出提示（§2/§5.3）→ Task 6 结果气泡内提示条；自然回复+SQL（§2/§4.2）→ Task 2 `_parse_response`；仅 CSV/JSON（§2）→ Task 5；内存级会话（§2）→ Task 6 `messages` state。
- 后端：schemas（§4.1）→ Task 1；nl2sql 多轮/校验/健壮性（§4.2）→ Task 2；路由（§4.3）→ Task 3；异常收敛（§4.2 健壮性）→ Task 2 `_sanitize_error` + Task 3 `detail=str(e)`。
- 前端：utils/export.ts（§5.1）→ Task 5；types/query.ts（§5.2）→ Task 5；ChatAssistant（§5.3）→ Task 6；Home 集成 + 删除旧组件（§5.4）→ Task 7。
- 错误处理与边界（§6）：生成失败→Task 6 Alert；执行失败保留 SQL→Task 6（`error` 分支不清 sql）；超时→Task 2 timeout=30；历史上限 20→Task 6 `MAX_HISTORY`；空结果无提示→Task 6 `rows.length > 0` 条件；切换库清空→Task 7 用 `key` 需补充（见下）。
- 测试策略（§7）：后端→Task 2/3；.rest→Task 4；前端手动→Task 6/7。

**2. Placeholder 扫描：** 无 TBD/TODO；每个代码步骤含实际代码。

**3. 类型一致性：** `generate_sql` 参数 `user_prompt`（保留旧名，兼容既有 keyword 调用）→ Task 2/3 一致；`downloadCsv/downloadJson(result, dbName)` Task 5/6/7 一致；`ChatMessage.role` 为 `"user"|"assistant"` 前后端一致。

**4. 规格缺口补充：** §6「切换数据库清空对话」已在 Task 7 Step 1 通过 `key={selectedDatabase}` 落实（见上方第 3 点代码）。
