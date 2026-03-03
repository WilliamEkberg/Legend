// Doc: Natural_Language_Code/chat/info_chat.md

import { useState, useRef, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import ReactMarkdown from "react-markdown";
import type { ChatMessage, ChatMode, ChatEvent, ProposedChange } from "../../data/types";
import { sendChatMessage, confirmChatChanges, clearChatSession } from "../../api/client";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface ChatPanelProps {
  onClose: () => void;
  onNodeSelect?: (nodeType: "module" | "component", id: number) => void;
  onMapMutated?: () => void;
}

export function ChatPanel({ onClose, onNodeSelect, onMapMutated }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [mode, setMode] = useState<ChatMode>("ask");
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [pendingChanges, setPendingChanges] = useState<ProposedChange[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Read API config from localStorage (same keys as MapView)
  const apiKey = localStorage.getItem("legend:apiKey") || "";
  const provider = localStorage.getItem("legend:provider") || "anthropic";
  const model = localStorage.getItem("legend:model") || undefined;

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = "auto";
      ta.style.height = Math.min(ta.scrollHeight, 120) + "px";
    }
  }, [input]);

  const handleSend = useCallback(async () => {
    const trimmed = input.trim();
    if (!trimmed || isStreaming) return;
    if (!apiKey) {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "Please set your API key in the Launcher page first.",
          timestamp: new Date().toISOString(),
        },
      ]);
      return;
    }

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: trimmed,
      timestamp: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setIsStreaming(true);

    // Create a placeholder assistant message for streaming
    const assistantId = crypto.randomUUID();
    const assistantMessage: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      toolCalls: [],
      toolResults: [],
      proposedChanges: [],
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, assistantMessage]);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await sendChatMessage(
        {
          message: trimmed,
          mode,
          session_id: sessionId,
          api_key: apiKey,
          provider,
          model,
        },
        (event: ChatEvent) => {
          setMessages((prev) => {
            const updated = [...prev];
            const idx = updated.findIndex((m) => m.id === assistantId);
            if (idx === -1) return prev;
            const msg = { ...updated[idx] };

            switch (event.type) {
              case "text":
                msg.content += event.content || "";
                break;
              case "tool_call":
                msg.toolCalls = [
                  ...(msg.toolCalls || []),
                  { name: event.name!, arguments: event.arguments || {} },
                ];
                break;
              case "tool_result":
                msg.toolResults = [
                  ...(msg.toolResults || []),
                  { name: event.name!, result: event.result || "" },
                ];
                break;
              case "proposed_change":
                if (event.change) {
                  msg.proposedChanges = [...(msg.proposedChanges || []), event.change];
                  setPendingChanges((prev) => [...prev, event.change!]);
                }
                break;
              case "error":
                msg.content += `\n\n**Error:** ${event.text || "Unknown error"}`;
                break;
              case "done":
                if (event.session_id) {
                  setSessionId(event.session_id);
                }
                break;
            }

            updated[idx] = msg;
            return updated;
          });
        },
        controller.signal,
      );
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setMessages((prev) => {
          const updated = [...prev];
          const idx = updated.findIndex((m) => m.id === assistantId);
          if (idx !== -1) {
            updated[idx] = {
              ...updated[idx],
              content: updated[idx].content + `\n\n**Error:** ${(e as Error).message}`,
            };
          }
          return updated;
        });
      }
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }
  }, [input, isStreaming, apiKey, provider, model, mode, sessionId]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleStop = () => {
    abortRef.current?.abort();
  };

  const handleClearChat = async () => {
    if (sessionId) {
      await clearChatSession(sessionId);
    }
    setMessages([]);
    setSessionId(undefined);
    setPendingChanges([]);
  };

  const handleApplyChange = async (changeId: string) => {
    if (!sessionId) return;
    try {
      const response = await confirmChatChanges({
        session_id: sessionId,
        change_ids: [changeId],
      });
      const result = response.results?.[0];
      const succeeded = result?.success === true;
      const newStatus = succeeded ? "applied" : "rejected";
      setPendingChanges((prev) =>
        prev.map((c) => (c.id === changeId ? { ...c, status: newStatus } : c)),
      );
      // Update the proposed change status in the message that contains it
      setMessages((prev) =>
        prev.map((msg) => ({
          ...msg,
          proposedChanges: msg.proposedChanges?.map((c) =>
            c.id === changeId ? { ...c, status: newStatus } : c,
          ),
        })),
      );
      if (succeeded) {
        onMapMutated?.();
      }
    } catch (e) {
      console.error("Failed to apply change:", e);
    }
  };

  const handleRejectChange = (changeId: string) => {
    setPendingChanges((prev) =>
      prev.map((c) => (c.id === changeId ? { ...c, status: "rejected" } : c)),
    );
    setMessages((prev) =>
      prev.map((msg) => ({
        ...msg,
        proposedChanges: msg.proposedChanges?.map((c) =>
          c.id === changeId ? { ...c, status: "rejected" } : c,
        ),
      })),
    );
  };

  const handleApplyAll = async () => {
    const pendingIds = pendingChanges.filter((c) => c.status === "pending").map((c) => c.id);
    if (!sessionId || pendingIds.length === 0) return;
    try {
      const response = await confirmChatChanges({
        session_id: sessionId,
        change_ids: pendingIds,
      });
      const resultMap = new Map(response.results.map((r) => [r.change_id, r.success]));
      const updateStatus = (c: ProposedChange) => {
        const success = resultMap.get(c.id);
        if (success === undefined) return c;
        return { ...c, status: (success ? "applied" : "rejected") as ProposedChange["status"] };
      };
      setPendingChanges((prev) => prev.map(updateStatus));
      setMessages((prev) =>
        prev.map((msg) => ({
          ...msg,
          proposedChanges: msg.proposedChanges?.map(updateStatus),
        })),
      );
      if (response.results.some((r) => r.success)) {
        onMapMutated?.();
      }
    } catch (e) {
      console.error("Failed to apply changes:", e);
    }
  };

  const handleRejectAll = () => {
    const rejectAll = (c: ProposedChange) =>
      c.status === "pending" ? { ...c, status: "rejected" as const } : c;
    setPendingChanges((prev) => prev.map(rejectAll));
    setMessages((prev) =>
      prev.map((msg) => ({
        ...msg,
        proposedChanges: msg.proposedChanges?.map(rejectAll),
      })),
    );
  };

  const pendingCount = pendingChanges.filter((c) => c.status === "pending").length;

  // Parse node references like [Name](module:123) and make them clickable
  const renderNodeLink = (href: string | undefined, children: React.ReactNode) => {
    if (!href) return <a>{children}</a>;
    const match = href.match(/^(module|component):(\d+)$/);
    if (match) {
      const nodeType = match[1] as "module" | "component";
      const id = parseInt(match[2], 10);
      return (
        <button
          className="text-primary underline hover:text-primary/80 font-medium"
          onClick={() => onNodeSelect?.(nodeType, id)}
        >
          {children}
        </button>
      );
    }
    return <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary underline">{children}</a>;
  };

  return (
    <motion.div
      className="absolute top-0 right-0 h-full w-[420px] bg-card border-l border-border shadow-xl z-30 flex flex-col"
      initial={{ x: "100%" }}
      animate={{ x: 0 }}
      exit={{ x: "100%" }}
      transition={{ type: "spring", damping: 40, stiffness: 200 }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-foreground">Chat</h2>
          <div className="flex rounded-md border border-border overflow-hidden">
            <button
              className={cn(
                "px-2.5 py-1 text-xs font-medium transition-colors",
                mode === "ask"
                  ? "bg-primary text-primary-foreground"
                  : "bg-transparent text-muted-foreground hover:text-foreground",
              )}
              onClick={() => setMode("ask")}
            >
              Ask
            </button>
            <button
              className={cn(
                "px-2.5 py-1 text-xs font-medium transition-colors border-l border-border",
                mode === "edit"
                  ? "bg-primary text-primary-foreground"
                  : "bg-transparent text-muted-foreground hover:text-foreground",
              )}
              onClick={() => setMode("edit")}
            >
              Edit
            </button>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            className="text-xs h-7 px-2"
            onClick={handleClearChat}
            title="Clear chat"
          >
            Clear
          </Button>
          <Button variant="ghost" size="sm" className="text-lg px-2 h-7" onClick={onClose}>
            &times;
          </Button>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        {messages.length === 0 && (
          <div className="text-center text-muted-foreground text-sm mt-8">
            <p className="font-medium mb-1">Architecture Assistant</p>
            <p className="text-xs">
              {mode === "ask"
                ? "Ask questions about your architecture map."
                : "Ask questions or request changes to your architecture map."}
            </p>
          </div>
        )}

        {messages.map((msg) => (
          <div key={msg.id} className={cn("flex flex-col gap-1", msg.role === "user" ? "items-end" : "items-start")}>
            {msg.role === "user" ? (
              <div className="bg-primary text-primary-foreground rounded-2xl rounded-br-md px-3 py-2 max-w-[85%] text-sm">
                {msg.content}
              </div>
            ) : (
              <div className="max-w-[95%] space-y-2">
                {/* Tool call indicators */}
                {msg.toolCalls && msg.toolCalls.length > 0 && (
                  <div className="space-y-1">
                    {msg.toolCalls.map((tc, i) => (
                      <ToolCallBadge key={i} name={tc.name} />
                    ))}
                  </div>
                )}

                {/* Text content */}
                {msg.content && (
                  <div className="bg-muted rounded-2xl rounded-bl-md px-3 py-2 text-sm prose prose-sm dark:prose-invert max-w-none [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0.5 [&_pre]:my-1 [&_code]:text-xs [&_h1]:text-base [&_h2]:text-sm [&_h3]:text-sm">
                    <ReactMarkdown
                      components={{
                        a: ({ href, children }) => renderNodeLink(href, children),
                      }}
                    >
                      {msg.content}
                    </ReactMarkdown>
                  </div>
                )}

                {/* Proposed changes */}
                {msg.proposedChanges && msg.proposedChanges.length > 0 && (
                  <div className="space-y-2">
                    {msg.proposedChanges.map((change) => (
                      <ProposedChangeCard
                        key={change.id}
                        change={change}
                        onApply={() => handleApplyChange(change.id)}
                        onReject={() => handleRejectChange(change.id)}
                      />
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}

        {isStreaming && (
          <div className="flex items-center gap-2 text-muted-foreground text-xs">
            <div className="flex gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-bounce" style={{ animationDelay: "0ms" }} />
              <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-bounce" style={{ animationDelay: "150ms" }} />
              <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-bounce" style={{ animationDelay: "300ms" }} />
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Pending changes bar */}
      {pendingCount > 0 && (
        <div className="px-4 py-2 border-t border-border bg-amber-500/10 flex items-center justify-between">
          <span className="text-xs font-medium text-amber-600 dark:text-amber-400">
            {pendingCount} change{pendingCount !== 1 ? "s" : ""} proposed
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              className="text-xs h-6 px-2"
              onClick={handleRejectAll}
            >
              Reject All
            </Button>
            <Button
              size="sm"
              className="text-xs h-6 px-2"
              onClick={handleApplyAll}
            >
              Apply All
            </Button>
          </div>
        </div>
      )}

      {/* Input */}
      <div className="px-4 py-3 border-t border-border shrink-0">
        <div className="flex gap-2 items-end">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={mode === "ask" ? "Ask about your architecture..." : "Describe changes to make..."}
            className="flex-1 resize-none bg-muted rounded-lg px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary min-h-[36px] max-h-[120px]"
            rows={1}
            disabled={isStreaming}
          />
          {isStreaming ? (
            <Button
              size="sm"
              variant="destructive"
              className="h-9 px-3"
              onClick={handleStop}
            >
              Stop
            </Button>
          ) : (
            <Button
              size="sm"
              className="h-9 px-3"
              onClick={handleSend}
              disabled={!input.trim()}
            >
              Send
            </Button>
          )}
        </div>
        <p className="text-[10px] text-muted-foreground mt-1">
          Enter to send, Shift+Enter for new line
        </p>
      </div>
    </motion.div>
  );
}


function ToolCallBadge({ name }: { name: string }) {
  const label = name.replace(/_/g, " ");
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-blue-500/10 text-blue-600 dark:text-blue-400 text-[10px] font-medium">
      <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
      {label}
    </span>
  );
}


function ProposedChangeCard({
  change,
  onApply,
  onReject,
}: {
  change: ProposedChange;
  onApply: () => void;
  onReject: () => void;
}) {
  const isAdd = change.tool_name.startsWith("add_");
  const isDelete = change.tool_name.startsWith("delete_");

  return (
    <div
      className={cn(
        "rounded-lg border px-3 py-2 text-xs",
        change.status === "applied" && "border-green-500/30 bg-green-500/5",
        change.status === "rejected" && "border-red-500/30 bg-red-500/5 opacity-60",
        change.status === "pending" && isAdd && "border-green-500/30 bg-green-500/5",
        change.status === "pending" && isDelete && "border-red-500/30 bg-red-500/5",
        change.status === "pending" && !isAdd && !isDelete && "border-amber-500/30 bg-amber-500/5",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-foreground font-medium">{change.description}</p>
        {change.status === "pending" ? (
          <div className="flex gap-1 shrink-0">
            <button
              className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-red-500/10 text-red-600 dark:text-red-400 hover:bg-red-500/20"
              onClick={onReject}
            >
              Reject
            </button>
            <button
              className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-green-500/10 text-green-600 dark:text-green-400 hover:bg-green-500/20"
              onClick={onApply}
            >
              Apply
            </button>
          </div>
        ) : (
          <span
            className={cn(
              "text-[10px] font-medium shrink-0",
              change.status === "applied" ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400",
            )}
          >
            {change.status === "applied" ? "Applied" : "Rejected"}
          </span>
        )}
      </div>
    </div>
  );
}
