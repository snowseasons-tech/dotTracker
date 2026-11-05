# End-to-end agent workflow for application delivery

This workflow coordinates one lead agent and three specialist agents from a product brief through a verified release. It is designed for dotTracker, then reusable for other Snow Seasons Tech apps. It defines roles, task boundaries, handoffs, and release gates; plug it into the local runner you choose rather than assuming a particular model endpoint or agent CLI.

## Team and machine mapping

| Role | Suggested host | Owns |
|---|---|---|
| Coordinator / release lead | Alienware desktop | Requirements, architecture decisions, task graph, integration, final go/no-go |
| Backend / data agent | Beelink2 (Ubuntu) | API, auth, tenant isolation, schema, ingestion, migrations |
| Product UI agent | WSL2 Debian | Responsive interface, map interactions, accessibility, API wiring |
| Verification / security agent | ROG laptop | Independent test plan, abuse cases, security review, regression verification |

Keep the model runtime and the code checkout separate when practical. Do not distribute production credentials to worker machines. For concurrent edits, create one Git worktree and branch per implementation agent. The coordinator owns the integration branch; agents return commits or patches, never overwrite another agent's worktree.

## Workflow and gates

### 0. Intake

Coordinator reads the brief, repository `AGENTS.md`, existing architecture, and any user-provided constraints. Produce `product/brief.md` with:

- primary user and top jobs-to-be-done;
- MVP scope and explicit exclusions;
- success measure and acceptance criteria;
- data sensitivity, roles, tenant boundaries, and threat assumptions;
- unresolved decisions, each with an owner and due gate.

Do not dispatch implementation until acceptance criteria and MVP boundaries are concrete. If a decision can be safely defaulted, record the assumption instead of blocking.

### 1. Design contract

Coordinator owns the final architecture and writes `product/design.md` and `product/interfaces.md`. Backend agent proposes schema/API details; UI agent proposes screen states and interaction flows; verification agent reviews both for missing cases. Coordinator resolves conflicts before implementation.

The interface contract names endpoint paths, payloads, error shapes, role permissions, timestamps/time zones, pagination/limits, and tenant scoping. Schema changes include an upgrade path and rollback or recovery note. No worker invents a competing API contract during implementation.

### 2. Parallel implementation

Dispatch only tasks that do not edit the same files:

- Backend: schema/migrations, API behavior, authorization, integration seams.
- UI: screens, accessibility, loading/error/empty states, API client against the signed-off interface.
- Verification: test cases, fixtures, abuse cases, dependency and deployment checklist; review code only after interfaces stabilize.

Each task names allowed paths, prohibited scope, acceptance criteria, dependencies, and expected evidence. Agents use separate worktrees. Changes to shared contracts return to the coordinator for a decision before coding continues.

### 3. Handoff and integration

Every agent returns:

1. branch/commit or patch;
2. changed paths and concise design notes;
3. commands run and exact results;
4. known gaps or failed checks;
5. compatibility notes and migration impact.

Coordinator reviews the diff, checks that scope did not expand, integrates one branch at a time, resolves conflicts, and runs the repository's full check set. A green worker report is evidence, not approval; the coordinator reproduces required checks after integration.

### 4. Independent verification

Verification agent receives the acceptance criteria and integrated diff, not an implementation summary alone. It attempts to falsify the release by checking:

- unauthenticated and wrong-role behavior;
- tenant A cannot read or mutate tenant B data, including foreign IDs;
- malformed, boundary, stale, duplicate, and out-of-order inputs;
- failure and recovery paths (database unavailable, expired/replayed ticket, reconnect, empty data);
- dependency/container findings and secret leakage;
- migration against both an empty database and the previous supported schema;
- browser/mobile layout and keyboard/screen-reader-critical paths where applicable.

Each finding is written as severity, affected path, reproducible evidence, impact, and a proposed fix. Critical/high findings block release. Medium findings require a named owner and explicit coordinator decision; do not silently waive them.

### 5. Release gate

Coordinator can mark release-ready only when all required checks pass on the integrated commit:

- requirements-to-tests traceability is complete;
- automated unit, integration, and end-to-end checks pass;
- schema migration and rollback/recovery are rehearsed;
- dependency, container, and secret scans have no unresolved critical/high findings;
- production configuration is validated without demo settings;
- backup restore, health checks, logs, metrics, and alert path are exercised;
- deployment and rollback instructions are reviewable;
- independent verification report has no release-blocking issues.

If infra cannot be reached, mark the gate **not run** with the missing dependency. Never report a build as production-verified from static checks alone. User approval is the final step before irreversible public launch or customer-data migration.

### 6. Deploy and learn

Coordinator prepares a versioned release note, deployment checklist, rollback trigger, and pilot feedback capture. Deploy first to staging, exercise smoke checks, then seek the required release decision and deploy production. After release, monitor error rates, latency, ingest freshness, and user feedback; assign owners and target dates to follow-up issues. New ideas go to the backlog, not into an unreviewed hotfix.

## Task sizing and routing

A task should fit one specialist, have a clear file boundary, and produce a reviewable commit. Split work whenever it mixes API contract changes with UI rewrites or combines feature work with independent security verification. Keep contract ownership with the coordinator. For a solo run, use the same sequence and gates serially.

## dotTracker first run plan

| Order | Owner | Deliverable | Gate |
|---|---|---|---|
| 1 | Coordinator | Accept criteria, MVP exclusions, threat boundaries | User jobs and roles are explicit |
| 2 | Backend + UI; coordinator resolves | API/schema contract and screen flow | No interface ambiguity |
| 3 | Backend | Tenant-safe migrations, auth, assets, ingest, fence events | DB integration checks pass |
| 4 | UI | Map, assets, history, observations, alert states | All API states represented |
| 5 | Verification | Tenant isolation, replay, timestamps, geofence and deploy test report | No critical/high issues |
| 6 | Coordinator | Merge, regression checks, release package | Full release checklist passes |
| 7 | Coordinator + operator | Staging, smoke, backup restore rehearsal, production rollout | Rollback is ready |

The current dotTracker workspace cannot complete the database/container gates because Docker and PostgreSQL are unavailable there. The current app is therefore a hardened development/pilot candidate, not production-verified.
