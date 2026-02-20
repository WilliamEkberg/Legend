# Doc: Natural_Language_Code/revalidation/info_revalidation.md
"""
LLM prompt templates for re-validation pipeline.

Four prompts:
1. New file classification (file contents + existing components → assign or propose new)
2. Component pipeline-decision revalidation (code + decisions → comparison)
3. Human decision implementation check (code + human decisions → status)
4. Module decision revalidation (component decisions + module decisions → comparison)
"""

# ---------------------------------------------------------------------------
# New file classification (Phase 0)
# ---------------------------------------------------------------------------

NEW_FILE_CLASSIFICATION_SYSTEM = """\
You are a technical architect classifying newly added source files into an \
existing component structure. For each new file, decide whether it belongs \
to an existing component or requires a new component. Base your decision on \
the file's imports, naming conventions, and functional purpose relative to \
the existing components.
Output only valid JSON — no prose, no explanation outside the JSON object.\
"""


def new_file_classification_prompt(
    module_name: str,
    existing_components: list[dict],
    new_file_contents: str,
) -> str:
    components_text = "\n".join(
        f"  - {c['name']}: {c.get('purpose', 'no description')} (files: {', '.join(c.get('files', []))})"
        for c in existing_components
    )
    return f"""\
Classify the following new source files into the component structure of module "{module_name}".

Existing components:
{components_text}

New files to classify:
{new_file_contents}

For each new file, decide:
1. If it clearly belongs to an existing component, assign it there.
2. If it doesn't fit any existing component, propose a new component with a name and purpose.

Rules:
- Prefer assigning to existing components when there is a reasonable fit.
- Only propose a new component when the file serves a clearly distinct purpose.
- If multiple new files belong together, group them under the same new component.

Respond with JSON only:
{{
  "classifications": [
    {{"file": "<path>", "existing_component": "<component_name>"}},
    {{"file": "<path>", "new_component": {{"name": "<component_name>", "purpose": "<1-2 sentence purpose>"}}}}
  ]
}}"""


# ---------------------------------------------------------------------------
# Component pipeline-decision revalidation
# ---------------------------------------------------------------------------

COMPONENT_REVALIDATION_SYSTEM = """\
You are a technical architect validating whether existing architectural decisions \
still accurately describe source code. You must be conservative: only flag a \
decision as changed if there is a concrete, specific difference between what the \
decision states and what the code does. Do NOT reframe or improve wording — only \
identify factual inaccuracies.
Output only valid JSON — no prose, no explanation outside the JSON object.\
"""


def component_revalidation_prompt(
    component_name: str,
    existing_decisions: list[dict],
    file_contents: str,
) -> str:
    decisions_text = "\n".join(
        f"  [{d['id']}] ({d['category']}) {d['text']}"
        for d in existing_decisions
    )
    return f"""\
Validate the existing decisions for the "{component_name}" component against its current source code.

Existing decisions:
{decisions_text}

For each decision, classify it as:
- "confirmed" — The decision is still factually accurate. The code matches what the decision says.
- "updated" — The code has changed in a way that makes the decision factually wrong. Provide the corrected text.
- "outdated" — The code pattern described by this decision has been completely removed.

CRITICAL RULES:
- Do NOT reword decisions that are essentially correct. If the meaning is the same, mark as "confirmed".
- Only mark "updated" if the code has actually changed in a way that makes the statement false.
- Better wording alone is NOT a reason to mark as "updated".

Also check if the code contains important architectural decisions not covered by any existing decision. If so, list them as "new_decisions".

Source files:
{file_contents}

Respond with JSON only:
{{
  "validations": [
    {{"decision_id": <id>, "status": "confirmed"}},
    {{"decision_id": <id>, "status": "updated", "new_text": "<corrected statement>", "reason": "<what changed>"}},
    {{"decision_id": <id>, "status": "outdated", "reason": "<why it no longer applies>"}}
  ],
  "new_decisions": [
    {{"category": "<category>", "text": "<new falsifiable statement>"}}
  ]
}}"""


# ---------------------------------------------------------------------------
# Human decision implementation check
# ---------------------------------------------------------------------------

HUMAN_DECISION_CHECK_SYSTEM = """\
You are checking whether human-specified architectural decisions have been \
implemented in the source code. These decisions represent intentional changes \
that a human wants to make (or has already made). Check if the code now reflects \
each decision.
Output only valid JSON — no prose, no explanation outside the JSON object.\
"""


def human_decision_check_prompt(
    component_name: str,
    human_decisions: list[dict],
    file_contents: str,
) -> str:
    decisions_text = "\n".join(
        f"  [{d['id']}] ({d['category']}) {d['text']}"
        for d in human_decisions
    )
    return f"""\
Check whether these human-specified decisions for "{component_name}" have been implemented in the code.

Human decisions:
{decisions_text}

For each decision, classify it as:
- "implemented" — The code now matches what this decision describes. The intended change has been made.
- "diverged" — The code has moved away from what this decision describes. The code contradicts the intent.
- "unchanged" — The decision is still pending. The code hasn't been modified to match it yet.

Source files:
{file_contents}

Respond with JSON only:
{{
  "validations": [
    {{"decision_id": <id>, "status": "implemented", "reason": "<evidence in code>"}},
    {{"decision_id": <id>, "status": "diverged", "reason": "<how code contradicts>"}},
    {{"decision_id": <id>, "status": "unchanged", "reason": "<what's still missing>"}}
  ]
}}"""


# ---------------------------------------------------------------------------
# Module decision revalidation
# ---------------------------------------------------------------------------

MODULE_REVALIDATION_SYSTEM = """\
You are a technical architect validating whether module-level decisions still \
accurately summarize the component decisions listed below. Module decisions are \
cross-cutting patterns shared across components and deployment-level concerns. \
Only flag changes if the underlying component decisions have materially shifted.
Output only valid JSON — no prose, no explanation outside the JSON object.\
"""


def module_revalidation_prompt(
    module_name: str,
    existing_module_decisions: list[dict],
    component_decisions_text: str,
) -> str:
    decisions_text = "\n".join(
        f"  [{d['id']}] ({d['category']}) {d['text']}"
        for d in existing_module_decisions
    )
    return f"""\
Validate the existing module-level decisions for "{module_name}" against the current component decisions.

Existing module decisions:
{decisions_text}

Current component decisions:
{component_decisions_text}

For each module decision, classify it as:
- "confirmed" — Still accurately summarizes the component decisions.
- "updated" — Component decisions have shifted, module decision needs updating. Provide corrected text.
- "outdated" — The cross-cutting pattern or deployment concern no longer exists.

CRITICAL RULES:
- Do NOT reword decisions that are essentially correct.
- Only mark "updated" if the component decisions have materially changed.
- Cross-cutting decisions should still appear across the majority of components to be valid.

Also check if there are new cross-cutting patterns not captured by existing module decisions.

Respond with JSON only:
{{
  "validations": [
    {{"decision_id": <id>, "status": "confirmed"}},
    {{"decision_id": <id>, "status": "updated", "new_text": "<corrected statement>", "reason": "<what changed>"}},
    {{"decision_id": <id>, "status": "outdated", "reason": "<why it no longer applies>"}}
  ],
  "new_decisions": [
    {{"category": "cross_cutting", "text": "<new cross-cutting decision>"}}
  ]
}}"""
