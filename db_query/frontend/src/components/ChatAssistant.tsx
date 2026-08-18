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
import { detectExportIntent, exportResult } from "../utils/export";

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

/** 最近一条带结果且非空的消息结果。 */
function findLatestResult(messages: ChatMessage[]): QueryResult | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (!m) continue;
    if (m.role === "assistant" && m.result && m.result.rows.length > 0) {
      return m.result;
    }
  }
  return null;
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

    setInput("");

    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: prompt,
    };

    // 自然语言触发导出：命中导出意图则直接导出最近结果，不走 NL 接口
    const intent = detectExportIntent(prompt);
    if (intent.isExport) {
      setMessages((prev) => [...prev, userMsg]);
      const latest = findLatestResult(messages);
      if (!latest) {
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            content: "还没有可导出的查询结果。",
            status: "done",
          },
        ]);
      } else {
        exportResult(latest, intent.format, databaseName);
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            content: `已导出 ${intent.format.toUpperCase()}（${latest.rowCount} 行）`,
            status: "done",
          },
        ]);
      }
      scrollToBottom();
      return;
    }

    const assistantMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "",
      status: "generating",
    };
    setMessages((prev) => [...prev, userMsg, assistantMsg]);
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
                        需要将这次查询结果导出为 CSV 或 JSON 文件吗？
                      </Text>
                      <Button size="small" onClick={() => exportResult(m.result!, "csv", databaseName)}>
                        CSV
                      </Button>
                      <Button size="small" onClick={() => exportResult(m.result!, "json", databaseName)}>
                        JSON
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
