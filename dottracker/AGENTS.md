# Repository agent instructions

- Follow [AGENT_WORKFLOW.md](AGENT_WORKFLOW.md) for role boundaries, isolated worktrees, handoffs, and release gates.
- Read [README.md](README.md) for current behavior, security assumptions, and local/production run paths before making changes.
- Keep the MVP scope aligned with the supplied dotTracker playbook. Route out-of-scope work to a follow-up backlog.
- Preserve tenant boundaries in every API query and database relationship. Add tests for authorization and cross-tenant access whenever behavior changes.
- Make schema changes as explicit, reviewed migrations. `sql/schema.sql` is only the initial-install baseline.
- Never commit `.env`, database passwords, JWT secrets, device tokens, customer data, or generated production credentials.
- Report exact checks run. State infrastructure checks as `NOT RUN` if required services are unavailable; never call static compilation production verification.
- Do not deploy, publish, or modify customer data without an explicit release instruction.
