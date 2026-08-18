# 一键导出功能设计文档

> 覆盖范围：手动查询（EXEC & EXPORT）与自然语言对话（对话式导出）两条导出路径
> 最后更新：2026-08-18（已实施，含验证结果）

## 一、需求背景

现有导出能力：手动查询结果可导出 CSV/JSON（RESULTS 区两个按钮，>10000 行弹确认）。
目标：
1. 手动标签：把「执行查询」与「导出结果」合并为一次点击（EXEC & EXPORT）。
2. 自然语言标签：查询出结果后 AI 主动询问是否导出，并支持用自然语言触发导出。

## 二、MANUAL SQL：EXEC & EXPORT

### 交互
- QUERY EDITOR 卡片右上角、EXECUTE 旁新增 `Dropdown.Button`「EXEC & EXPORT」。
- 主按钮点击 = 执行 SQL + 导出 CSV；下拉菜单可选 CSV / JSON。
- 仅 MANUAL SQL 标签显示；NATURAL LANGUAGE 走对话式导出（见第三节）。

### 设计决策与理由
| 决策 | 理由 |
|------|------|
| 主按钮默认 CSV、下拉二选一 | 与 RESULTS 区既有 CSV/JSON 两个导出按钮能力对齐；下拉不占横向空间 |
| 复用同一导出逻辑（`exportResult`） | 空结果拦截、>10000 行 Modal.confirm、成功提示三处行为一致，避免三份重复 |
| 只改前端、不动后端 | 导出为纯前端 Blob 下载，无需接口变更 |

## 三、NATURAL LANGUAGE：对话式导出

### 交互
- 查询出结果后，结果气泡内自动追加：`需要将这次查询结果导出为 CSV 或 JSON 文件吗？` + [CSV][JSON]。
- 用户可回复自然语言（如「导出CSV」「下载成JSON」）触发导出最近一条结果。

### 设计决策与理由（核心思路）
| 决策 | 理由 |
|------|------|
| 主动询问用前端固定气泡 + 快捷按钮 | 模型不感知执行结果（`/query/natural` 回复先于执行），由模型问会出现在执行前且不可控；前端气泡时序准确、确定性高、零后端改动 |
| NL 触发用前端关键词检测 | 零后端改动、行为可预测；导出领域关键词（导出/export/下载/download）歧义低 |
| 气泡按钮即导出入口，不做常驻 EXPORT 下拉 | 早期「每条结果常驻下拉」与气泡按钮功能重叠，YAGNI 收敛为一个入口 |
| 导出目标 = 最近一条带结果的消息；无结果则提示 | 符合「这次查询结果」的直觉；多轮对话下每轮结果都有气泡按钮，历史结果也可随时导出 |
| 大结果集确认框沿用 | 与手动标签行为完全一致，避免误下载大文件 |

## 四、实现（最终代码结构）

| 文件 | 改动 |
|------|------|
| `frontend/src/utils/export.tsx` | 共享 `exportResult(result, format, dbName)`：空结果拦截 + >10000 行 Modal.confirm + downloadCsv/downloadJson + 成功提示；新增 `detectExportIntent(prompt)`：正则匹配导出意图，识别 json、默认 csv |
| `frontend/src/pages/Home.tsx` | 内联 exportResult 移到 utils 共享；QUERY EDITOR extra 新增 EXEC & EXPORT 的 `Dropdown.Button`（EXECUTE + 下拉二选一）；清理未用 import |
| `frontend/src/components/ChatAssistant.tsx` | 结果气泡内追加主动询问 + CSV/JSON 按钮；handleSend 前置 `detectExportIntent`：命中则导出最近结果并追加确认气泡（无结果则提示「还没有可导出的查询结果。」），跳过 NL 接口；未命中走原查询流程 |

后端零改动。

## 五、验证结果

| 检查 | 结果 |
|------|------|
| `tsc --noEmit` | ✅ 通过 |
| `vite build` | ✅ 通过（3067 modules） |
| eslint / vitest | ⚠️ 仓库既有缺失（无 eslint 配置、无测试文件），与本次改动无关 |

手动验证要点：
- 手动：输入 SQL → EXEC & EXPORT → 下载 CSV/JSON、>10000 行弹确认。
- NL：查询 → 气泡询问 → 按钮导出 / 输入「导出CSV」自动导出 + 确认气泡。

## 六、注意事项

- 请求 404 时检查 `frontend/.env` 的 `VITE_API_BASE_URL`，应去掉 `/api/v1` 后缀（代码内请求路径已含 `/api/v1`）。
- 改 `.env` 需重启前端 dev server；纯代码改动 Vite HMR 自动生效，必要时浏览器 F5。
