// Doc: Natural_Language_Code/ticket_generation/info_ticket_generation.md
// Doc: Natural_Language_Code/Frontend/info_frontend.md

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { save } from "@tauri-apps/plugin-dialog";
import { writeTextFile } from "@tauri-apps/plugin-fs";
import type { GeneratedTicket } from "../../data/types";

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
    <div className="ticket-card">
      <div className="ticket-header">
        <h3 className="ticket-title">{ticket.title}</h3>
        <button
          className={`ticket-copy-btn${copied ? " copied" : ""}`}
          onClick={handleCopy}
          title="Copy as markdown"
        >
          {copied ? "Copied!" : "Copy"}
        </button>
      </div>

      <p className="ticket-description">{ticket.description}</p>

      {ticket.acceptance_criteria && (
        <div className="ticket-section">
          <span className="ticket-section-label">Acceptance Criteria</span>
          <p className="ticket-criteria">{ticket.acceptance_criteria}</p>
        </div>
      )}

      {ticket.affected_files.length > 0 && (
        <div className="ticket-section">
          <span className="ticket-section-label">Files</span>
          <ul className="ticket-files">
            {ticket.affected_files.map((f) => (
              <li key={f} className="ticket-file">
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
        className="ticket-panel"
        initial={{ y: "100%" }}
        animate={{ y: 0 }}
        exit={{ y: "100%" }}
        transition={{ type: "spring", damping: 40, stiffness: 200 }}
      >
        <div className="ticket-panel-header">
          <div className="ticket-panel-title-row">
            <h2 className="ticket-panel-title">
              Tickets
              <span className="ticket-count">{tickets.length}</span>
            </h2>
            {mapCorrections > 0 && (
              <span className="map-corrections-note">
                {mapCorrections} map correction{mapCorrections !== 1 ? "s" : ""} (no code change needed)
              </span>
            )}
          </div>
          <div className="ticket-panel-actions">
            {tickets.length > 0 && (
              <button className="ticket-download-btn" onClick={downloadAll}>
                Download
              </button>
            )}
            {tickets.length > 1 && (
              <button className="ticket-copy-all-btn" onClick={copyAll}>
                Copy All
              </button>
            )}
            <button className="ticket-panel-close" onClick={onClose}>
              &times;
            </button>
          </div>
        </div>

        <div className="ticket-panel-body">
          {tickets.length === 0 ? (
            <p className="ticket-empty">
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
