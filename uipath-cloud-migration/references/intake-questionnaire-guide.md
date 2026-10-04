# Fixed Intake Questionnaire

Ask this intake on every migration or assessment run before any migration command executes. Do not infer URLs, tenant names, folders, entity families, or credential-store mappings. Secrets are never requested or accepted; collect environment-variable names only.

The intake has three blocks, in order. Read the answers back before proceeding. Any missing or ambiguous answer is a stop condition.

## How to ask

- **Choice:** use a structured prompt for small closed sets such as deployment type, migration shape, yes/no policy gates, and target state.
- **Conversation:** use plain text for URLs, tenant names, folder paths, entity families, organization names, authority values, credential-store names, and environment-variable names.
- **Automatic check:** test source reachability and, for direct REST sources, inspect the scopes returned by the token response. Do not ask the operator to self-report either fact.

Batch the discrete choices by block, up to four at a time. Batch the free-text values by block. For direct REST sources, ask the combined External Application/permission question before collecting credential-variable names. After the source details are supplied, run reachability immediately, then request a token with the fixed candidate scope set and report only the granted scope and expiry metadata—not the token.

## Block 1 — source connection

| # | Item | Method | Required handling |
|---|---|---|---|
| 1 | Source deployment type | Choice | `msi_standalone`, `automation_suite`, or `cloud_tenant` |
| 2 | External Application readiness | Choice, direct REST only | One combined prompt: “Does the External Application exist with all of the required permissions?” Display the complete required set before the prompt: `OR.Folders OR.Assets OR.Queues OR.Execution OR.Settings OR.Administration OR.Jobs OR.Users OR.Robots OR.Machines OR.Webhooks OR.License`. A No answer is a hard stop. For a Cloud source, state that the existing `uip` session is used and this gate is not applicable. |
| 3 | Source Orchestrator base URL | Conversation | Host URL only; no `/odata` path or tenant path. |
| 4 | Source tenant name | Conversation | Exact tenant name. For a host-level direct REST probe, temporarily omit the tenant; discovery remains tenant-scoped. |
| 5 | Source Identity URL | Conversation | For direct REST, derive `<orchestrator-url>/identity` and allow an explicit override. For Cloud, use the `uip` session and do not require an Identity URL. |
| 6 | Credential-variable names | Conversation, direct REST only | Names for client ID and client secret, defaulting to `UIP_ONPREM_CLIENT_ID` and `UIP_ONPREM_CLIENT_SECRET`. Never request values. |
| 7 | Authentication | Derived, not a question | `msi_standalone` and `automation_suite` always use `direct_rest`; `cloud_tenant` uses the existing `uip` session. Do not offer an independent auth-mode choice. |
| 8 | Source reachability | Automatic check | Run `check-url` against the supplied base/Identity URL. Any HTTP response proves reachability; DNS, refused connections, and timeouts fail the check. |
| 9 | Granted Orchestrator permissions | Automatic check, direct REST only | Request a token using the fixed candidate scope set. Read back the response `scope` and `expires_in`; never print or store the access token. If the request fails because the full set is not accepted, isolate the missing scope without exposing credentials. |

### Block 1 sequence and hard stops

1. Ask deployment type and, for a direct REST source, the combined External Application readiness question.
2. Ask the base URL, tenant, derived/override Identity URL, and direct REST variable names together. State the derived authentication mode.
3. Immediately run the reachability check. If it fails, do not attempt discovery; create the handover config and provide the operator with the machine-local command sequence to run from a reachable host.
4. If direct REST reachability passes, run the scope/token check and report the granted scopes and expiry metadata. If authentication or scope discovery fails, stop until the app, credentials, or permissions are corrected.
5. For a Cloud source, verify the existing `uip` session during the access-check phase instead of requesting direct REST credentials.

Set credential values only in the operator’s own terminal. If a real secret is pasted into chat, do not run or repeat it; state that it is compromised and must be rotated. When an assistant runs commands in fresh processes, use persistent variables such as `setx` on Windows so the commands can inherit them.

## Block 2 — migration shape and scope

Ask one choice prompt for the first four items, then one conversation prompt for the last two.

| # | Item | Method | Options or format |
|---|---|---|---|
| 10 | Migration shape | Choice | `lift_and_shift`, `folder_subset`, `entity_subset`, or `assessment_only` |
| 11 | Include New-state queue items | Choice | Yes or No |
| 12 | Include built-in bucket file contents | Choice | Yes or No |
| 13 | Rehearsal or production cutover | Choice | Rehearsal or cutover |
| 14 | Folder scope | Conversation | `all discoverable` or exact fully-qualified folder paths |
| 15 | Entity scope | Conversation | `all supported` or exact entity-family names |

For `lift_and_shift`, explicitly read back “every discoverable folder and every supported entity family,” then confirm the folder and entity answers anyway. Resolve named folder paths from discovery; a name that resolves to zero folders is a hard stop. Do not silently narrow a declared lift-and-shift.

## Block 3 — target and policy

Ask one choice prompt for items 16–19, one separate choice for item 20, then one conversation prompt for items 21–24.

| # | Item | Method | Options or format |
|---|---|---|---|
| 16 | Target tenant exists | Choice | Yes or No — not provisioned |
| 17 | Target type | Choice | Automation Cloud or another On-Premises tenant via direct REST |
| 18 | Target population | Choice | Empty or populated |
| 19 | Identity principals | Choice | Yes, No, or unknown |
| 20 | Automatic user import | Choice | Yes or No; default No must be explicitly confirmed |
| 21 | Cloud authority | Conversation | Production default or explicit authority |
| 22 | Target organization and tenant | Conversation | Exact names |
| 23 | Target credential-store binding | Conversation | Store name/key, or `none yet` |
| 24 | First canary size | Conversation | State and confirm the default `minimal` |

If the target is not provisioned, record the remaining target fields as not applicable rather than silently omitting them. A migration run cannot proceed without a named target tenant. Credential assets in scope require a target credential-store binding before validation.

## Stated defaults

State these defaults; do not hide them:

- Credential assets receive a dummy password and require post-migration correction.
- Webhooks receive a temporary signing secret and require rotation.
- External storage buckets remain manual review unless explicitly enabled by policy.
- Non-secret settings may be copied; secret-bearing or source-deployment settings are refused.
- Trigger enablement is preserved, so enabled triggers may fire after apply.
- Request pacing is conservative; apply uses sequential batches of 10 actions by default, and queue-item bulk calls use groups of 10 by default.
- Each logical item gets exactly one attempt; the exact CLI/API or exception reason is reported immediately, and continue-on-error remains enabled so later items can continue.
- Generated configs, snapshots, reports, staged binaries, plans, and apply results remain local and are not committed or shared unsanitized.

## Read back before proceeding

Confirm the deployment, derived authentication, both URLs where applicable, source tenant, External Application readiness, and the automatically verified granted permissions. Confirm reachability or the reachable-host handover. Confirm the migration shape in plain language, resolved folder/entity scope, target organization/tenant or not-provisioned status, credential-store binding, and canary size.

Also state before analysis approval that the analysis report is mandatory and read-only, staging happens only after report approval, apply requires a separate approval and a validated plan, and the canary precedes any larger apply. Explain that source IDs, history, robot session state, real credential passwords, provider secrets, machine keys, and other server-owned values do not transfer.

## Stop conditions

Stop when:

- Any intake item is missing or ambiguous.
- A direct REST source lacks an External Application with the displayed permissions, or scope discovery cannot verify the grant.
- Source reachability fails.
- A named folder resolves to zero records.
- A migration has no named target tenant.
- Credential assets are in scope without a target credential store.
- The operator asks to skip analysis, readiness blockers, validation, the separate apply approval, or the canary.
