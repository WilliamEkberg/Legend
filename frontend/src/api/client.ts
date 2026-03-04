// Doc: Natural_Language_Code/opencode_runner/info_opencode_runner.md
// Doc: Natural_Language_Code/Frontend/info_frontend.md
// Doc: Natural_Language_Code/research_agent/info_map_editor.md
// Doc: Natural_Language_Code/ticket_generation/info_ticket_generation.md

import { save } from "@tauri-apps/plugin-dialog";
import { writeTextFile } from "@tauri-apps/plugin-fs";
import type {
  MapData,
  ChangeRecordsResponse,
  TicketGenerateResponse,
  SavedTicket,
  MapVersion,
  VersionDecision,
  VersionComparison,
  ValidationSummary,
  ValidationRun,
  DecisionValidation,
  ChatEvent,
  ChatMode,
} from "../data/types";

export interface RunRequest {
  api_key: string;
  provider: string;
  model?: string;
}

export interface RunResponse {
  success: boolean;
  output: string;
  error: string;
}

const CREDIT_KEYWORDS = [
  "insufficient",
  "quota",
  "billing",
  "credits",
  "budget",
  "exceeded",
  "payment",
  "balance",
  "api credits",
  "authentication failed",
  "invalid api key",
  "expired",
];

export function isCreditError(message: string): boolean {
  const lower = message.toLowerCase();
  return CREDIT_KEYWORDS.some((kw) => lower.includes(kw));
}

export class CreditError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CreditError";
  }
}

export async function runOpenCode(req: RunRequest): Promise<RunResponse> {
  const res = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  }

  return res.json();
}

export async function updateDecision(
  id: number,
  updates: { text?: string; category?: string },
): Promise<void> {
  const res = await fetch(`/api/decisions/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export async function createDecision(body: {
  text: string;
  category: string;
  module_id?: number;
  component_id?: number;
}): Promise<{ id: number }> {
  const res = await fetch("/api/decisions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function deleteDecision(id: number): Promise<void> {
  const res = await fetch(`/api/decisions/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export async function fetchChangeRecords(): Promise<ChangeRecordsResponse> {
  const res = await fetch("/api/change-records");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function generateTickets(
  apiKey: string,
  model?: string,
): Promise<TicketGenerateResponse> {
  const res = await fetch("/api/tickets/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey, model }),
  });
  if (!res.ok) {
    if (res.status === 401 || res.status === 402) {
      const detail = await res.json().catch(() => null);
      throw new CreditError(
        detail?.detail ??
          "API credit issue. Your API key may be invalid or out of credits.",
      );
    }
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
}

export async function fetchTickets(): Promise<{ tickets: SavedTicket[] }> {
  const res = await fetch("/api/tickets");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function deleteModule(id: number): Promise<void> {
  const res = await fetch(`/api/modules/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export async function deleteComponent(id: number): Promise<void> {
  const res = await fetch(`/api/components/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export async function createModule(body: {
  name: string;
  classification?: string;
  type?: string;
  technology?: string;
}): Promise<{ id: number }> {
  const res = await fetch("/api/modules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function createComponent(body: {
  module_id: number;
  name: string;
  purpose?: string;
}): Promise<{ id: number }> {
  const res = await fetch("/api/components", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function createModuleEdge(body: {
  source_id: number;
  target_id: number;
  edge_type: string;
  label?: string;
}): Promise<{ ok: boolean }> {
  const res = await fetch("/api/module-edges", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function createComponentEdge(body: {
  source_id: number;
  target_id: number;
  edge_type: string;
  label?: string;
}): Promise<{ ok: boolean }> {
  const res = await fetch("/api/component-edges", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchMap(): Promise<MapData> {
  const res = await fetch("/api/map");

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  }

  return res.json();
}

export async function exportMapAsFile(): Promise<void> {
  const mapData = await fetchMap();

  const exportPayload = {
    version: 1,
    exported_at: new Date().toISOString(),
    map: mapData,
  };

  const content = JSON.stringify(exportPayload, null, 2);

  const timestamp = new Date()
    .toISOString()
    .replace(/[:.]/g, "-")
    .slice(0, 19);

  const filePath = await save({
    defaultPath: `legend-map-${timestamp}.json`,
    filters: [{ name: "JSON", extensions: ["json"] }],
  });
  if (filePath) {
    await writeTextFile(filePath, content);
  }
}

export async function importMap(
  data: Record<string, unknown>,
): Promise<{ ok: boolean; summary: Record<string, number> }> {
  const res = await fetch("/api/map/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`HTTP ${res.status}: ${detail}`);
  }
  return res.json();
}

// ── Streaming run ──

export interface StreamRunRequest {
  api_key: string;
  provider: string;
  model?: string;
  step: string; // "part1" | "part2" | "part3" | "revalidation"
  repo_path?: string;
}

export interface StreamEvent {
  type: "stdout" | "stderr" | "error" | "done";
  text?: string;
  success?: boolean;
}

export async function runOpenCodeStream(
  req: StreamRunRequest,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/run/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal,
  });

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  }

  const reader = res.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          const event: StreamEvent = JSON.parse(line.slice(6));
          onEvent(event);
        } catch {
          // skip malformed SSE lines
        }
      }
    }
  }
}

// ── Map Versions ──

export async function fetchVersions(): Promise<{ versions: MapVersion[] }> {
  const res = await fetch("/api/versions");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchVersion(
  id: number,
): Promise<{ version: MapVersion; decisions: VersionDecision[] }> {
  const res = await fetch(`/api/versions/${id}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function createManualVersion(): Promise<{
  id: number;
  version_number: number;
}> {
  const res = await fetch("/api/versions", { method: "POST" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function compareVersions(
  aId: number,
  bId: number,
): Promise<VersionComparison> {
  const res = await fetch(`/api/versions/${aId}/compare/${bId}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ── Validation Runs ──

export async function fetchValidationRuns(): Promise<{
  runs: ValidationRun[];
}> {
  const res = await fetch("/api/validation-runs");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchValidationRun(
  id: number,
): Promise<{ run: ValidationRun; validations: DecisionValidation[] }> {
  const res = await fetch(`/api/validation-runs/${id}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchValidationSummary(): Promise<ValidationSummary> {
  const res = await fetch("/api/validation/summary");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ── Chat ──

export interface ChatRequest {
  message: string;
  mode: ChatMode;
  session_id?: string;
  api_key: string;
  provider: string;
  model?: string;
}

export async function sendChatMessage(
  req: ChatRequest,
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal,
  });

  if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);

  const reader = res.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          const event: ChatEvent = JSON.parse(line.slice(6));
          onEvent(event);
        } catch {
          // skip malformed SSE lines
        }
      }
    }
  }
}

export async function confirmChatChanges(body: {
  session_id: string;
  change_ids: string[];
}): Promise<{
  results: Array<{
    change_id: string;
    success: boolean;
    result?: Record<string, unknown>;
    error?: string;
  }>;
}> {
  const res = await fetch("/api/chat/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function clearChatSession(sessionId: string): Promise<void> {
  await fetch(`/api/chat/session/${sessionId}`, { method: "DELETE" });
}
