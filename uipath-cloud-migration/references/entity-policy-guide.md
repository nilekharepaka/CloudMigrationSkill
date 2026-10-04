# Entity Policy

Dependency order, apply support, and per-entity rules. Concrete syntax for every operation lives in `{SKILL_DIR}/docs/references/cli-reference.md`.

---

## Dependency Order

Plan and apply in this order, always:

| # | Entity | Scope | Depends on |
|---|--------|-------|-----------|
| 1 | folders | Tenant | — |
| 2 | credential stores | Tenant | — |
| 3 | roles | Tenant | — |
| 4 | users | Tenant / org identity | roles |
| 5 | machines | Tenant | folders (for assignment) |
| 6 | robots | Tenant (legacy) | machines, users |
| 7 | environments | Tenant (legacy) | robots |
| 8 | assets | Folder | folders, credential stores |
| 9 | queues | Folder | folders |
| 10 | storage buckets | Folder | folders |
| 11 | calendars | Tenant | — |
| 12 | webhooks | Tenant | the entities whose events they subscribe to |
| 13 | feeds | Tenant | — |
| 14 | settings | Tenant | — |
| 15 | packages | Tenant feed | — |
| 16 | libraries | Tenant / host feed | — |
| 17 | processes | Folder | folders, packages |
| 18 | triggers | Folder | processes, queues, calendars |
| 19 | bucket files | Folder | storage buckets |
| 20 | queue items | Folder | queues |

Rationale: configuration entities run first; packages and libraries are deliberately deferred to the late binary-reconciliation phase; package-dependent processes and triggers follow binary availability; bucket files and queue items are final content work, with queue items last. Packages still precede processes because a process binds to a package version, and triggers follow processes, queues, and calendars because they resolve all three by name.

Reordering causes unresolved-reference failures at apply time and leaves the target half-populated with no bulk undo.

---

## Apply Support

### Cloud-native target

Most writes go through the `uip` CLI. Calendars, credential stores, bucket files and queue items go through the Orchestrator API, because the CLI cannot express the fields they need.

| Entity | Auto-apply | Notes |
|---|---|---|
| folders | Yes | Path, description, feed type, permission model, provision type |
| credential stores | Yes — definition only | Created through the API; provider secrets never travel |
| roles | Custom roles only | Created empty, then permissions granted by a follow-up update |
| users | Only when explicitly enabled | Requires the identity principal to already exist or be importable in the target org |
| machines | Machine templates only | Template and slot allocation; keys do not transfer |
| robots | No — manual review | Legacy construct; needs a modern-folder decision |
| environments | No — manual review | Legacy construct; superseded by folders |
| assets | Yes | Value, scope, description, tags. Credential assets need a target store and get a dummy password |
| queues | Yes | Full definition: retries, SLA, risk SLA, unique reference, encryption, retention. Never queue items |
| storage buckets | Built-in only | External providers carry connection secrets |
| packages | Yes | Requires binaries staged locally first |
| libraries | Tenant-feed only | Host-feed libraries are manual review |
| processes | Yes | Package version, entry point, input arguments, priority, tags, retention |
| calendars | Yes | Name, timezone, and excluded dates — via the API, which the CLI cannot express |
| triggers | Yes — time, queue, and API | Requires processes, queues, and calendars already applied |
| webhooks | Yes | Created with a temporary signing secret; must be rotated |
| feeds | No — manual review | No create API; feed configuration and credentials are environment-specific |
| settings | Non-secret keys only | Keys holding a secret, or pointing at the source deployment, are refused |
| bucket files | Built-in buckets only | Content streamed source-to-target through pre-signed URIs |
| queue items | New-state only | Batched per queue. Re-runs can duplicate without unique reference |

### Direct REST On-Prem target

Narrower set: folders, custom roles, machine templates, assets, queues, packages, processes, calendars.

A plan containing any entity outside this set is refused for a direct REST target. Narrow the entity scope rather than trying to force it.

---

## Per-Entity Rules

**folders** — Matched by fully-qualified path. Parents are created before children. A source folder path that differs from the requested target scope is a rename decision the operator must make explicitly; the engine does not guess.

**credential stores** — The store definition is created in the target through the API: name, type, host name, and additional configuration. The provider secret is never returned by discovery and never travels, so a store fronting an external vault (CyberArk, Azure Key Vault, and similar) has to be re-authenticated in the target before any credential asset bound to it will resolve. An Orchestrator-database store needs no secret, so it migrates outright.

Because a store must exist before credential assets bind to it, stores stay early in the dependency order.

**roles** — Only custom roles with a tenant or folder scope are candidates. Static and built-in roles are excluded because the target already owns equivalents, and recreating them causes permission drift.

A role is created empty and its permissions are granted by a second command keyed on the new role. Two consequences: discovery must return the role's permissions or the role arrives empty (the plan says so, and it lands on the remediation list), and a role whose create succeeds but whose grant fails is a role with no access rather than a missing role. Check the apply results for failures at the follow-up stage, not just the create stage.

**users** — Orchestrator metadata does not carry an identity. A user is applicable only when the principal already resolves in the target organization, or when automatic import is explicitly enabled and identity dependencies are valid. Unconfirmed principals are manual review. Never create identities to make a plan look clean.

**machines** — Machine templates migrate. Classic machines, machine keys, and license allocation do not. Robot-to-machine mappings are re-established in the target as part of post-migration work.

**robots and environments** — Discovered so the operator can size the modernization effort. These are legacy constructs; the correct Cloud answer is usually modern folders plus machine templates, not a like-for-like recreation. Always manual review.

**assets** — Text, integer, boolean, and credential assets migrate with their value, scope, description, and tags.

Per-robot asset values are **not** migrated, and this is deliberate rather than a gap: the API does carry them, but every one references a robot, and robots have no create endpoint at all. Migrating per-robot values would write bindings to robots that cannot exist in the target. They are re-established after robots are re-registered.

Credential assets bind to a credential store in the target. Stores are migrated too, but the binding still has to resolve, so name the target store in the config during intake rather than discovering the problem at apply time. Assets are created with the configured dummy password and reported for correction — a credential asset in the target with a real name and a dummy password is a live misconfiguration until an operator fixes it, so it belongs on the remediation list before apply, not after.

**queues** — The full definition migrates: retries, SLA and risk SLA, unique-reference enforcement, encryption, and both retention policies.

**queue items** — Only items in **New** state migrate, and that restriction is not configurable. Copying an InProgress item would let two tenants work the same transaction; copying a Successful or Failed item would re-process finished work and corrupt reporting. Transaction history never migrates.

Items are batched per queue into one bulk call, with a bounded queue-item batch size of 10 by default. If a bulk call fails, the engine makes one isolated attempt for each item, so one bad item cannot mark its neighbors failed. Every logical item receives exactly one attempt; the exact CLI/API or exception reason is recorded immediately, and later items and entity families continue when continuation is enabled. Two consequences still need stating before apply: a re-run can duplicate items in a queue that does not enforce unique references, and item volume drives apply time more than every other entity combined — a large queue backlog is a scheduling decision, not a detail.

**storage buckets** — Built-in storage buckets migrate, and their **file contents** migrate with them: each file is streamed source-to-target through pre-signed URIs, never through the migration host's disk. External providers (S3, Azure Blob, and similar) carry provider credentials, so both the bucket and its contents stay manual review.

**packages** — Packages are handled in the late binary-reconciliation phase, after configuration entities and before package-dependent processes. Binaries are downloaded from the source feed and staged locally only when the target inventory does not contain the same normalized package ID and exact version. Apply uploads missing staged binaries to the target tenant feed. Version-by-version: an existing version in the target is a skip, not an overwrite. Target matching is reported with the inventory source and skip reason.

**libraries** — Tenant-feed libraries follow the late package path. Before downloading, the engine checks the target for an exact normalized library/package ID plus version; feed IDs are not compared across tenants. A match is reported as `already_in_target` and avoids both source download and target upload. Different versions, incomplete identities, and ambiguous duplicate target keys are not skipped. Host-feed libraries are shared infrastructure in the source deployment and are manual review — recreating them per-tenant in Cloud is a decision, not a copy.

**processes** — A process binds a package version to a folder. It cannot be applied before its package version resolves in the target feed. Entry point, input arguments, priority, tags, auto-update, attended visibility, and retention migrate; execution history does not.

**calendars** — Name, timezone, and excluded dates migrate through the Orchestrator API. The plan records the excluded-date count, and the target calendar must be verified before any dependent trigger is enabled. This verification is a release control, not a cosmetic check.

**triggers** — The highest-risk entity, and the one that most needs the dependency order. A trigger binds to its process, queue, and calendar by *target* key, so all three must already exist; source IDs are meaningless in the target and are never reused.

All three types migrate. Time triggers carry cron, timezone, and stop strategy; queue triggers carry the queue binding, item threshold, job limits, and items-per-job ratio; API triggers carry slug, method, and calling mode. Type is taken from the source record when stated and inferred from the payload when not — a record carrying a queue binding is a queue trigger regardless of what a missing type field implies, because emitting it as a time trigger would create a broken trigger rather than fail.

Enablement is preserved: an enabled source trigger arrives enabled and will begin firing jobs. Review migrated triggers before they do. A migration that silently starts production workloads in a new tenant is worse than one that fails.

**webhooks** — Endpoint URL and event subscriptions migrate. The signing secret does not: a temporary value is set at apply time and must be rotated in Cloud. Until it is rotated, the receiving endpoint cannot trust the payloads.

**feeds** — Assessed and reported. Feeds have no create API and their configuration is environment-specific.

**settings** — Non-secret keys migrate as key/value updates. Anything whose key names a password, API key, secret, token, or connection string is refused, as are the deployment URLs and NuGet keys that point at the source installation — copying those would either leak a source secret into the target or repoint the target at the source's feeds. Refused keys are reported so the operator can set them deliberately.

---

## Applying Only Part of an Entity Family

There is no per-record exclusion. The levers are the entity scope, the folder scope, and the two opt-in switches for user import and external buckets.

So a family containing even one manual-review record cannot be applied at all: apply refuses the whole plan. To migrate the rest of that family, drop it from the entity scope for the automated run and handle those records separately. The analysis workbook's per-entity manual-review count is what tells you which families are clean — read it before choosing scope, not after apply fails.

---

## Matching and Diffing

- Entities are matched between source and target by natural key — fully-qualified folder path, name within folder, or package identifier plus version. Never by source ID.
- An entity that exists in the target is a skip with a reason, not an overwrite. This engine creates; it does not reconcile in place.
- Every skip and every manual-review item must appear in the analysis report the operator approves. A silent skip is a defect.
- Report source and target counts per entity so the operator can spot a folder-scope mistake before apply rather than after.
