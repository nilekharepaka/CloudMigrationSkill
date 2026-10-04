# Migration Boundaries

What "exact migration" means, what is never migrated, and what the operator owns afterwards. State the relevant parts of this before the operator approves the plan.

---

## What "Exact" Means

Recreating every migratable, target-supported field and relationship as faithfully as the target platform allows.

It does not mean a byte-identical clone. Orchestrator objects carry server-owned state that no API returns and no API accepts on create. A migration recreates definitions; it does not transplant a running system.

---

## Never Migrated

**Identity and secrets**

- Real credential asset passwords
- Credential store provider secrets and connection configuration
- Storage provider credentials for external buckets
- Webhook signing secrets
- Machine keys and robot keys
- Identity-provider identities and their authentication configuration
- API keys, PATs, and External Application secrets

**Data and history**

- Queue items in any state other than New, and all transaction history
- Job and runtime execution history
- Logs
- Audit history
- Robot session state
- Test data queues content

**Server-owned metadata**

- Source entity IDs
- Creation and modification timestamps, and their author fields
- Any read-only server field
- License allocation and consumption state

**Deployment-specific configuration**

- Tenant settings that carry a secret (SMTP password, NuGet API keys) or point at the source deployment (feed URLs)
- Feed configuration and feed credentials
- Host-feed libraries
- Built-in and static roles
- External storage bucket provider configuration
- Classic robots and classic environments as like-for-like objects

---

## Migrated as Definitions Only

These land in the target with their configuration but without their accumulated state:

| Entity | What arrives | What does not |
|---|---|---|
| queues | Definition, retries, SLA and risk SLA, unique reference, encryption, retention | Items in any state but New; all history |
| storage buckets (built-in) | The bucket and its file contents | Nothing |
| storage buckets (external) | Nothing — manual review | Provider config and contents |
| processes | Package binding, entry point, input arguments, priority, tags, retention | Job history |
| assets (credential) | Name, scope, username, target store binding | The password |
| webhooks | URL and event subscriptions | The signing secret |
| triggers | Type, schedule, bindings, thresholds, enablement | Execution history; enablement arrives as-is and should be reviewed |
| machines | The template and its slot allocation | Keys, license allocation, robot registrations |
| custom roles | Permission set, granted after create | User assignments |
| calendars | Name, timezone, and excluded dates | — |
| credential stores | Name, type, host, configuration | The provider secret |
| assets (per-robot values) | Nothing | Values bound to robots, which have no create API |
| settings | Non-secret keys | Secret-bearing keys and source deployment URLs |

---

## Version and Deployment Caveats

- Very old source Orchestrator versions predate parts of the entity surface. Discovery reports what exists; absent entities are not failures.
- On 2018.4 and 2019.4 sources, Organization Units must be enabled before folders resolve at all.
- Automation Suite sources behave like On-Prem for discovery purposes, including the direct REST auth path.
- Automation Cloud targets support the widest auto-apply set. Direct REST On-Prem targets support a narrower set — see [entity-policy-guide.md](entity-policy-guide.md).

---

## Post-Migration Checklist

Hand this to the operator as the closing deliverable of the run. Every unchecked item is a live gap in the target tenant.

**Secrets**

- [ ] Real password set on every credential asset created with a dummy value
- [ ] Every webhook signing secret rotated
- [ ] Credential stores re-authenticated against their provider, and credential asset bindings confirmed to resolve
- [ ] External storage bucket provider configuration recreated
- [ ] Secret-bearing tenant settings set in the target (SMTP password, NuGet API keys)

**Infrastructure**

- [ ] Machine keys generated in the target
- [ ] Robots and machines registered against the target
- [ ] Licenses allocated
- [ ] Feeds configured

**Access**

- [ ] Custom role permission grants confirmed — a create that succeeded with a failed grant leaves a role with no access
- [ ] Users and groups present in the target organization
- [ ] Role assignments recreated
- [ ] Folder-level permissions verified

**Runtime**

- [ ] Migrated triggers reviewed, and enabled deliberately rather than by default
- [ ] Processes test-run in the target before cutover
- [ ] Queue item counts reconciled against the source New-state count
- [ ] Per-robot asset values re-established once robots are registered

**Closure**

- [ ] Every manual-review entity resolved or formally accepted as out of scope
- [ ] Tenant settings reviewed and set
- [ ] Source tenant decommissioning decision made explicitly, not by default
- [ ] Generated artifacts retained locally or deleted per the operator's decision, and not committed
