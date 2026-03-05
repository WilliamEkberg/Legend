// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { useState, useRef, useEffect } from "react";
import { Send } from "lucide-react";
import { cn } from "@/lib/utils";

interface MapChatBarProps {
  onSubmit: (message: string) => void;
}

export function MapChatBar({ onSubmit }: MapChatBarProps) {
  const [input, setInput] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = "auto";
      ta.style.height = Math.min(ta.scrollHeight, 96) + "px";
    }
  }, [input]);

  const handleSubmit = () => {
    const trimmed = input.trim();
    if (!trimmed) return;
    onSubmit(trimmed);
    setInput("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="absolute bottom-6 left-1/2 -translate-x-1/2 z-20 w-full max-w-[560px] px-4">
      <div
        className={cn(
          "flex items-end gap-2 rounded-2xl border border-border",
          "bg-card/80 backdrop-blur-xl shadow-lg",
          "px-4 py-2.5",
          "ring-1 ring-border/50",
        )}
      >
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about your architecture..."
          className="flex-1 resize-none bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none min-h-[24px] max-h-[96px] py-0.5 leading-relaxed"
          rows={1}
        />
        <button
          onClick={handleSubmit}
          disabled={!input.trim()}
          className={cn(
            "shrink-0 flex items-center justify-center w-8 h-8 rounded-xl transition-colors",
            input.trim()
              ? "bg-primary text-primary-foreground hover:bg-primary/90"
              : "bg-muted text-muted-foreground cursor-not-allowed",
          )}
        >
          <Send className="w-4 h-4" />
        </button>
      </div>
      <p className="text-center text-[10px] text-muted-foreground/60 mt-1.5">
        Enter to chat · Shift+Enter for new line
      </p>
    </div>
  );
}
