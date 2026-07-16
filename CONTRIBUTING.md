# Contributing to trailmem

Thanks for your interest. trailmem is a small, focused, local-first project;
contributions that keep it that way are very welcome.

## Ground rules

- **The design is the contract.** The full specification lives in
  [`docs/`](docs/index.md) — schema, welcome lifecycle, dedup policy,
  evolution rules, MCP/CLI surfaces, hooks, and the dashboard contract.
  Implement to the spec. If a change alters intended behavior, update the
  relevant `docs/` page in the same pull request.
- **Minimal code.** No premature abstraction, no speculative configuration,
  no cache layer. Three similar lines beat one clever helper. Match the scope
  of the change to the problem.
- **No telemetry, ever.** trailmem never phones home. Any change that emits
  analytics or contacts a remote server will be rejected.

## Development setup

```bash
git clone https://github.com/amitnexTech/trailmem
cd trailmem
python -m venv .venv && . .venv/bin/activate
pip install -e .
python tests/test_smoke.py       # core path (FTS + optional vec)
python tests/test_mcp_e2e.py     # real stdio MCP round-trip
```

`trailmem setup` downloads the default embedding model (checksum-verified)
into `~/.trailmem/`. Use a throwaway `TRAILMEM_HOME=/tmp/...` when testing so
you never touch your real memory database.

## Before you open a pull request

- Run both test scripts; they must pass.
- Keep commits focused and messages descriptive (why, not just what).
- If you add a new MCP agent host to `trailmem integrate`, add it as one row
  in the `JSON_HOSTS` table (or a detect/integrate pair for non-JSON config),
  and test it against a copy of a real config — never overwrite existing
  entries.
- Report security issues privately — see [SECURITY.md](SECURITY.md), not a
  public issue.

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
