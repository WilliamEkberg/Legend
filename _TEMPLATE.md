# [Feature Name]

## Section 1 (HUMAN EDITS ONLY)

### Purpose

[1-2 sentences: what this does and why it exists.]

---

### How it Works

> Focus on WHAT and WHY, not implementation details. This section forces you to think through the problem before coding.

#### Workflow

1. [Step 1 - what happens and why]
2. [Step 2 - what happens and why]
3. [Step 3 - what happens and why]

#### [Sub-component or Related System]

    [Additional business logic as needed. Each subsection should be independently understandable.]

#### Rules & Edge Cases

- [Constraint or rule]
- [What happens in unusual situations]

---

## Section 2 (HUMAN + AI)

### Architecture

#### Data Model

```typescript
// TS/SQL shapes that matter
```

#### System Flow

```
User action
    ↓
System validates
    ↓
Process request
    ↓
Update state
    ↓
Return feedback
```

#### Key Components

- `path/to/Component.tsx` — [what it does]
- `path/to/function.ts` — [what it does]

---

### Implementation

#### Files to Create

- `path/to/new-file.ts` — [purpose]

#### Files to Modify

- `path/to/existing.ts` — [what changes]

#### Backend API

- `functionName()` — [one-line description]

---

### Key Technical Decisions

> Document non-obvious choices with rationale. Helps future maintainers understand WHY.

#### [Decision 1]

**Choice:** [What was decided]

**Why:** [Reason - what alternatives were considered, why this wins]

---

## Planned Changes

- [ ] Current task item
- [ ] Another task item

---

## Log

- YYYY-MM-DD :: author :: Created doc

---

<!--
OPTIONAL SECTIONS - Add to Section 2 when relevant:

### Permission Model
[Roles, auth rules, source of truth for access control]

### Database Changes
[New tables, modified schemas, indexes, migrations]

### Tests
[Test strategy, test files to create, key scenarios]

### Dependencies
[External libs, cross-feature links]

### Long Term Planned
[Future ideas - separate from current Planned Changes]

### Security Considerations
[Auth, data protection, vulnerabilities and mitigations]

### Performance Considerations
[Expected load, optimization strategies, targets]
-->
