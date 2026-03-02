// Doc: Natural_Language_Code/opencode_runner/info_opencode_runner.md
// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { useState, useRef, useEffect } from "react";
import { Link } from "react-router-dom";
import { runOpenCodeStream, type StreamEvent } from "./api/client";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const ASCII_LOGO = `
 ██▓    ▓█████   ▄████ ▓█████  ███▄    █ ▓█████▄
▓██▒    ▓█   ▀  ██▒ ▀█▒▓█   ▀  ██ ▀█   █ ▒██▀ ██▌
▒██░    ▒███   ▒██░▄▄▄░▒███   ▓██  ▀█ ██▒░██   █▌
▒██░    ▒▓█  ▄ ░▓█  ██▓▒▓█  ▄ ▓██▒  ▐▌██▒░▓█▄   ▌
░██████▒░▒████▒░▒▓███▀▒░▒████▒▒██░   ▓██░░▒████▓
░ ▒░▓  ░░░ ▒░ ░ ░▒   ▒ ░░ ▒░ ░░ ▒░   ▒ ▒  ▒▒▓  ▒
░ ░ ▒  ░ ░ ░  ░  ░   ░  ░ ░  ░░ ░░   ░ ▒░ ░ ▒  ▒
  ░ ░      ░   ░ ░   ░    ░      ░   ░ ░  ░ ░  ░
    ░  ░   ░  ░      ░    ░  ░         ░    ░
                                           ░      `.trimStart();

const PROVIDERS = [
  { value: "anthropic", label: "anthropic" },
  { value: "openai", label: "openai" },
  { value: "google", label: "google" },
  { value: "groq", label: "groq" },
];

const PROVIDER_MODELS: Record<string, { value: string; label: string }[]> = {
  anthropic: [
    { value: "anthropic/claude-sonnet-4-20250514", label: "Claude Sonnet 4" },
    { value: "anthropic/claude-opus-4-20250514", label: "Claude Opus 4" },
    { value: "anthropic/claude-haiku-4-5-20251001", label: "Claude Haiku 4.5" },
  ],
  openai: [
    { value: "openai/gpt-4o", label: "GPT-4o" },
    { value: "openai/gpt-4o-mini", label: "GPT-4o Mini" },
    { value: "openai/o3-mini", label: "o3-mini" },
  ],
  google: [
    { value: "gemini/gemini-2.5-pro", label: "Gemini 2.5 Pro" },
    { value: "gemini/gemini-2.0-flash", label: "Gemini 2.0 Flash" },
  ],
  groq: [
    { value: "groq/llama-3.3-70b-versatile", label: "Llama 3.3 70B" },
    { value: "groq/llama-3.1-8b-instant", label: "Llama 3.1 8B Instant" },
    { value: "groq/mixtral-8x7b-32768", label: "Mixtral 8x7B" },
  ],
};

const STEPS = [
  { value: "full", label: "Full Pipeline (Parts 1–3)" },
  { value: "part1", label: "Part 1 — Module Classification" },
  { value: "part2", label: "Part 2 — Component Discovery" },
  { value: "part3", label: "Part 3 — Map Descriptions" },
  { value: "revalidation", label: "Re-validate — Ingest Code Changes" },
];

const PIPELINE_STEPS = [
  { step: "part1", label: "Module Classification" },
  { step: "part2", label: "Component Discovery" },
  { step: "part3", label: "Map Descriptions" },
];

function loadStored(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

interface OutputLine {
  type: "stdout" | "stderr" | "error";
  text: string;
}

function App() {
  const [apiKey, setApiKey] = useState(() => loadStored("legend:apiKey", ""));
  const [provider, setProvider] = useState(() => loadStored("legend:provider", "anthropic"));
  const [model, setModel] = useState(() => {
    const stored = loadStored("legend:model", "");
    if (stored) return stored;
    const prov = loadStored("legend:provider", "anthropic");
    return PROVIDER_MODELS[prov]?.[0]?.value ?? "";
  });
  const [repoPath, setRepoPath] = useState(() => loadStored("legend:repoPath", ""));
  const [step, setStep] = useState(() => loadStored("legend:step", "full"));
  const [loading, setLoading] = useState(false);
  const [lines, setLines] = useState<OutputLine[]>([]);
  const [success, setSuccess] = useState<boolean | null>(null);
  const [error, setError] = useState("");
  const [pipelineStep, setPipelineStep] = useState(0);
  const [pipelineCompleted, setPipelineCompleted] = useState<number[]>([]);
  const outputRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  function persist(key: string, value: string) {
    try { localStorage.setItem(key, value); } catch { /* ignore */ }
  }

  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [lines]);

  async function runSingleStep(
    stepName: string,
    controller: AbortController,
  ): Promise<boolean> {
    return new Promise((resolve) => {
      let stepSuccess = false;
      runOpenCodeStream(
        {
          api_key: apiKey,
          provider,
          model: model || undefined,
          step: stepName,
          repo_path: repoPath || undefined,
        },
        (event: StreamEvent) => {
          if (event.type === "done") {
            stepSuccess = event.success ?? false;
          } else if (event.text) {
            const lineType = event.type as OutputLine["type"];
            setLines((prev) => [...prev, { type: lineType, text: event.text! }]);
          }
        },
        controller.signal,
      ).then(() => resolve(stepSuccess))
        .catch((e) => {
          if ((e as Error).name !== "AbortError") {
            setError(e instanceof Error ? e.message : "Unknown error");
          }
          resolve(false);
        });
    });
  }

  async function handleRun() {
    if (!apiKey.trim()) {
      setError("ERROR: api_key is required");
      return;
    }

    setLoading(true);
    setLines([]);
    setSuccess(null);
    setError("");
    setPipelineStep(0);
    setPipelineCompleted([]);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      if (step === "full") {
        for (let i = 0; i < PIPELINE_STEPS.length; i++) {
          if (controller.signal.aborted) break;

          const { step: stepName, label } = PIPELINE_STEPS[i];
          setPipelineStep(i + 1);

          if (i > 0) {
            setLines((prev) => [
              ...prev,
              { type: "stdout", text: "" },
              { type: "stdout", text: `── Step ${i + 1}/3: ${label} ──` },
              { type: "stdout", text: "" },
            ]);
          } else {
            setLines((prev) => [
              ...prev,
              { type: "stdout", text: `── Step 1/3: ${label} ──` },
              { type: "stdout", text: "" },
            ]);
          }

          const ok = await runSingleStep(stepName, controller);
          if (!ok) {
            setSuccess(false);
            setPipelineStep(0);
            return;
          }
          setPipelineCompleted((prev) => [...prev, i]);
        }
        setSuccess(true);
        setPipelineStep(0);
      } else {
        await runOpenCodeStream(
          {
            api_key: apiKey,
            provider,
            model: model || undefined,
            step,
            repo_path: repoPath || undefined,
          },
          (event: StreamEvent) => {
            if (event.type === "done") {
              setSuccess(event.success ?? false);
            } else if (event.text) {
              const lineType = event.type as OutputLine["type"];
              setLines((prev) => [...prev, { type: lineType, text: event.text! }]);
            }
          },
          controller.signal,
        );
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setError(e instanceof Error ? e.message : "Unknown error");
      }
    } finally {
      setLoading(false);
      setPipelineStep(0);
      abortRef.current = null;
    }
  }

  function handleCancel() {
    abortRef.current?.abort();
  }

  return (
    <div className="min-h-screen bg-background overflow-y-auto">
      <div className="w-full max-w-2xl mx-auto space-y-6 p-4 py-8">
        {/* Theme toggle */}
        <div className="flex justify-end">
          <ThemeToggle />
        </div>

        {/* ASCII Logo */}
        <div className="text-center">
          <pre className="inline-block text-primary text-xs leading-tight font-mono select-none">{ASCII_LOGO}</pre>
        </div>
        <p className="text-center text-muted-foreground text-xs font-mono tracking-widest">
          v1.0.0 :: unrestricted mode
        </p>

        {/* How it works */}
        <div className="bg-card border border-border rounded-lg p-4 space-y-3">
          <p className="text-sm text-muted-foreground leading-relaxed">
            Legend is a workspace for spec-driven development. It uses AI to analyze your codebase and generate an interactive architecture map with editable decisions and exportable tickets. When your code evolves, re-validate to ingest code changes and check which decisions still hold — without rebuilding the map from scratch.
          </p>
          <div className="space-y-1.5">
            <div className="flex items-center gap-2 text-sm">
              <span className="flex items-center justify-center w-5 h-5 rounded-full bg-primary text-primary-foreground text-xs font-bold shrink-0">1</span>
              <span className="text-foreground">Set API key &amp; repo path</span>
            </div>
            <div className="flex items-center gap-2 text-sm">
              <span className="flex items-center justify-center w-5 h-5 rounded-full bg-primary text-primary-foreground text-xs font-bold shrink-0">2</span>
              <span className="text-foreground">Run Parts 1–3 to build the map</span>
            </div>
            <div className="flex items-center gap-2 text-sm">
              <span className="flex items-center justify-center w-5 h-5 rounded-full bg-primary text-primary-foreground text-xs font-bold shrink-0">3</span>
              <span className="text-foreground">View, edit &amp; generate tickets</span>
            </div>
            <div className="flex items-center gap-2 text-sm">
              <span className="flex items-center justify-center w-5 h-5 rounded-full bg-muted text-muted-foreground text-xs font-bold shrink-0">4</span>
              <span className="text-muted-foreground">(optional) Re-validate to ingest new code changes</span>
            </div>
          </div>
          <p className="text-xs text-destructive/80 bg-destructive/10 rounded px-2 py-1.5">
            Re-running Parts 1–3 resets the map. Use Re-validate instead to ingest changes and update decisions without losing your edits.
          </p>
        </div>

        {/* Form fields */}
        <div className="space-y-4">
          <div className="space-y-1.5">
            <label className="text-sm font-medium text-foreground font-mono">
              <span className="text-primary font-bold">&gt;</span> api_key
            </label>
            <Input
              type="password"
              value={apiKey}
              onChange={(e) => { setApiKey(e.target.value); persist("legend:apiKey", e.target.value); }}
              placeholder="sk-..."
              spellCheck={false}
              className="font-mono"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-sm font-medium text-foreground font-mono">
              <span className="text-primary font-bold">&gt;</span> repo_path
            </label>
            <Input
              type="text"
              value={repoPath}
              onChange={(e) => { setRepoPath(e.target.value); persist("legend:repoPath", e.target.value); }}
              placeholder="/path/to/repository"
              spellCheck={false}
              className="font-mono"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-sm font-medium text-foreground font-mono">
              <span className="text-primary font-bold">&gt;</span> provider
            </label>
            <select
              value={provider}
              onChange={(e) => {
                const v = e.target.value;
                setProvider(v);
                persist("legend:provider", v);
                const def = PROVIDER_MODELS[v]?.[0]?.value ?? "";
                setModel(def);
                persist("legend:model", def);
              }}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              {PROVIDERS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1.5">
            <label className="text-sm font-medium text-foreground font-mono">
              <span className="text-primary font-bold">&gt;</span> model
            </label>
            <select
              value={model}
              onChange={(e) => { setModel(e.target.value); persist("legend:model", e.target.value); }}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              {(PROVIDER_MODELS[provider] ?? []).map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1.5">
            <label className="text-sm font-medium text-foreground font-mono">
              <span className="text-primary font-bold">&gt;</span> pipeline step
            </label>
            <select
              value={step}
              onChange={(e) => { setStep(e.target.value); persist("legend:step", e.target.value); }}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              {STEPS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Run / Cancel */}
        <div className="flex items-center gap-3">
          <Button
            onClick={handleRun}
            disabled={loading}
            className="font-mono"
          >
            {loading
              ? pipelineStep > 0
                ? `executing... (step ${pipelineStep}/3)`
                : "executing..."
              : step === "full"
                ? "$ run full pipeline"
                : `$ run ${step}`}
          </Button>
          {loading && (
            <Button variant="destructive" onClick={handleCancel} className="font-mono">
              cancel
            </Button>
          )}
        </div>

        {/* Inline error */}
        {error && lines.length === 0 && (
          <p className="text-sm text-destructive font-mono">{error}</p>
        )}

        {/* Pipeline progress */}
        {step === "full" && (pipelineStep > 0 || pipelineCompleted.length > 0) && (
          <div className="flex items-center gap-4">
            {PIPELINE_STEPS.map((ps, i) => {
              const isCompleted = pipelineCompleted.includes(i);
              const isActive = pipelineStep === i + 1;
              return (
                <div key={ps.step} className="flex items-center gap-1.5">
                  <span
                    className={`w-2.5 h-2.5 rounded-full transition-colors ${
                      isCompleted
                        ? "bg-primary"
                        : isActive
                          ? "bg-primary animate-pulse"
                          : "bg-muted-foreground/30"
                    }`}
                  />
                  <span
                    className={`text-xs font-mono ${
                      isCompleted
                        ? "text-primary"
                        : isActive
                          ? "text-primary"
                          : "text-muted-foreground"
                    }`}
                  >
                    Part {i + 1}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        {/* Output */}
        {lines.length > 0 && (
          <div
            className={`rounded-lg border overflow-hidden ${
              success === true
                ? "border-primary/50"
                : success === false
                  ? "border-destructive/50"
                  : "border-border"
            }`}
          >
            <div
              className={`flex items-center gap-2 px-3 py-2 text-xs font-mono ${
                success === true
                  ? "bg-primary/10 text-primary"
                  : success === false
                    ? "bg-destructive/10 text-destructive"
                    : "bg-card text-muted-foreground"
              }`}
            >
              <span
                className={`w-2 h-2 rounded-full ${
                  success === true
                    ? "bg-primary"
                    : success === false
                      ? "bg-destructive"
                      : "bg-muted-foreground animate-pulse"
                }`}
              />
              {success === null
                ? pipelineStep > 0
                  ? `running step ${pipelineStep}/3 — ${PIPELINE_STEPS[pipelineStep - 1].label}`
                  : "running..."
                : success
                  ? step === "full" ? "exit 0 — pipeline complete" : "exit 0 — success"
                  : step === "full" && pipelineStep === 0 ? `exit 1 — failed at step ${pipelineCompleted.length + 1}/3` : "exit 1 — failed"}
            </div>
            <div
              ref={outputRef}
              className="max-h-80 overflow-y-auto p-3 bg-card font-mono text-xs leading-relaxed"
            >
              {lines.map((line, i) => (
                <pre
                  key={i}
                  className={`whitespace-pre-wrap break-words ${
                    line.type === "stderr" || line.type === "error"
                      ? "text-destructive"
                      : "text-foreground"
                  }`}
                >
                  {line.text}
                </pre>
              ))}
            </div>
          </div>
        )}

        {/* Map link */}
        <div className="text-center">
          <Link
            to="/map"
            className="text-sm text-primary hover:text-primary/80 font-mono transition-colors"
          >
            View Architecture Map &rarr;
          </Link>
        </div>
      </div>
    </div>
  );
}

export default App;
