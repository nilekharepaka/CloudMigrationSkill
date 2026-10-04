# Safety and Approval Policy

Approval gates, apply safety, and credential handling. Concrete flags and their values live in `{SKILL_DIR}/docs/references/cli-reference.md`.

---

## Approval Gates

There are three, and they are separate. Passing one never implies the next.

### Gate 1 — Execution plan

Before the first command of the migration runs, present:

- Source system type, tenant, and auth method.
- Target system, organization, and tenant.
- Folder scope and entity scope.
- Which artifacts will be written locally.
- The ordered sequence of phases, with each marked read-only or writing.
- The explicit statement that the analysis phase is read-only, that binary staging downloads files locally only, and that apply creates objects in the target only after a further approval.

Wait for a yes. Do not start with "just the read-only part" to save time — a discovery run against a production Orchestrator is a load event and it may be the wrong tenant.

### Gate 2 — Analysis report

The analysis workbook is the operator's decision document. It must let them check, without reading JSON:

- The readiness verdict, and every blocker standing behind it.
- Source and target counts per entity, reconciling against planned and skipped.
- Every record's disposition: automatic, hybrid, manual prerequisite, or manual only.
- Per entity, what migrates and what stays behind.
- What must be created in the target before apply can run.
- Every skipped record, with the reason.
- What is excluded by design.
- The apply sequence and the gates still outstanding.

Hand it over and walk them through it. Confirm specifically that they have seen the blockers, the manual prerequisites, and the skip list — those are where migrations surprise people.

**No blocker may survive this gate.** A blocker means apply would fail or would half-populate the target; clearing it is the operator's work, not something to note and move past. Approving the analysis authorizes staging and validation. It does not authorize apply.

### Gate 3 — Apply

Ask again, as its own question, naming the target tenant. Confirm:

- The plan validated clean.
- The canary size.
- That credential assets will land with a dummy password.
- That webhooks will land with a temporary secret.
- That triggers may begin firing jobs in the target once applied.
- That this apply will produce a per-entity audit report to review immediately afterward — before deciding whether to escalate the canary or re-run any failure.

The engine refuses to apply without explicit operator confirmation passed at the command line. Never wrap that confirmation into a script, a loop, or a chained command to avoid re-asking.

---

## Apply Safety Invariants

1. **A validated plan is a precondition.** Apply consumes a plan file and refuses to run without one. Validation runs against the live target, so a plan that validated last week is not a plan that validates today.
2. **The first apply against a new target is a canary.** Apply a minimal number of actions, stop, and inspect the target tenant in the UI or via read commands. Only then increase the limit. Escalate in steps — a small batch, then a larger one — rather than jumping from canary to everything.
3. **Inspect between steps, not just at the end.** The point of a canary is the inspection, not the small number.
4. **Manual-review entities are out of band.** The engine refuses a plan that mixes them with auto-apply actions, and there is no per-record exclusion — so a family containing one manual record must be dropped from the automated run wholesale. The analysis report's per-entity disposition is what tells you which families are clean.
5. **Continue-on-error is a reporting decision, not a safety net.** When apply continues past a failure, the target is now partially populated and the failure list is the only record of what did not land. Read it.
6. **There is no bulk undo.** Orchestrator creates are not transactional. Plan the canary as if rollback means manual deletion, because it does.
7. **Never hand-edit a plan and apply it.** Change scope in the config, regenerate, revalidate. A hand-edited plan invalidates every count the operator approved.
8. **Re-running apply is a resume, not a retry.** Objects already created in the target are skipped as existing. Confirm that before re-running, and never "clean up" the target by deleting objects to force a fresh run without explicit operator instruction.

---

## Credential and Secret Invariants

**The invariant: secrets are never written to skill files, config files, discovery snapshots, plan files, analysis reports, apply results, or any repository-committed artifact.**

- The engine obtains source credentials from environment variables at runtime. The config records only which variables to read, never their values.
- Set those variables in the same shell session that runs the command. Never persist them into a file, a profile that gets committed, or a chat transcript.
- The Cloud target authenticates through the `uip` CLI session. Where a target write needs the API directly, the token comes from the CLI's own sanctioned refresh contract and is held in memory for that run only. Never write it to a file, a log, an artifact, or a chat message, and never hand-roll a second credential for the target.
- Do not echo, print, or summarize a secret value, even partially, even to confirm it is set. Confirm presence, not content.
- Credential asset values are not returned by Orchestrator metadata calls. There is no flag, scope, or endpoint that changes this. Do not go looking for one.

**Credential assets** are created with the configured dummy password and flagged for post-migration correction. Every such asset is a live misconfiguration in the target until an operator sets the real password. This must be on the operator's remediation list before apply is approved.

**Webhook signing secrets** are replaced with a temporary dummy value at apply time and must be rotated in Cloud afterwards. Until rotation, the receiving endpoint cannot verify payload authenticity.

**Credential stores** are created in the target as definitions. Their provider secrets never migrate, so a store fronting an external vault must be re-authenticated there before any credential asset bound to it will resolve.

**Artifacts** — config, discovery snapshots, plans, analysis workbooks, staged binaries, and apply results are operator-local. They contain a full tenant inventory. Do not commit them, do not attach them to tickets, and do not paste them into shared channels unless the operator has sanitized them first.

---

## Post-Migration Remediation

Apply completing is not the migration completing. Every apply — canary or full — produces a human-auditable, per-entity report of what succeeded, what failed and with what reason, and what this run never reached; review it immediately, before moving on, not only once the whole migration is done. Hand the operator the outstanding list:

- Review the per-entity audit report; triage every failure by its stated reason before re-running or escalating.
- Set real passwords on every credential asset created with a dummy value.
- Rotate every webhook signing secret.
- Re-authenticate credential stores against their providers, and confirm credential asset bindings resolve.
- Recreate machine keys and re-register robots and machines against the target.
- Resolve every manual-review entity.
- Verify custom role permission sets and reassign users.
- Review migrated triggers before enabling them, so the target does not start unattended production work unannounced.
- Reconcile migrated queue item counts against the source New-state count, and confirm no other item state, job history, or audit history was expected to migrate.
- Clear every remaining item on the report's Hybrid Follow-Ups and Manual Prerequisites sheets.
