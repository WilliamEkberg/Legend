// Doc: Natural_Language_Code/Frontend/info_frontend.md

import type {
  MapData,
  MapModule,
  MapComponent,
  MapDecision,
  MapEdgeRaw,
} from "../data/types";

/** Replace pipe separators with commas for safe markdown table cells. */
function escapeForTable(value: string | null | undefined): string {
  return (value ?? "").replace(/\s*\|\s*/g, ", ");
}

/** Sanitize a module name into a valid filename (no extension). */
function toFileName(name: string): string {
  return name
    .replace(/[^a-zA-Z0-9_\- ]/g, "")
    .trim()
    .replace(/\s+/g, "_");
}

/** Group decisions by category and render as markdown. */
function renderDecisions(
  decisions: MapDecision[],
  headingPrefix: string,
): string {
  if (decisions.length === 0) return "";

  const grouped = new Map<string, MapDecision[]>();
  for (const d of decisions) {
    const cat = d.category || "General";
    if (!grouped.has(cat)) grouped.set(cat, []);
    grouped.get(cat)!.push(d);
  }

  const lines: string[] = [];
  for (const [category, items] of grouped) {
    lines.push(`${headingPrefix} ${category}`);
    for (const d of items) {
      lines.push(`- ${d.text} *(source: ${d.source})*`);
      if (d.detail) {
        for (const detailLine of d.detail.split("\n")) {
          lines.push(`  > ${detailLine}`);
        }
      }
      lines.push("");
    }
    lines.push("");
  }
  return lines.join("\n");
}

/** Render a single component as a markdown sub-section. */
function renderComponent(component: MapComponent): string {
  const lines: string[] = [];

  lines.push(`### ${component.name}`);
  lines.push("");
  lines.push(`**Purpose:** ${component.purpose}`);
  lines.push("");

  if (component.files.length > 0) {
    lines.push("**Files:**");
    for (const f of component.files) {
      const suffix = f.is_test ? " *(test)*" : "";
      lines.push(`- \`${f.path}\`${suffix}`);
    }
    lines.push("");
  }

  if (component.decisions.length > 0) {
    lines.push("**Decisions:**");
    lines.push("");
    lines.push(renderDecisions(component.decisions, "####"));
  }

  return lines.join("\n");
}

/** Build the full markdown content for one module. */
export function buildModuleMd(
  module: MapModule,
  allModules: MapModule[],
  moduleEdges: MapEdgeRaw[],
): string {
  const lines: string[] = [];

  lines.push(`# ${module.name}`);
  lines.push("");

  // Overview table
  lines.push("## Overview");
  lines.push("");
  lines.push("| Property | Value |");
  lines.push("|----------|-------|");
  lines.push(`| Classification | ${module.classification} |`);
  lines.push(`| Type | ${module.type} |`);
  lines.push(`| Technology | ${escapeForTable(module.technology)} |`);
  lines.push(`| Deployment Target | ${module.deployment_target} |`);
  lines.push(`| Source Origin | ${module.source_origin} |`);
  lines.push("");

  // Directories
  if (module.directories.length > 0) {
    lines.push("## Directories");
    lines.push("");
    for (const dir of module.directories) {
      lines.push(`- \`${dir}\``);
    }
    lines.push("");
  }

  // Dependencies (outgoing edges)
  const idToName = new Map(allModules.map((m) => [m.id, m.name]));
  const outgoing = moduleEdges.filter((e) => e.source_id === module.id);
  const incoming = moduleEdges.filter((e) => e.target_id === module.id);

  if (outgoing.length > 0) {
    lines.push("## Dependencies");
    lines.push("");
    for (const e of outgoing) {
      const target = idToName.get(e.target_id) ?? `Module #${e.target_id}`;
      lines.push(`- **${e.edge_type}** -> ${target} (weight: ${e.weight})`);
    }
    lines.push("");
  }

  if (incoming.length > 0) {
    lines.push("## Depended On By");
    lines.push("");
    for (const e of incoming) {
      const source = idToName.get(e.source_id) ?? `Module #${e.source_id}`;
      lines.push(
        `- ${source} **${e.edge_type}** this module (weight: ${e.weight})`,
      );
    }
    lines.push("");
  }

  // Module-level decisions
  if (module.decisions.length > 0) {
    lines.push("## Key Decisions");
    lines.push("");
    lines.push(renderDecisions(module.decisions, "###"));
  }

  // Components
  if (module.components.length > 0) {
    lines.push("---");
    lines.push("");
    lines.push("## Components");
    lines.push("");
    for (const comp of module.components) {
      lines.push(renderComponent(comp));
      lines.push("---");
      lines.push("");
    }
  }

  return lines.join("\n");
}

/** Build the Table_of_content.md content. */
export function buildTableOfContents(
  modules: MapModule[],
  moduleEdges: MapEdgeRaw[],
  fileNameMap: Map<number, string>,
): string {
  const lines: string[] = [];

  lines.push("# Architecture Map - LLM Context");
  lines.push("");
  lines.push(`*Exported: ${new Date().toISOString()}*`);
  lines.push("");

  // Modules table
  lines.push("## Modules");
  lines.push("");
  lines.push(
    "| Module | Classification | Technology | Components | File |",
  );
  lines.push(
    "|--------|---------------|------------|------------|------|",
  );
  for (const mod of modules) {
    const fname = fileNameMap.get(mod.id) ?? "unknown.md";
    lines.push(
      `| ${mod.name} | ${mod.classification} | ${escapeForTable(mod.technology)} | ${mod.components.length} | [${fname}](./${fname}) |`,
    );
  }
  lines.push("");

  // Dependency graph
  const idToName = new Map(modules.map((m) => [m.id, m.name]));
  const edgesBySource = new Map<number, MapEdgeRaw[]>();
  for (const e of moduleEdges) {
    if (!edgesBySource.has(e.source_id)) edgesBySource.set(e.source_id, []);
    edgesBySource.get(e.source_id)!.push(e);
  }

  if (moduleEdges.length > 0) {
    lines.push("## Module Dependency Graph");
    lines.push("");
    for (const mod of modules) {
      const edges = edgesBySource.get(mod.id);
      if (!edges || edges.length === 0) continue;
      lines.push(`### ${mod.name}`);
      for (const e of edges) {
        const target = idToName.get(e.target_id) ?? `Module #${e.target_id}`;
        lines.push(`- ${e.edge_type} -> ${target}`);
      }
      lines.push("");
    }
  }

  return lines.join("\n");
}

/** Convert MapData into a Map of filename -> markdown content. */
export function buildLlmContextFiles(
  mapData: MapData,
): Map<string, string> {
  const files = new Map<string, string>();
  const fileNameMap = new Map<number, string>();

  // Build unique filenames
  const usedNames = new Set<string>();
  for (const mod of mapData.modules) {
    let base = toFileName(mod.name);
    if (!base) base = `Module_${mod.id}`;
    let fname = `${base}.md`;
    if (usedNames.has(fname)) {
      fname = `${base}_${mod.id}.md`;
    }
    usedNames.add(fname);
    fileNameMap.set(mod.id, fname);
  }

  // Build module files
  for (const mod of mapData.modules) {
    const fname = fileNameMap.get(mod.id)!;
    const content = buildModuleMd(mod, mapData.modules, mapData.module_edges);
    files.set(fname, content);
  }

  // Build table of contents
  const toc = buildTableOfContents(
    mapData.modules,
    mapData.module_edges,
    fileNameMap,
  );
  files.set("Table_of_content.md", toc);

  return files;
}
