# 对话式 AI 助手 + 导出触发 设计文档

> 日期：2026-08-13
> 状态：已批准（brainstorming 逐段确认）
> 关联文档：`docs/superpowers/specs/2026-08-13-db-query-architecture-design.md`（项目架构设计）

## 1. 背景与现状

数据库查询工具的「自然语言生成 SQL」目前是**一次性输入**：用户在 NATURAL LANGUAGE tab 输入一句话 → 后端生成 SQL → 自动切到 MANUAL tab → 用户再手动点 EXECUTE。全程无对话上下文，结果出来后也不询问导出。

导出功能（CSV / JSON）已存在，但只挂在 Home.tsx RESULTS 卡的固定按钮上，纯前端生成（RFC 4180，>10000 行弹窗确认）。

**目标**：把自然语言 Tab 升级为「对话式 AI 助手」，在对话里自动产出查询结果，并在结果后主动提供 CSV/JSON 导出。

## 2. 需求（已与用户逐项确认）

| 维度 | 决策 |
|------|------|
| 交互形态 | 对话式 AI 助手（多轮聊天界面，取代现有 NATURAL LANGUAGE tab） |
| 查询执行 | 助手自动执行：生成 SQL → 自动查询 → 结果表格显示在对话气泡里 |
| 对话记忆 | 完整多轮记忆：历史（含 assistant 的回复与 SQL）作为上下文用于生成新 SQL |
| 导出询问 | 前端固定提示条（结果出现后自动附带，不依赖大模型生成） |
| 助手回复 | 一句自然语言说明 + SQL 代码块 |
| 导出格式 | 仅 CSV/JSON（沿用现有实现，不新增格式） |
| 会话持久化 | 内存级，刷新即清，无后端存储改动 |

## 3. 目标架构与数据流

方案选型：**两步式**（后端只管生成，前端负责执行），复用现有 `/query` 端点与 sqlglot 校验，后端保持无状态。

```
用户输入
  → ChatAssistant 追加 user 气泡
  → POST /api/v1/dbs/{name}/query/natural
       body: { prompt, messages: [完整历史(最近20条)] }
  → 后端 nl2sql：system(元数据+规则) + 历史 + 当前 prompt → LLM
       → 解析 { reply, sql } → sqlglot 校验（仅 SELECT + LIMIT 1000）
  → 前端拿到 { reply, sql } → 自动 POST /api/v1/dbs/{name}/query { sql }
  → 结果表格渲染进该 assistant 气泡 + 固定导出提示条
  → 用户点 [导出CSV]/[导出JSON] → utils.downloadCsv/downloadJson(该条 result)
```

## 4. 后端改造

### 4.1 schemas.py

```python
class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]   # 对话历史角色
    content: str                          # 文本（assistant 历史消息 = 自然回复 + SQL 代码块）

class NaturalLanguageInput(BaseModel):
    prompt: str = Field(..., min_length=5, max_length=500)   # 当前输入（保留校验）
    messages: list[ChatMessage] = Field(default_factory=list)  # 历史上下文

class GeneratedSqlResponse(BaseModel):
    reply: str   # 自然语言说明（原 explanation 改名 reply）
    sql: str
```

### 4.2 nl2sql.py

- `generate_sql(messages: list[dict], prompt: str, metadata: dict, db_type)`：
  prompt 组装 = `system(元数据 + 规则) + 历史 user/assistant 消息 + 当前 user prompt`。
- 系统提示词要求输出格式：**一句自然语言说明 + 用 ```sql 代码块包裹的 SQL**。
- 响应解析：提取 ```sql ... ``` 块为 `sql`，其余为 `reply`；无代码块时整段兜底当 sql。
- SQL 校验：仍走 `validate_and_transform_sql`（仅 SELECT + 自动补 LIMIT 1000，按 db_type 选 dialect）。
- **健壮性修复（一并纳入）**：
  - 异常信息收敛：`except` 中对 `str(e)` 截断（约 500 字符）+ 剥离 HTML 特征，避免整段拦截页/HTML 抛给前端。
  - `AsyncOpenAI` 显式 `timeout=30`。
- 返回 `{"reply": ..., "sql": ...}`。

### 4.3 queries.py route（`/api/v1/dbs/{name}/query/natural`）

- 传入 `input_data.messages + input_data.prompt` 调用 `generate_sql`。
- 返回 `GeneratedSqlResponse(reply=..., sql=...)`。
- 错误仍映射 HTTP 500 + `detail`（detail 为已收敛的错误信息）。

## 5. 前端改造

### 5.1 共享导出工具 `frontend/src/utils/export.ts`

从 Home.tsx:158-222 抽出：

```ts
downloadCsv(result: QueryResult, dbName: string)   // RFC 4180 + Blob 下载
downloadJson(result: QueryResult, dbName: string)  // JSON 缩进导出
```

> 说明：为避免 Home 与 ChatAssistant 复制同一套约 60 行导出逻辑，抽取为共享 util。>10000 行的确认弹窗保留在组件层（先 Modal.confirm 再调 download）。

### 5.2 共享类型 `frontend/src/types/query.ts`

把 Home.tsx:35 内部定义的 `QueryResult` 接口抽到独立类型文件，Home 与 ChatAssistant 共用。

### 5.3 新组件 `ChatAssistant.tsx`（`frontend/src/components/`）

```
Props:  { databaseName: string }
State:  messages: ChatMessage[]  |  input  |  sending

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sql?: string;
  result?: QueryResult;
  error?: string;
  status?: "generating" | "executing" | "done" | "error";
}
```

- **提交流程**：追加 user 气泡 + 占位 assistant（`generating`）→ `POST /query/natural`（`{prompt, messages: 历史序列化}`）→ 得 `{reply, sql}` → 更新气泡（`executing`）→ 自动 `POST /query`（`{sql}`）→ 得 result 渲染（`done`）；任一步失败置 `error`。
- **渲染**：滚动消息列表（user 右对齐 / assistant 左对齐）；assistant 气泡 = 自然文本 + SQL 代码块（`<pre>` 深色底 + 复制按钮）+ 结果表格（AntD Table，50 条/页）+ **固定导出提示条**「需要导出为 CSV/JSON？[导出CSV][导出JSON]」（下载该条 result）+ 错误 Alert。
- 输入区：TextArea + 发送按钮（Cmd/Ctrl+Enter），顶部「清空对话」按钮。
- 发送历史给后端前只保留最近 20 条，防止 token 超限。

### 5.4 Home.tsx 集成

- NATURAL LANGUAGE tab children：`<NaturalLanguageInput/>` → `<ChatAssistant databaseName={selectedDatabase} />`。
- RESULTS 卡导出按钮改调 `utils/downloadCsv/downloadJson`。
- 删除 `NaturalLanguageInput.tsx`（被取代，不留死代码）；清理其专属 state（`generatingSql`/`nlError` 若不再被引用）。
- MANUAL SQL tab 保持不变。

## 6. 错误处理与边界

| 场景 | 行为 |
|------|------|
| 生成失败（LLM / 校验） | 气泡内 Alert 显示后端 detail（已截断清洗）；不打断对话，可继续或清空 |
| 执行失败（SQL 运行时错误） | 气泡显示错误，**仍保留已生成 SQL 代码块**，可复制到 MANUAL 微调 |
| 超时 | 后端 AsyncOpenAI timeout=30；前端 axios 超时后气泡友好提示 |
| 对话历史上限 | 只发最近 20 条给后端 |
| 空结果 | `rows.length===0` 时不显示导出提示条 |
| 自动执行安全性 | 沿用 /query 既有 sqlglot 校验（仅 SELECT + LIMIT 1000），无写入风险 |
| 切换数据库 | selectedDatabase 变化即清空对话（内存级防串库），用 `key={selectedDatabase}` 或组件内 effect |

## 7. 测试策略

### 7.1 后端 pytest

- `test_nl2sql.py` 更新：
  - mock 响应改为「自然回复 + ```sql 块」，断言 `reply`/`sql` 解析正确。
  - 多轮历史进入 prompt。
  - 校验仍生效：非 SELECT 拒绝、无 LIMIT 补 1000。
  - 异常截断：mock 抛 HTML 类错误，断言 `str(e)` 被截断/清洗。
  - 旧断言 `explanation` → `reply`。
- 跑 `uv run pytest` 全量确认不回归。

### 7.2 前端

- 项目前端单测本就少（CLAUDE.md 注明主要靠 `.rest` 手动测）。
- ChatAssistant 若项目有现成 vitest 组件测试模式则跟随补测；否则用手动验证清单。

### 7.3 fixtures/test.rest

- 更新 natural 端点用例：请求体加 `messages`，响应断言改 `reply`/`sql`。

## 8. 非目标 / 范围外

- 不新增导出格式（无 Excel/Markdown）。
- 不做会话持久化（内存级，刷新即清）。
- 不做 NL 命令直接触发导出（用户选定「前端固定提示」，排除 LLM/命令解析导出意图）。
- 不做助手闲聊等非查询对话能力（YAGNI，本次只保证查询 + 导出场景）。

## 9. 假设

- 聊天界面**取代**现有 NATURAL LANGUAGE tab（原地升级，不新增第三个 Tab）。
- 对话里每个查询结果独立携带导出提示条，各自导出自己的结果。
- 后端保持无状态：前端持有会话消息，每次全量发送历史。
- `ChatMessage` 的 assistant 历史内容按「自然回复 + ```sql 代码块」序列化，便于模型续接上下文。
- **导出仅由固定提示条触发**：用户在对话里说"导出"相关的话只作为模型上下文，前端不做命令解析、不据此触发导出（与 §8 非目标一致）。
