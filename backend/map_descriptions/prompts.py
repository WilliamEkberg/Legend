# Doc: Natural_Language_Code/map_descriptions/info_map_descriptions.md
"""
LLM prompt templates for Part 3 map descriptions.

Three prompts:
1. Component decision extraction (Phase A)
2. Module elevation — cross-cutting patterns (Phase B)
3. Module deployment decisions (Phase B)
"""

# ---------------------------------------------------------------------------
# Component decision extraction
# ---------------------------------------------------------------------------

COMPONENT_SYSTEM = """\
You are a technical architect extracting architectural decisions from source code.
Your task is to identify the concrete, falsifiable decisions that define a software component.
A good decision is specific, structural, and verifiable by reading the code.
Output only valid JSON — no prose, no explanation outside the JSON object.\
"""


def component_extraction_prompt(component_name: str, file_contents: str) -> str:
    return f"""\
Analyze the source code for the "{component_name}" component and extract its technical decisions.

Categories (use exactly these strings):
- api_contracts    — Interfaces exposed, method signatures, input/output contracts
- patterns         — Design patterns and architectural choices (Repository, Middleware, Event-driven, etc.)
- libraries        — External dependencies and how they are used
- boundaries       — What this component does NOT do; where it delegates to other components
- error_handling   — Failure modes, error strategies, retry/fallback behavior
- data_flow        — What data enters, what transformations happen, what leaves

Each decision has two parts:
- "text": A SHORT label (max ~10 words) that gives intuition about what this decision covers. Think of it as a heading you'd scan in a list.
- "detail": The actual technical substance — concrete, falsifiable, specific. This is where the real information goes: what exactly the code does, specific function names, patterns, constraints, and trade-offs. Use newlines (\\n) to separate distinct points within the detail for readability — don't write one massive run-on paragraph.

Examples of good decisions:
- (api_contracts)   text: "Single public auth entry point"
                    detail: "Exposes authenticate(token) -> User | Error as the sole interface.\nAll auth checks funnel through this single entry point.\nCalled per-request by middleware with the raw Bearer token."
- (patterns)        text: "Repository pattern for DB access"
                    detail: "All database queries go through a Repository interface.\nNo component accesses the DB directly."
- (libraries)       text: "jsonwebtoken for token handling"
                    detail: "Chose over jose for simpler API.\nOnly HS256 and RS256 algorithms are allowed.\nTokens parsed and validated in a single call."
- (boundaries)      text: "Delegates user lookup externally"
                    detail: "Never queries the users database directly.\nAll user resolution goes through the users component's public API."
- (error_handling)  text: "HTTP 401/403 error strategy"
                    detail: "Returns 401 for expired tokens with WWW-Authenticate header (error=invalid_token) for client-side refresh flows.\n403 for insufficient permissions.\nNo retry logic."
- (data_flow)       text: "HTTP body → JWT → typed User"
                    detail: "Accepts raw HTTP request body.\nValidates JWT payload structure and signature.\nReturns typed User object.\nRejects malformed tokens before signature check."

Examples of decisions to SKIP:
- "Written in TypeScript" (language is a module-level fact, not a component decision)
- "Exports a default function" (trivially true, no alternative was considered)
- "Imports React" (obvious from the technology stack)

Rules:
- "text" MUST be short (max ~10 words) — it is a scannable label, not the decision itself
- "detail" carries the real substance — be specific, name functions, patterns, constraints
- Every statement must be traceable to actual code in the files provided
- Do not invent decisions that are not in the code
- Only include a decision if it represents a choice where a reasonable alternative existed. Skip facts that are trivially obvious from the technology stack.
- Not every category applies to every component. Only use categories where the component makes a notable decision.
- Extract 2–8 decisions (fewer for small components, more for complex ones)

Source files:
{file_contents}

Respond with JSON only:
{{
  "decisions": [
    {{"category": "<category>", "text": "<concise one-sentence summary>", "detail": "<optional deeper context or null>"}}
  ]
}}"""


# ---------------------------------------------------------------------------
# Module elevation — cross-cutting patterns
# ---------------------------------------------------------------------------

MODULE_ELEVATION_SYSTEM = """\
You are a technical architect identifying cross-cutting patterns across software components.
Your task is to find decisions that appear in similar form across the majority of components in a module.
Elevated decisions replace their source component decisions — return the exact source IDs.
Output only valid JSON — no prose, no explanation outside the JSON object.\
"""


def elevation_prompt(
    module_name: str,
    module_type: str,
    module_technology: str,
    component_decisions_text: str,
) -> str:
    return f"""\
Analyze the component decisions for module "{module_name}" (type: {module_type}, technology: {module_technology}).

Find decisions that appear in similar form across more than 50% of the listed components.
These are cross-cutting patterns that should be elevated to module level.

Rules:
- "text" MUST be short (max ~10 words) — a scannable label for the cross-cutting pattern
- "detail" carries the substance — what the pattern is, which components share it, specifics. Use newlines (\\n) to separate distinct points within the detail for readability.
- Only elevate decisions shared across the MAJORITY of components (>50%)
- Do NOT elevate conflicting decisions (e.g. one component uses Zod, another uses Joi) — keep those at component level
- Return the merged statement and the exact IDs of all source decisions to delete
- If no cross-cutting patterns exist, return an empty list

Component decisions (each decision shows [id] (category) text):
{component_decisions_text}

Respond with JSON only:
{{
  "elevated": [
    {{
      "text": "<concise one-sentence summary>",
      "detail": "<optional deeper context or null>",
      "source_decision_ids": [<id>, <id>, ...]
    }}
  ]
}}"""


# ---------------------------------------------------------------------------
# Module deployment decisions
# ---------------------------------------------------------------------------

MODULE_DEPLOYMENT_SYSTEM = """\
You are a technical architect extracting deployment-level decisions from configuration files.
Your task is to identify concrete, falsifiable decisions about how a module is deployed, configured, and operated.
Output only valid JSON — no prose, no explanation outside the JSON object.\
"""


def deployment_prompt(
    module_name: str,
    module_type: str,
    module_technology: str,
    deployment_target: str,
    file_contents: str,
) -> str:
    return f"""\
Analyze the deployment configuration files for module "{module_name}".

Module metadata:
- Type: {module_type}
- Technology: {module_technology}
- Deployment target: {deployment_target}

Extract deployment-level decisions covering:
- Runtime environment (what platform/runtime does this module use?)
- Entry points (how is it started or invoked?)
- Configuration (env vars, config files, CLI arguments)
- Inter-module communication (HTTP, message queues, shared databases, etc.)
- Build and deployment (how is it built and deployed?)

Rules:
- "text" MUST be short (max ~10 words) — a scannable label for the deployment decision
- "detail" carries the substance — specific config values, file paths, environment details. Use newlines (\\n) to separate distinct points within the detail for readability.
- Only include decisions traceable to the configuration files provided
- Do not repeat decisions already captured at the component level

Configuration files:
{file_contents}

Respond with JSON only:
{{
  "decisions": [
    {{"category": "deployment", "text": "<concise one-sentence summary>", "detail": "<optional deeper context or null>"}}
  ]
}}"""
