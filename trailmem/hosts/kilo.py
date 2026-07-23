"""Kilo — verified host (live self-report 2026-07-23, kilo 7.3.41; schema
cross-checked against https://app.kilo.ai/config.json).

MCP: ~/.config/kilo/kilo.jsonc, top-level key `mcp`, `type: local`, ONE
combined command array, env key `environment`. Schema is STRICT
(additionalProperties: false at root and per-entry) — unknown keys are
rejected. Format is JSONC (comments allowed); patch_json_map falls back to a
manual instruction when the file actually contains comments.

Session identity: NONE reaches trailmem. The conversation id (ses_...) lives
only as the primary key in ~/.local/share/kilo/kilo.db and is never exported
to env or tool payloads — deliberately NOT mined (racy DB scraping, against
the identity contract). KILO_RUN_ID exists but is per-TUI-PROCESS, not
per-conversation (one run can span several conversations and a resumed
conversation gets a new run id) — disproven as a session key, so Kilo runs
stateless. NOTE: Kilo MCP children inherit the FULL parent env (verified via
/proc), including stale vars from before a config edit — the config-entry
`environment` pin is still the only trustworthy attribution, and env changes
need a session restart to reach the server.

No lifecycle hooks, no scriptable statusline, no MCP-prompts-as-commands
(config schema + docs + binary grep all confirm absence). Welcome is
LLM-driven via the usage skill; the save flow is a custom slash command:
a .md file in ~/.config/kilo/command/ (SINGULAR on the live install — docs
say commands/ but the live loader globs command/) surfaces as /<filename>
in the TUI (TUI-only; headless `kilo run` ignores slash invocation).
"""

import shutil

from . import _util
from ._util import Artifact, Host


def _detect():
    return (shutil.which("kilo") is not None
            or (_util._HOME() / ".kilo" / "bin" / "kilo").exists()
            or (_util._HOME() / ".config" / "kilo" / "kilo.jsonc").exists())


def _path():
    return _util._HOME() / ".config" / "kilo" / "kilo.jsonc"


def _entry(cmd, args):
    return {"type": "local", "command": [cmd, *args],
            "environment": {"TRAILMEM_AGENT_TYPE": "kilo"}}


def _tm_save_path():
    return _util._HOME() / ".config" / "kilo" / "command" / "tm-save.md"


HOST = Host(
    "Kilo", "kilo",
    detect=_detect,
    artifacts=[
        _util.json_mcp_artifact(_path, "mcp", _entry, write=True),
        _util.skill_artifact(lambda: _util._HOME() / ".config" / "kilo" / "skills"),
        Artifact("/tm-save command",
                 lambda cmd, args: _util.install_packaged(
                     "commands/tm-save.md", _tm_save_path(), "/tm-save command"),
                 lambda: _util.remove_file(_tm_save_path(), "/tm-save command"),
                 check=_util.file_check(_tm_save_path)),
    ],
    mcp_entry=_entry,
    # session_env: none. KILO_RUN_ID was here as a guess — disproven (per
    # process, not per conversation); the real ses_ id never leaves kilo.db.
)
