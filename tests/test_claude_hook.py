"""Claude Code hooks-artifact invariants.

1. SessionStart group MUST carry matcher "startup|clear" — a matcherless
   group fires on resume/compact too and re-injects the briefing (live-hit
   2026-07-23 on the hand-installed group this artifact replaces).
2. install/remove own only groups whose command runs `trailmem hook`;
   foreign groups in the same event arrays and all other settings keys
   survive untouched.
3. A legacy matcherless trailmem group is upgraded in place.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HOME = os.path.join(tempfile.gettempdir(), "tm-claude-hook-home")
shutil.rmtree(HOME, ignore_errors=True)
os.environ["TRAILMEM_HOME"] = f"{HOME}/.trailmem"
os.environ["TRAILMEM_DB"] = f"{HOME}/.trailmem/trailmem.db"

import json  # noqa: E402

from trailmem.hosts import _util, claude  # noqa: E402

FOREIGN = {"matcher": "startup",
           "hooks": [{"type": "command", "command": "echo hi"}]}
LEGACY = {"hooks": [{"type": "command",
                     "command": "/home/x/.local/bin/trailmem hook "
                                "session-start --agent claude",
                     "timeout": 10}]}


def run() -> None:
    real_home = _util._HOME
    _util._HOME = lambda: Path(HOME)
    try:
        path = Path(HOME) / ".claude" / "settings.json"

        # --- 1. fresh install: both groups, matcher present ---
        assert claude._hook_check() == "not installed"
        assert "startup|clear" in claude.install_hook()
        data = json.loads(path.read_text())
        assert data["hooks"]["SessionStart"][0]["matcher"] == "startup|clear"
        assert "session-stop" in \
            data["hooks"]["SessionEnd"][0]["hooks"][0]["command"]
        assert claude._hook_check() == "installed"
        assert claude.install_hook() == "hooks already installed"

        # --- 2. foreign groups + other settings keys survive ---
        data["hooks"]["SessionStart"].insert(0, FOREIGN)
        data["model"] = "opus"
        path.write_text(json.dumps(data))
        assert claude.install_hook() == "hooks already installed"
        assert claude.remove_hook() is not None
        data = json.loads(path.read_text())
        assert data["hooks"]["SessionStart"] == [FOREIGN]
        assert "SessionEnd" not in data["hooks"], "empty event must be dropped"
        assert data["model"] == "opus"
        assert claude.remove_hook() is None, "second remove is a no-op"

        # --- 3. legacy matcherless group is detected and upgraded ---
        path.write_text(json.dumps({"hooks": {"SessionStart": [LEGACY]}}))
        assert claude._hook_check().startswith("no matcher")
        claude.install_hook()
        data = json.loads(path.read_text())
        ours = [g for g in data["hooks"]["SessionStart"] if claude._is_ours(g)]
        assert len(ours) == 1 and ours[0]["matcher"] == "startup|clear"
        assert claude._hook_check() == "installed"

        # --- 4. malformed settings fail loudly, never silently clobbered ---
        path.write_text("not json {")
        try:
            claude.install_hook()
            raise AssertionError("malformed settings must raise")
        except RuntimeError:
            pass
        assert path.read_text() == "not json {"
    finally:
        _util._HOME = real_home

    print("CLAUDE HOOK OK")


if __name__ == "__main__":
    run()
