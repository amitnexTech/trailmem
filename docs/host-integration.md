# TrailMem Host Integration — agent adapter, session identity, hooks, MCP config

Add a new AI agent host through one auto-discovered adapter module without
teaching TrailMem core about that host's native environment or event format.

**Status:** REFERENCE

Before writing an adapter, have the target agent run the [[host-discovery]]
self-report — its evidence-backed answers (session id source, hook events and
registration schema, command/prompt support) are the input this page codifies.

## Architecture Contract

One host conversation must produce one canonical `SessionContext`, one
namespaced session row, and the same identity on hook and MCP paths:

```text
native host event/env
        |
trailmem/hosts/<host>.py
        |
SessionContext(schema_version=1)
        |
CLI hook / MCP / store / welcome / statusline
```

The adapter emits:

```json
{
  "schema_version": 1,
  "agent_type": "example",
  "session_id": "host-conversation-id",
  "project": "/absolute/project",
  "event": "tool-context",
  "source": "example-adapter"
}
```

Core validates this envelope before using it. `session_id` must be a string or
null and is capped at 512 characters. `event` is null or one of
`session-start`, `session-stop`, and `tool-context`. `source` is a lowercase
slug of at most 80 characters. A malformed canonical envelope is rejected even
on tools that otherwise allow stateless access; it never silently loses identity.

Core owns validation, namespacing, storage, boundary handling, and tool
behavior. A host module owns all native mechanics:

- Host detection and lowercase agent slug.
- Native session environment variable names.
- Native session and project payload keys.
- MCP config shape and `TRAILMEM_AGENT_TYPE` pin.
- Hook installation, update, and surgical removal.
- Host-specific skill, prompt, or command artifacts.

Native variables such as `CODEX_THREAD_ID`, `CLAUDE_CODE_SESSION_ID`, or
`KIRO_SESSION_ID` must never be added to core identity resolution. See
[[schema]] and [[mcp]] for the downstream contracts.

## Facts To Verify Before Coding

Do not infer a host integration from another agent's format. Verify these
facts against the installed host, its official documentation, or captured
event payloads:

1. Stable agent slug and reliable installation/config detection.
2. MCP config path, container key, command shape, and env-map key.
3. Whether the host supplies a real conversation ID to MCP child processes.
4. Native session ID env names and hook payload keys.
5. Project/cwd payload keys and whether paths are absolute.
6. Lifecycle events: real session start/end versus per-turn stop events.
7. Hook stdin/output protocol, matcher syntax, timeout, and trust/restart flow.
8. Config ownership: verified safe auto-write or manual instructions only.

If a mechanic is unverified, leave it unsupported or manual. Never copy a
Claude/Codex/Kiro hook or config shape by analogy.

## Minimal Adapter

Create exactly one file: `trailmem/hosts/example.py`. The registry in
`trailmem/hosts/__init__.py` discovers every non-private module exposing a
`HOST` object; no registry edit is required.

```python
"""Example — detected host; MCP format not yet verified for auto-write."""

from . import _util
from ._util import Host


def _path():
    return _util._HOME() / ".example" / "mcp.json"


def _entry(cmd, args):
    return _util.std_entry("example", cmd, args)


HOST = Host(
    name="Example",
    agent="example",
    detect=lambda: (_util._HOME() / ".example").is_dir(),
    artifacts=[
        _util.json_mcp_artifact(
            _path,
            "mcpServers",
            _entry,
            write=False,
        ),
    ],
    mcp_entry=_entry,
    session_env=("EXAMPLE_SESSION_ID",),
    session_payload=("session_id", "conversationId"),
    project_payload=("cwd", "workspaceRoot"),
)
```

`write=False` is the safe default. It prints the exact manual MCP entry but
does not modify third-party config. Change it to `write=True` only after live
schema verification. The returned artifact records this policy, so shared
tests do not require a host-name allowlist.

If the host cannot expose a stable session ID, omit `session_env` and
`session_payload`. CRUD still works with agent/project attribution, while
welcome and save-awareness intentionally run stateless.

## MCP Registration

Every MCP entry must launch the current environment's interpreter:

```text
<python> -u -m trailmem.mcp_server
```

Every entry must pin:

```text
TRAILMEM_AGENT_TYPE=example
```

Use shared helpers when the verified format matches:

- `std_entry()` for standard command/args/env JSON.
- `json_mcp_artifact()` for a JSON map with paired install/remove.
- `patch_json_map()` only for verified plain JSON.
- `manual_mcp()` for unverified or unsafe-to-write formats.
- A custom `Artifact` when the host provides an official registration CLI,
  TOML, JSONC, or another non-standard format.

Install and remove must be paired in the same `Artifact`. Removal may delete
only TrailMem-owned entries/files and must preserve foreign config.

## Session Identity Mapping

`Host.resolve_context()` applies this precedence:

1. Explicit session ID supplied by the caller.
2. First non-empty verified host payload key.
3. Generic `TRAILMEM_SESSION_ID`.
4. First non-empty verified host-native session env.

For project scope, explicit `TRAILMEM_PROJECT` wins; otherwise the adapter
uses the first verified project payload key and falls back to cwd.

The adapter creates the raw external ID. `SessionContext.key` namespaces it
as `<agent>:<external-id>`. Never namespace inside the host module and never
invent PID, process-parent, CLI, timestamp, or random session IDs.

Canonical `session_context` is authoritative once emitted. Legacy MCP
`agent_type`, `session_id`, and `project` values cannot override it.

## Lifecycle Hooks

Install a start hook only when the host has a real session-start event.
The command should call:

```text
<python> -m trailmem hook session-start --agent example
```

The hook adapter reads native JSON stdin, resolves one context, and invokes
the normal welcome path. Do not inject memory content on every turn.

Install `session-stop` only for a verified session-end event. Never map a
per-turn `Stop`, tool-finished, or response-finished event to session-stop.

When MCP child processes cannot receive the host's conversation ID, add a
targeted pre-tool hook matching only TrailMem MCP calls:

```text
<python> -m trailmem hook tool-context --agent example
```

It must inject the canonical `session_context` into tool input without adding
model-visible memory content. Codex is the reference for this transport;
Kiro is the reference for a dedicated SessionStart hook file. See [[hooks]].

## Optional Host Artifacts

Keep host-specific convenience files in the same module's `artifacts` list:

- Usage skill via `skill_artifact()`.
- Save prompt or slash command via `install_packaged()` and `remove_file()`.
- Dedicated hook files.
- Custom MCP registration/removal functions.

`trailmem integrate` and `trailmem uninstall` iterate the same auto-discovered
artifact list. A new artifact without a paired remover violates the contract.

## Verification Checklist

Add `tests/test_<host>_hook.py` only when the host has custom identity or hook
behavior. A standard generic adapter should pass shared tests without editing
core or host allowlists.

Required checks:

1. Adapter module appears in `HOST_BY_AGENT`.
2. Agent slug is unique and valid.
3. MCP entry pins the correct `TRAILMEM_AGENT_TYPE`.
4. Launch command is `<python> -u -m trailmem.mcp_server`.
5. Native payload/env maps to the expected canonical context and project.
6. Shared table-driven adapter conformance checks pass for the declared fields.
7. Hook and MCP context produce the same namespaced session key.
8. Conflicting legacy identity fields cannot split the session.
9. Missing real ID produces stateless behavior, not a fake row.
10. Install is idempotent and preserves foreign config.
11. Uninstall removes only TrailMem-owned artifacts.
12. Unverified formats remain manual and untouched.

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/test_identity.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/test_phase0.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/test_uninstall.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/test_<host>_hook.py
git diff --check
kh-search lint
graphify update .
```

After installing the package, verify from outside the repository cwd so a
local checkout cannot mask stale site-packages. Restart the host after MCP or
hook changes, and complete any host-specific hook trust flow.

## When Core Changes Are Allowed

A normal host integration must not edit `identity.py`, `mcp_server.py`,
`store.py`, `sessions.py`, `cli.py`, or the registry.

A core change is justified only when the canonical contract itself needs a
new host-independent capability, for example:

- A new versioned `SessionContext` field required by multiple hosts.
- A new generic artifact/helper used by multiple adapters.
- A protocol-wide validation or security rule.
- A lifecycle concept that cannot be represented by start, stop, or targeted
  tool-context events.

One unusual host is not sufficient reason to add its native fields to core.
Keep the exception inside that host module first.

## Review Gate

Before calling an integration complete, report each host mechanic as one of:

- **Verified:** tested against the live host or authoritative format.
- **Manual:** supported through printed configuration, not auto-written.
- **Stateless:** agent/project attribution works, no stable conversation ID.
- **Unsupported:** mechanic is unknown and deliberately not guessed.

The integration is complete only when install and uninstall are reversible,
identity is stable across hook and MCP paths, and a restarted live host uses
the installed package rather than repository-local imports.

## Related

- [[index]]
- [[hooks]]
- [[mcp]]
- [[schema]]
- [[cli]]
