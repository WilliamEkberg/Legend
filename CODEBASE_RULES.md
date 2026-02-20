# Project Documentation Guide

info_[Name].md- This projects sourse of truth is the md fles in the Natural_Language_Code folder. This is the primary code and shuld be detailed enogh to be able to build tha actual code without more context. Code examples can be part of it but shuld be limited to short parts that are sensitive. When we work we first look in these md files and edit in them untill we are satisfied with the solution, then we implement the actual code.

## Natural_Language_Code folder

This folder maintains a logical structure with folders to separate different parts of the codebase.

### Folder Structure (Hybrid Approach)

```
Natural_Language_Code/
├── auth/
│   ├── info_auth.md              # Main file for auth domain
│   ├── info_auth_oauth.md        # Sub-file: OAuth details
│   └── info_auth_permissions.md  # Sub-file: Permission system
├── billing/
│   ├── info_billing.md           # Main file for billing domain
│   └── info_billing_stripe.md    # Sub-file: Stripe integration
```

### Naming Convention

- **Main file:** `info_[feature].md` — overview, high-level how it works
- **Sub-file:** `info_[feature]_[component].md` — detailed docs for specific parts

### When to Split

Split when a document exceeds ~200 lines or has complex sub-components that deserve their own doc.

### Linking Between Files

In the main file, add a "Sub-documents" section:

```markdown
## Sub-documents
- [OAuth Integration](./info_auth_oauth.md)
- [Permission System](./info_auth_permissions.md)
```

In sub-files, link back at the top:

```markdown
> Part of [Auth System](./info_auth.md)
```

### Code → Doc References

Code files should reference their documentation at the top:

```typescript
// Doc: Natural_Language_Code/auth/info_auth_oauth.md
```

```python
# Doc: Natural_Language_Code/billing/info_billing_stripe.md
```

This creates bidirectional links — docs list code files in Implementation, code files point back to docs.


## Workflow and rules THAT MUST BE FOLLOWED

`info_[folder].md` must always be accurate and current.

- **Plan first (in docs).** Before changing code, edit the relevant `info_[folder].md` in that feature’s folder to describe the intended changes.
- **Iterate in the md file.** Review and refine the plan until it’s solid. Record decisions in a `Planned changes` section at the bottom of the file.
- **Implement.** Make the code changes exactly as documented. If something proves impractical, pause and update the md plan before continuing.
- **Test.** Test so that types are correct and then run "npm run build". If something needs fixing: go back and fix it.
- **Sync after shipping.(IMPORTANT!)** Update `info_[folder].md` so it reflects the final state. Then clean up the `Planned changes` section (summarize or archive as needed). YOU MUST read throgh all the files that you have edited before making this sync.
- **Logging** Add a log to the "Changes Log" section. Add new updates at the bottom of the Log. Logs should include the human responsible for the changes or AI session.

## Structure of `info_[folder].md` files

Use `_TEMPLATE.md` as your starting point. Copy it and fill in the sections.

### Section 1 (HUMAN EDITS ONLY) Do not edit without explicit human approval 

| Section | Purpose |
|---------|---------|
| **Purpose** | 1-2 sentences on what this feature does and why |
| **How it Works** | Business logic in plain language — workflow, rules, edge cases |

### Section 2 (HUMAN + AI) Can collaborate freely  

| Section | Purpose |
|---------|---------|
| **Architecture** | Data Model, System Flow, Key Components |
| **Implementation** | Files to create/modify, Backend API |
| **Key Technical Decisions** | Non-obvious choices with rationale |

### Always Include

| Section | Purpose |
|---------|---------|
| **Planned Changes** | Checkboxes for current work items |
| **Log** | Running log: `YYYY-MM-DD :: author :: note` | (author refers to the user reponsable for the AI)

### Optional Sections (add when relevant)

- **Permission Model** — Roles, auth rules
- **Database Changes** — Schema modifications, migrations
- **Tests** — Test strategy and scenarios
- **Dependencies** — External libs, cross-feature links
- **Long Term Planned** — Future ideas (not current work)
- **Security / Performance** — Domain-specific concerns

**Notes**
- Keep file paths explicit (e.g., `components/WorkspaceView.tsx`).
- If something is not implemented it can only exist in `Planned Changes` or `Long Term Planned`.
- Log all decisions in `Log`; summarize significant ones in `Key Technical Decisions`.
- If docs and code conflict, ask — don't assume   


# Writing tests:
When you write tests make sure to put them in a test folder as a subfolder for where we are working so they are easy to find and use.
They shuld have:
- An sh file to run them easally
- A readme file with a short explination of what is tested