"""Phase-0 Windows-fix invariants.

1. Server launch shape is python -m, never the SAC-blocked trailmem-mcp .exe.
2. Every host entry carries the TRAILMEM_AGENT_TYPE pin (without it every
   store hard-rejects — attribution is config-env only; hosts spawn clean).
3. Old .exe entries upgrade to python -m and KEEP the env pin.
4. A broken onnxruntime degrades to FTS-only: embed() returns None, store and
   query keep working.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HOME = os.path.join(tempfile.gettempdir(), "tm-phase0-home")
shutil.rmtree(HOME, ignore_errors=True)
os.environ["TRAILMEM_HOME"] = HOME
os.environ["TRAILMEM_DB"] = f"{HOME}/trailmem.db"


def run() -> None:
    # --- 1. launch shape ---
    from trailmem import integrate

    cmd, args = integrate.mcp_command()
    assert cmd == sys.executable, cmd
    assert args == ["-u", "-m", "trailmem.mcp_server"], args
    assert "trailmem-mcp" not in " ".join([cmd, *args])

    # `trailmem-mcp` script must be gone from pyproject; `trailmem` must stay.
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    scripts = pyproject.read_text().split("[project.scripts]")[1].split("[")[0]
    assert "trailmem-mcp" not in scripts, "server .exe entry must stay removed"
    assert "trailmem =" in scripts, "CLI script must stay"

    # --- 2. env-pin invariant on every JSON host entry factory ---
    from trailmem import hosts
    from trailmem.hosts import _util as hu

    json_hosts = [h for h in hosts.HOSTS if h.mcp_entry]
    assert json_hosts, "at least one JSON host adapter must exercise the shared helper"
    for h in json_hosts:
        e = h.mcp_entry(cmd, args)
        envmap = e.get("env") or e.get("environment")
        assert envmap and envmap.get("TRAILMEM_AGENT_TYPE"), \
            f"{h.name} entry lost the attribution env pin: {e}"
        flat = e["command"] if isinstance(e["command"], list) \
            else [e["command"], *e.get("args", [])]
        assert flat[0] == sys.executable and "trailmem.mcp_server" in flat, \
            f"{h.name} does not launch python -m: {flat}"

    # --- 3. old-launcher upgrade keeps env pin + user env vars ---
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "mcp.json"
        cfg.write_text(json.dumps({"mcpServers": {"trailmem": {
            "command": "/home/u/.local/bin/trailmem-mcp", "args": [],
            "env": {"TRAILMEM_AGENT_TYPE": "kiro", "CUSTOM": "keep"}}}}))
        msg = hu.patch_json_map(cfg, "mcpServers", hu.std_entry("kiro", cmd, args))
        assert "upgraded" in msg, msg
        e = json.loads(cfg.read_text())["mcpServers"]["trailmem"]
        assert e["command"] == sys.executable and e["args"] == args, e
        assert e["env"]["TRAILMEM_AGENT_TYPE"] == "kiro", e
        assert e["env"]["CUSTOM"] == "keep", "user env vars must survive upgrade"

        # Kilo combined-array shape upgrades without leftover stale keys
        kcfg = Path(td) / "kilo.jsonc"
        kcfg.write_text(json.dumps({"mcp": {"trailmem": {
            "type": "local", "command": ["/home/u/.local/bin/trailmem-mcp"],
            "environment": {"TRAILMEM_AGENT_TYPE": "kilo"}}}}))
        hu.patch_json_map(kcfg, "mcp", hosts.kilo.HOST.mcp_entry(cmd, args))
        ke = json.loads(kcfg.read_text())["mcp"]["trailmem"]
        assert ke["command"] == [cmd, *args], ke
        assert "args" not in ke, "stale keys break strict hosts (Kilo)"
        assert ke["environment"]["TRAILMEM_AGENT_TYPE"] == "kilo", ke

        # up-to-date entry is untouched
        msg2 = hu.patch_json_map(cfg, "mcpServers", hu.std_entry("kiro", cmd, args))
        assert msg2 == "already registered", msg2

        # env map present but pin missing → pin gets added (not "already registered")
        cfg3 = Path(td) / "nopin.json"
        cfg3.write_text(json.dumps({"mcpServers": {"trailmem": {
            "command": cmd, "args": args, "env": {"CUSTOM": "x"}}}}))
        msg3 = hu.patch_json_map(cfg3, "mcpServers", hu.std_entry("cursor", cmd, args))
        assert "env" in msg3, msg3
        e3 = json.loads(cfg3.read_text())["mcpServers"]["trailmem"]
        assert e3["env"] == {"CUSTOM": "x", "TRAILMEM_AGENT_TYPE": "cursor"}, e3

        # --- write policy: unverified hosts never touch their config ---
        real_home = hu._HOME
        hu._HOME = lambda: Path(td)
        try:
            for h in json_hosts:
                mcp_artifact = next(
                    artifact for artifact in h.artifacts
                    if artifact.label == "MCP registration"
                )
                msg = mcp_artifact.install(cmd, args)
                wrote = "not auto-configured" not in msg and "wrote" in msg
                assert wrote == mcp_artifact.auto_writes_config, \
                    f"{h.name} write policy violated: {msg}"
            assert not (Path(td) / ".config" / "zed" / "settings.json").exists(), \
                "manual-policy host config must not be created"

            # --- Claude statusline artifact: write-if-absent, never clobber ---
            sl = next(a for a in hosts.claude.HOST.artifacts
                      if a.label == "statusline")
            settings = Path(td) / ".claude" / "settings.json"
            msg = sl.install(cmd, args)
            assert "wired" in msg, msg
            written = json.loads(settings.read_text())["statusLine"]
            assert written["type"] == "command", written
            assert "-m trailmem statusline --agent claude" in written["command"], written
            assert sl.install(cmd, args) == "statusline already wired"
            assert "removed" in sl.remove()
            assert "statusLine" not in json.loads(settings.read_text())
            # a user's own statusline is kept on install AND on remove
            settings.write_text(json.dumps(
                {"statusLine": {"type": "command", "command": "bash my-line.sh"}}))
            assert "kept" in sl.install(cmd, args)
            assert sl.remove() is None
            assert json.loads(settings.read_text())["statusLine"]["command"] == \
                "bash my-line.sh", "foreign statusline must survive uninstall"

            # --- doctor checks: drift states on a written JSON host (Kilo) ---
            kilo_mcp = next(a for a in hosts.kilo.HOST.artifacts
                            if a.label == "MCP registration")
            assert kilo_mcp.check() == "registered", kilo_mcp.check()
            kilo_cfg = Path(td) / ".config" / "kilo" / "kilo.jsonc"
            data = json.loads(kilo_cfg.read_text())
            del data["mcp"]["trailmem"]["environment"]["TRAILMEM_AGENT_TYPE"]
            kilo_cfg.write_text(json.dumps(data))
            assert "missing TRAILMEM_AGENT_TYPE pin" in kilo_mcp.check()
            data["mcp"]["trailmem"] = {"command": ["/old/bin/trailmem-mcp"]}
            kilo_cfg.write_text(json.dumps(data))
            assert "STALE launcher" in kilo_mcp.check()
            del data["mcp"]["trailmem"]
            kilo_cfg.write_text(json.dumps(data))
            assert kilo_mcp.check() == "not registered"

            # --- Antigravity statusline: same shared artifact, its own path/agent ---
            ag = next(a for a in hosts.antigravity.HOST.artifacts
                      if a.label == "statusline")
            ag_settings = (Path(td) / ".gemini" / "antigravity-cli" / "settings.json")
            assert "wired" in ag.install(cmd, args)
            ag_written = json.loads(ag_settings.read_text())["statusLine"]
            assert "-m trailmem statusline --agent antigravity" in ag_written["command"]
            assert ag.check() == "wired"
            assert "removed" in ag.remove()
            assert ag.check() == "not wired"
        finally:
            hu._HOME = real_home

    # Codex TOML entry must parse even with Windows backslash paths
    import tomllib
    win_cmd = r"C:\Users\ansh\AppData\uv\tools\trailmem\Scripts\python.exe"
    args_toml = "[" + ", ".join(f"'{a}'" for a in args) + "]"
    block = (f"[mcp_servers.trailmem]\ncommand = '{win_cmd}'\n"
             f"args = {args_toml}\n{hosts.codex.ENV_LINE}\n")
    parsed = tomllib.loads(block)["mcp_servers"]["trailmem"]
    assert parsed["command"] == win_cmd and parsed["args"] == args
    assert parsed["env"]["TRAILMEM_AGENT_TYPE"] == "codex"

    # --- 4. broken onnxruntime → FTS-only degrade ---
    # Fake model files make available() true while InferenceSession fails —
    # exactly the Windows DLL-crash shape (files present, runtime dead).
    mdl = Path(HOME) / "models" / "bge-small"
    mdl.mkdir(parents=True, exist_ok=True)
    (mdl / "model.onnx").write_bytes(b"not a real onnx model")
    (mdl / "tokenizer.json").write_text("{}")

    from trailmem import embeddings
    assert embeddings.available()
    assert embeddings.embed("runtime probe text") is None, \
        "broken runtime must degrade, not raise"
    assert embeddings._broken

    from trailmem.schema import connect, init_db
    conn = connect()
    init_db(conn)
    from trailmem.store import store
    r = store(conn, "FTS-only degrade: store must keep working when onnxruntime "
                    "cannot load on this machine.", "Broken-ORT degrade", "lesson",
              agent_type="claude", code_files="none", doc_files="none")
    assert r["outcome"] == "stored", r
    from trailmem.queries import query
    res = query(conn, "degrade onnxruntime")
    assert res and res[0]["id"] == r["id"], res

    # reindex must refuse (probe) instead of dropping the vec table
    from trailmem import models
    assert models.reindex(conn) == 1, "reindex must refuse with a dead runtime"
    conn.close()

    # --- console sym fallback ---
    from trailmem import console
    console._unicode_ok = False
    assert console.sym("✓", "[OK]") == "[OK]"
    console._unicode_ok = True
    assert console.sym("✓", "[OK]") == "✓"
    console._unicode_ok = None

    print("PHASE0 OK")


if __name__ == "__main__":
    run()
