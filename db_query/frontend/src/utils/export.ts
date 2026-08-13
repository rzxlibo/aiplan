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
