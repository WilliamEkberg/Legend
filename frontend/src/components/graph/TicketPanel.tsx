// Doc: Natural_Language_Code/ticket_generation/info_ticket_generation.md
// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { save } from "@tauri-apps/plugin-dialog";
import { writeTextFile } from "@tauri-apps/plugin-fs";
import type { GeneratedTicket } from "../../data/types";
import { Button } from "@/components/ui/button";

interface TicketPanelProps {
  tickets: GeneratedTicket[];
  mapCorrections: number;
  onClose: () => void;
}

function ticketToMarkdown(t: GeneratedTicket): string {
  const files =
    t.affected_files.length > 0
      ? `\n**Affected Files:**\n${t.affected_files.map((f) => `- \`${f}\``).join("\n")}`
      : "";
  const criteria = t.acceptance_criteria
    ? `\n**Acceptance Criteria:**\n${t.acceptance_criteria}`
    : "";
  return `## ${t.title}\n\n**Description:**\n${t.description}${criteria}${files}`;
}

function TicketCard({ ticket }: { ticket: GeneratedTicket }) {
  const [copied, setCopied] = useState(false);

  function handleCopy() {
    navigator.clipboard.writeText(ticketToMarkdown(ticket)).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-foreground">{ticket.title}</h3>
        <Button
          variant="ghost"
          size="sm"
          className="text-xs h-7"
          onClick={handleCopy}
          title="Copy as markdown"
        >
          {copied ? "Copied!" : "Copy"}
        </Button>
      </div>

      <p className="text-sm text-muted-foreground">{ticket.description}</p>

      {ticket.acceptance_criteria && (
        <div className="mt-3">
          <span className="text-xs font-medium text-muted-foreground uppercase">Acceptance Criteria</span>
          <p className="text-sm text-foreground mt-1">{ticket.acceptance_criteria}</p>
        </div>
      )}

      {ticket.affected_files.length > 0 && (
        <div className="mt-3">
          <span className="text-xs font-medium text-muted-foreground uppercase">Files</span>
          <ul className="mt-1 space-y-0.5">
            {ticket.affected_files.map((f) => (
              <li key={f} className="text-xs font-mono text-muted-foreground">
                {f}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function TicketPanel({ tickets, mapCorrections, onClose }: TicketPanelProps) {
  function copyAll() {
    const md = tickets.map(ticketToMarkdown).join("\n\n---\n\n");
    navigator.clipboard.writeText(md);
  }

  async function downloadAll() {
    const md = tickets.map(ticketToMarkdown).join("\n\n---\n\n");
    const timestamp = new Date()
      .toISOString()
      .replace(/[:.]/g, "-")
      .slice(0, 19);
    const filePath = await save({
      defaultPath: `tickets-${timestamp}.md`,
      filters: [{ name: "Markdown", extensions: ["md"] }],
    });
    if (filePath) {
      await writeTextFile(filePath, md);
    }
  }

  return (
    <AnimatePresence>
      <motion.div
        className="absolute inset-x-0 bottom-0 max-h-[60%] bg-card border-t border-border shadow-xl z-20 flex flex-col rounded-t-xl"
        initial={{ y: "100%" }}
        animate={{ y: 0 }}
        exit={{ y: "100%" }}
        transition={{ type: "spring", damping: 40, stiffness: 200 }}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-semibold text-foreground flex items-center gap-2">
              Tickets
              <span className="inline-flex items-center justify-center min-w-[22px] h-[22px] rounded-full bg-primary text-primary-foreground text-xs font-bold px-1.5">
                {tickets.length}
              </span>
            </h2>
            {mapCorrections > 0 && (
              <span className="text-xs text-muted-foreground">
                {mapCorrections} map correction{mapCorrections !== 1 ? "s" : ""} (no code change needed)
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {tickets.length > 0 && (
              <Button variant="outline" size="sm" className="text-xs" onClick={downloadAll}>
                Download
              </Button>
            )}
            {tickets.length > 1 && (
              <Button variant="outline" size="sm" className="text-xs" onClick={copyAll}>
                Copy All
              </Button>
            )}
            <Button variant="ghost" size="sm" className="text-lg px-2" onClick={onClose}>
              &times;
            </Button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {tickets.length === 0 ? (
            <p className="text-sm text-muted-foreground italic">
              {mapCorrections > 0
                ? "All changes were map corrections — the code already matches the map."
                : "No tickets generated."}
            </p>
          ) : (
            tickets.map((t) => <TicketCard key={t.id} ticket={t} />)
          )}
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
