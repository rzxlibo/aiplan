/** CSV/JSON export helpers (pure frontend). */

import { Modal, message } from "antd";
import { ExclamationCircleOutlined } from "@ant-design/icons";
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

/** 共享导出：空结果拦截 + 大结果集确认 + 触发下载 + 成功提示。manual / NL 共用。 */
export function exportResult(
  result: QueryResult,
  format: "csv" | "json",
  dbName: string
): void {
  if (result.rows.length === 0) {
    message.warning("No data to export");
    return;
  }
  const doExport = () => {
    if (format === "csv") {
      downloadCsv(result, dbName);
      message.success(`Exported ${result.rowCount} rows to CSV`);
    } else {
      downloadJson(result, dbName);
      message.success(`Exported ${result.rowCount} rows to JSON`);
    }
  };
  if (result.rows.length > 10000) {
    Modal.confirm({
      title: "Large Dataset Warning",
      icon: <ExclamationCircleOutlined />,
      content: `You are about to export ${result.rowCount.toLocaleString()} rows. This may take a while and consume memory. Continue?`,
      onOk: doExport,
    });
  } else {
    doExport();
  }
}

const EXPORT_INTENT_RE = /导出|export|下载|download/i;

/** 检测自然语言是否为导出意图，并识别目标格式（默认 csv）。 */
export function detectExportIntent(prompt: string): {
  isExport: boolean;
  format: "csv" | "json";
} {
  if (!EXPORT_INTENT_RE.test(prompt)) {
    return { isExport: false, format: "csv" };
  }
  const format: "csv" | "json" = /json/i.test(prompt) ? "json" : "csv";
  return { isExport: true, format };
}
