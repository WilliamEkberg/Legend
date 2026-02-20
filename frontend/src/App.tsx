// Doc: Natural_Language_Code/opencode_runner/info_opencode_runner.md
// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { useState, useRef, useEffect } from "react";
import { Link } from "react-router-dom";
import { runOpenCodeStream, type StreamEvent } from "./api/client";
import "./App.css";

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
  const [pipelineStep, setPipelineStep] = useState(0); // 0=idle, 1-3=current step
  const [pipelineCompleted, setPipelineCompleted] = useState<number[]>([]); // completed step indices
  const outputRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  function persist(key: string, value: string) {
    try { localStorage.setItem(key, value); } catch { /* ignore */ }
  }

  // Auto-scroll output to bottom
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
        // Run all 3 steps sequentially
        for (let i = 0; i < PIPELINE_STEPS.length; i++) {
          if (controller.signal.aborted) break;

          const { step: stepName, label } = PIPELINE_STEPS[i];
          setPipelineStep(i + 1);

          // Add separator between steps
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
        // Single step (existing behavior)
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
    <div className="terminal">
      <div className="terminal-body">
        <div className="logo">
          <pre>{ASCII_LOGO}</pre>
        </div>
        <div className="version">v1.0.0 :: unrestricted mode</div>

        <div className="steps-box">
          <p className="steps-intro">
            Legend is a workspace for spec-driven development. It uses AI to analyze your codebase and generate an interactive architecture map with editable decisions and exportable tickets. When your code evolves, re-validate to ingest code changes and check which decisions still hold — without rebuilding the map from scratch.
          </p>
          <div className="steps-list">
            <div className="step"><span className="step-num">1</span> Set API key &amp; repo path</div>
            <div className="step"><span className="step-num">2</span> Run Parts 1–3 to build the map</div>
            <div className="step"><span className="step-num">3</span> View, edit &amp; generate tickets</div>
            <div className="step"><span className="step-num">4</span> (optional) Re-validate to ingest new code changes</div>
          </div>
          <div className="steps-warning">
            Re-running Parts 1–3 resets the map. Use Re-validate instead to ingest changes and update decisions without losing your edits.
          </div>
        </div>

        <div className="field">
          <div className="field-label">
            <span className="prompt">&gt;</span> api_key
          </div>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => { setApiKey(e.target.value); persist("legend:apiKey", e.target.value); }}
            placeholder="sk-..."
            spellCheck={false}
          />
        </div>

        <div className="field">
          <div className="field-label">
            <span className="prompt">&gt;</span> repo_path
          </div>
          <input
            type="text"
            value={repoPath}
            onChange={(e) => { setRepoPath(e.target.value); persist("legend:repoPath", e.target.value); }}
            placeholder="/path/to/repository"
            spellCheck={false}
          />
        </div>

        <div className="field">
          <div className="field-label">
            <span className="prompt">&gt;</span> provider
          </div>
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
          >
            {PROVIDERS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </div>

        <div className="field">
          <div className="field-label">
            <span className="prompt">&gt;</span> model
          </div>
          <select
            value={model}
            onChange={(e) => { setModel(e.target.value); persist("legend:model", e.target.value); }}
          >
            {(PROVIDER_MODELS[provider] ?? []).map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </div>

        <div className="field">
          <div className="field-label">
            <span className="prompt">&gt;</span> pipeline step
          </div>
          <select
            value={step}
            onChange={(e) => { setStep(e.target.value); persist("legend:step", e.target.value); }}
          >
            {STEPS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>

        <div className="run-row">
          <button
            className={`run-btn${loading ? " loading" : ""}`}
            onClick={handleRun}
            disabled={loading}
          >
            {loading
              ? pipelineStep > 0
                ? `executing... (step ${pipelineStep}/3)`
                : "executing..."
              : step === "full"
                ? "$ run full pipeline"
                : `$ run ${step}`}
          </button>
          {loading && (
            <button className="cancel-btn" onClick={handleCancel}>
              cancel
            </button>
          )}
        </div>

        {error && lines.length === 0 && (
          <div className="inline-error">{error}</div>
        )}

        {step === "full" && (pipelineStep > 0 || pipelineCompleted.length > 0) && (
          <div className="pipeline-progress">
            {PIPELINE_STEPS.map((ps, i) => {
              const isCompleted = pipelineCompleted.includes(i);
              const isActive = pipelineStep === i + 1;
              const isPending = !isCompleted && !isActive;
              return (
                <div key={ps.step} className={`pipeline-segment${isActive ? " active" : ""}${isCompleted ? " completed" : ""}${isPending ? " pending" : ""}`}>
                  <span className="pipeline-dot" />
                  <span className="pipeline-label">Part {i + 1}</span>
                </div>
              );
            })}
          </div>
        )}

        {lines.length > 0 && (
          <div className={`output-block ${success === true ? "success" : success === false ? "error" : "running"}`}>
            <div className="output-header">
              <span className="status-dot" />
              {success === null
                ? pipelineStep > 0
                  ? `running step ${pipelineStep}/3 — ${PIPELINE_STEPS[pipelineStep - 1].label}`
                  : "running..."
                : success
                  ? step === "full" ? "exit 0 — pipeline complete" : "exit 0 — success"
                  : step === "full" && pipelineStep === 0 ? `exit 1 — failed at step ${pipelineCompleted.length + 1}/3` : "exit 1 — failed"}
            </div>
            <div className="output-content" ref={outputRef}>
              {lines.map((line, i) => (
                <pre
                  key={i}
                  className={
                    line.type === "stderr" || line.type === "error"
                      ? "line-error"
                      : "line-out"
                  }
                >
                  {line.text}
                </pre>
              ))}
            </div>
          </div>
        )}

        <div style={{ textAlign: "center", marginTop: 24 }}>
          <Link to="/map" className="nav-link">
            View Architecture Map &rarr;
          </Link>
        </div>
      </div>
    </div>
  );
}

export default App;
