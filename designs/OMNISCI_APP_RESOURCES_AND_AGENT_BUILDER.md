# OmniSci app resources and agent builder

Status: design spike with the first vertical slice implemented.

## Decision

Compute, storage, tool connections, CLI discovery, and agent templates are
application resources. A project records scientific work; it does not own or
duplicate infrastructure credentials. A session selects an agent template and
captures the version it ran.

| Resource | Scope | Contains credentials? | Selected by |
| --- | --- | --- | --- |
| Compute connector | App | Credential reference only | Job/run |
| Storage connector | App | Credential reference only | Job/run |
| Tool connection | App + user identity | Secret-store reference only | Agent template |
| CLI readiness | Execution host | No | Resolved at session start |
| Agent template | App | No | Session/project role |
| Tool approval/policy | Session or project context | No | Runtime policy |

“Sign in once” means one logical connection that every agent can reference. It
does not mean copying a laptop credential to a remote SSH, Slurm, or qsub host.
Host-local CLIs and stdio MCP servers must report readiness per execution host.
Server-proxied HTTP/OAuth connections can be shared without giving their token
to the runner.

## Agent creation interaction

The builder is a split-pane workbench:

1. **Identity** — name and purpose.
2. **Runtime** — default harness and model; sessions may override these.
3. **Capabilities** — built-ins and, once implemented, references to app-level
   connections and skills.
4. **Instructions** — the `AGENTS.md` content.
5. **Generated manifest** — a continuously updated, read-only `config.yaml`
   preview.

Saving creates a durable template in the existing AgentStore and ArtifactStore.
The same template appears in Settings and the new-session picker. Starting a
conversation binds the stored template; it no longer creates a hidden,
session-scoped agent as a side effect.

The live YAML pane is the signature interaction. It preserves YAML as the
portable source of truth without forcing users to write it for routine changes.
An advanced raw source editor/import/export flow can be added later, provided it
round-trips through the same parser and validator.

## Default Science agent and setup

OmniSci ships one built-in `science` template as the first-run agent. It is a
portable scientific behavior layer, not a vendor-specific agent:

- The bundle declares Codex as its initial runtime so a user arriving with a
  ChatGPT/Codex subscription has the shortest path to a working session.
- It does not pin a model. Model identifiers are provider-specific and would
  make a later harness switch invalid.
- The new-session flow keeps the existing per-agent harness override. When the
  selected host explicitly reports Codex as unavailable, Automatic chooses the
  first verifiably ready harness instead. SDKs whose ambient credentials cannot
  be inspected remain explicit choices; unknown readiness does not invent one.
- Settings → Agents exposes the same preference as `Automatic` or an explicit
  runtime. The setting is device-local because host/provider readiness is also
  execution-environment-specific.
- Science sorts before the raw coding harnesses only when the user has no saved
  agent choice. Any explicit agent choice remains sticky.

Biology, chemistry, bioinformatics, literature review, and experimental design
ship as bundled skills. They are specialist lenses inside the same conversation
and use the selected Science runtime. This avoids requiring several vendor
subscriptions, keeps context coherent, and does not spend a second model call
for ordinary domain routing.

True sub-agents remain a later, distinct feature for workflows that benefit
from concurrency or independence, such as a literature scout plus data analyst,
or a primary analysis followed by a separate reviewer. That setup surface must
show each worker's harness readiness and define a fallback rule rather than
silently dropping a missing specialist.

The first-run workflow is:

1. Seed and select Science.
2. Resolve Automatic against the selected host; show the effective runtime and
   readiness in Settings.
3. If no usable runtime is known, direct the user to run `omnigent setup` on
   that host.
4. Start a session with the effective harness override.
5. Load specialist skills in the same session as the question crosses domains.

### Critique of alternatives

- A multi-step wizard hides interactions between harness, tools, and generated
  YAML and makes backtracking expensive. The split pane keeps those effects
  visible.
- Asking for MCP command lines, headers, tokens, and environment values while
  creating an agent confuses capability assignment with connection setup and
  previously wrote credential values into uploaded bundles.
- Treating the first session as the persistence boundary makes custom agents
  disappear from discovery, duplicates them across sessions, and cannot work
  reliably with managed execution targets.
- Making every CLI a checkbox on an agent is misleading. Shell-capable agents
  can see host CLIs subject to sandbox and policy; readiness is not a portable
  property of agent YAML.

## Tool connection interaction

Settings → Tools & connections owns connection lifecycle:

- Add connection.
- Choose provider/transport.
- Sign in or select an existing credential profile.
- Test on the app server and on each connected execution host.
- Disconnect or revoke.

The agent builder only shows connection name, capability summary, scope, and
readiness. Selecting a connection grants a stable reference; it never copies
the connection configuration or token into the agent bundle.

Proposed portable manifest shape:

```yaml
tools:
  builtins:
    - web_search
    - web_fetch
  connections:
    - ref: github-research
      allow:
        - search_code
        - read_file
    - ref: lab-literature
```

The parser should retain these references as unresolved declarations. At
session start the control plane resolves them for the authenticated user and
selected host:

1. Load the immutable agent-template version.
2. Resolve each connection reference against the app registry.
3. Check user access and host readiness.
4. For server-proxied HTTP MCP, keep credentials server-side.
5. For stdio MCP/CLI, send only non-secret launch metadata and use the host's
   own login/profile.
6. Fail before the first turn when a required connection is unavailable.

Resolution results should be visible in the session header and recorded in the
run/session manifest without secret values.

## Storage model for the next slice

```text
ToolConnection
  id                  stable slug used by agent manifests
  display_name
  kind                builtin | cli | mcp_http | mcp_stdio
  transport_config    URL or command/args, never secret values
  credential_ref      reference into user-scoped secret/profile storage
  execution_scope     server | host
  capability_cache    discovered tool names and descriptions
  updated_at

ToolConnectionStatus
  connection_id
  principal_id
  host_id              null for server-scoped connections
  state                ready | sign_in_required | unavailable | error
  checked_at
  error_summary
```

Connection definitions may be shared at workspace/app level, but credential
bindings are per user. This distinction is required before enabling the feature
on multi-user deployments.

## Implemented in this spike

- CLI/tool readiness no longer opens or depends on a science project.
- Settings exposes app-level Agents and Tools & connections categories.
- The visual builder no longer accepts raw MCP headers or environment secrets.
- The builder previews the exact YAML it will package.
- The builder can leave `model` blank so a template follows the selected
  harness's configured default.
- UI-created agents are stored as durable templates through `POST /v1/agents`.
- A built-in, model-unpinned Science agent is seeded as the first-run default,
  with host-aware runtime fallback and five bundled specialist skills.
- Settings → Agents acts as the Science setup/readiness surface.
- Template creation is deliberately limited to explicit local single-user
  runtimes until template ownership and user-scoped connection credentials are
  implemented.

## Next implementation slice

1. Add the ToolConnection and per-user credential-binding stores.
2. Add list/create/test/disconnect APIs with safe summaries only.
3. Extend AgentSpec with connection references and validate missing refs.
4. Resolve references at session launch for the chosen execution host.
5. Add the connection picker and readiness states to the builder.
6. Add optional independent Science workers (search, analysis, review) with
   per-worker readiness and fallback rules.
7. Add template update/delete/version history and YAML import/export.
