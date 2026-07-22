# Host Discovery — self-report prompt for a new agent host

**Status:** REFERENCE

TrailMem integrates a new host in two steps: (1) the host agent itself runs
this discovery prompt and produces an evidence-backed self-report — the agent
is the best witness of its own session/hook/config mechanism; (2) a maintainer
codifies that report into one `trailmem/hosts/<host>.py` module with paired
install/remove artifacts and tests (see [[host-integration]]). The agent
never writes TrailMem config itself — discovery is read-and-prove, codify is
reproducible code.

Paste everything between the markers into the target agent, run it in a real
session of that agent, and bring back the report.

---

## The prompt (copy from here)

```text
You are helping integrate a persistent-memory MCP server ("trailmem") with
YOUR host application. Produce a self-report about how YOUR host works.

HARD RULES
- EVIDENCE for every claim: a live env dump, the actual file content, an
  official doc URL, or a test you ran right now. Quote it. A claim without
  evidence must be labeled UNVERIFIED — never guess, never fill from training
  data about other tools.
- Do not permanently modify any config. If you create a test entry/file to
  prove a mechanism, show it working, then delete it and say so.
- If a section does not apply to your host, write "NOT SUPPORTED" + how you
  confirmed that (tried it / official docs).
- Answer in English, in the exact section structure below.

SECTION 1 — IDENTITY
- Host name, exact version (run the version command, paste output).
- How an installer can DETECT this host on a machine: binary on PATH, config
  directory, etc. Paste `which`/`ls` proof.

SECTION 2 — SESSION IDENTITY (most important)
- Where does YOUR current conversation/session ID live? Check ALL of:
  (a) environment variables of your main process (dump env, redact secrets,
      show the candidate vars);
  (b) the JSON payload your host pipes on stdin to hooks/statuslines (paste a
      real payload);
  (c) any transcript/log file path that embeds the ID.
- Is the ID stable for the whole conversation, and does it change on a new
  conversation? Prove with two values if you can.
- CRITICAL: when your host spawns an MCP SERVER process, does that child
  process inherit these env vars, or does it get a clean env? If you can read
  /proc/<pid>/environ of a running MCP server, paste the relevant lines.
- How is the current project/workspace directory exposed (env var, payload
  field cwd/project_dir, process cwd)?

SECTION 3 — MCP REGISTRATION
- Exact config file path AND format: JSON / JSONC (comments allowed?) / TOML /
  YAML / database. Paste a REAL existing MCP server entry from your config.
- Exact schema: top-level key (mcpServers / mcp / servers / ...), command as
  string+args or one combined array, env-map key name (env / environment),
  transport field? Does the host REJECT unknown keys (strict schema)?
- Does the host have an OFFICIAL CLI/command to add an MCP server (like
  `claude mcp add`)? If yes, paste its help text — that CLI is preferred over
  file editing.
- Restart/reload needed to pick up a new MCP server?

SECTION 4 — LIFECYCLE HOOKS (needed for the session-start welcome)
- Does your host support hooks/automations that run a SHELL COMMAND on
  events? If yes:
  - The exact registration mechanism: one shared hooks file, per-hook files,
    a settings key, or a UI-only flow. Paste the file path and a real/working
    example entry with its EXACT schema (matcher, command, timeout fields).
  - Which EVENTS exist? Specifically: is there a real SESSION-START event
    (fires once when a conversation starts/resumes)? Is there a real
    SESSION-END event (fires once when the conversation ends)?
  - WARNING: an event that fires after EVERY turn/response ("Stop",
    "response-finished") is NOT a session-end. Say explicitly what each
    candidate event's firing frequency is, and how you know.
  - What does the hook process receive: stdin JSON payload (paste one),
    env vars, arguments? Does the hook process inherit the session env vars
    or get a clean env?
  - Does the hook's stdout get injected into the model's context (needed for
    a welcome briefing) or only shown to the user, or discarded?
  - Do hooks need user approval/trust after registration (a /hooks review
    step)? Registration survives host updates?
- PROVE it: register one throwaway hook that runs `echo trailmem-probe`,
  trigger it, paste the observed behavior, then remove it.

SECTION 5 — SLASH COMMANDS / PROMPTS (needed for a /save command)
- Does your host surface MCP PROMPTS (prompts/list from an MCP server) as
  slash commands? Test against any connected MCP server and state the result.
- Does your host support CUSTOM user-defined commands/prompts from files?
  If yes: exact directory, file format (frontmatter fields?), how it is
  invoked (/name? /prompts:name?). PROVE it: create a dummy command file,
  invoke it, paste the result, delete it.
- If neither works, what is the closest thing (steering rules file, snippet
  system, nothing)?

SECTION 6 — STATUSLINE (optional)
- Is the statusline scriptable (a command the host runs whose stdout is
  displayed)? Config key + exact value shape, refresh frequency, and the
  stdin payload it receives (paste one). If fixed/non-scriptable, say so.

SECTION 7 — SKILLS / RULES FILES (optional)
- Does your host load skill/instruction files from a directory (like
  ~/.claude/skills/<name>/SKILL.md)? Exact path + format + when they load
  (always vs lazy).

SECTION 8 — SUMMARY TABLE
One row per capability: capability | supported? | mechanism | evidence ref |
VERIFIED or UNVERIFIED.
```

## (copy up to here)

---

## What happens with the report

The maintainer maps the report onto a `hosts/<host>.py` module:

- Section 2 → `session_env` / `session_payload` / `project_payload` fields
  and, when the MCP child gets a clean env, the `TRAILMEM_AGENT_TYPE` +
  hook-based context transport (Codex is the reference).
- Section 3 → `mcp_entry` factory + `write=True` only after the pasted schema
  is verified against the live binary (never before — the Kilo/OpenCode
  corruption lesson).
- Section 4 → a SessionStart welcome hook artifact ONLY if a real
  session-start event exists; session-stop only for a real session-end event.
  Per-turn events are never mapped to session boundaries.
- Section 5 → a save-command artifact (`/tm-save`-equivalent) using the
  host's proven custom-command format, or nothing if only MCP prompts work.
- Sections 6–7 → optional statusline / skill artifacts.

Every claim marked UNVERIFIED stays out of code until verified. The module
then goes through the [[host-integration]] Verification Checklist and the
test suites before anything is published.
