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

Examples of good decisions:
- (api_contracts)   "Exposes authenticate(token) -> User | Error as the sole public interface"
- (patterns)        "Uses the Repository pattern to abstract all database access behind an interface"
- (libraries)       "Uses jsonwebtoken for token parsing and validation"
- (boundaries)      "Delegates user lookup to the users component — never queries the database directly"
- (error_handling)  "Returns 401 for expired tokens and 403 for insufficient permissions; no retry"
- (data_flow)       "Accepts raw HTTP request body, validates JWT payload, returns typed User object"

Examples of decisions to SKIP:
- "Written in TypeScript" (language is a module-level fact, not a component decision)
- "Exports a default function" (trivially true, no alternative was considered)
- "Imports React" (obvious from the technology stack)

Rules:
- Every statement must be traceable to actual code in the files provided
- Do not invent decisions that are not in the code
- Only include a decision if it represents a choice where a reasonable alternative existed. Skip facts that are trivially obvious from the technology stack.
- Not every category applies to every component. Only use categories where the component makes a notable decision.
- Extract 2–8 decisions (fewer for small components, more for complex ones)
- Each decision must be a single, self-contained falsifiable statement

Source files:
{file_contents}

Respond with JSON only:
{{
  "decisions": [
    {{"category": "<category>", "text": "<falsifiable statement>"}}
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
- Only elevate decisions shared across the MAJORITY of components (>50%)
- Do NOT elevate conflicting decisions (e.g. one component uses Zod, another uses Joi) — keep those at component level
- Return the merged statement text and the exact IDs of all source decisions to delete
- If no cross-cutting patterns exist, return an empty list

Component decisions (each decision shows [id] (category) text):
{component_decisions_text}

Respond with JSON only:
{{
  "elevated": [
    {{
      "text": "<cross-cutting decision statement>",
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
- Only include decisions traceable to the configuration files provided
- Each decision must be a single, falsifiable statement
- Do not repeat decisions already captured at the component level

Configuration files:
{file_contents}

Respond with JSON only:
{{
  "decisions": [
    {{"category": "deployment", "text": "<deployment decision statement>"}}
  ]
}}"""
