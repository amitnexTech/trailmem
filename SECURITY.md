# Security Policy

## Reporting a Vulnerability

If you find a security issue in trailmem, please report it privately.

- **Preferred:** open a [private security advisory](https://github.com/amitnexTech/trailmem/security/advisories/new) on GitHub.
- Do **not** open a public issue for anything exploitable.

Please include what you found, how to reproduce it, and the version/commit
you tested. You can expect an initial response within about a week. There is
no bug-bounty program; this is a personal open-source project.

## Supported Versions

trailmem is pre-1.0. Only the latest released version on PyPI receives fixes.

| Version | Supported |
|---------|-----------|
| latest  | yes       |
| older   | no        |

## Security Model

trailmem is **local-first and single-user by design**. It is not a
multi-tenant service and makes no attempt to isolate untrusted users from
each other. Its trust boundary is the local machine and the account running
it. Points worth knowing:

- **Data at rest.** Memories live unencrypted in a local SQLite database
  (`~/.trailmem/trailmem.db` by default). Anything with read access to your
  home directory can read them. Do not store secrets, credentials, or
  personal data you would not put in a plaintext file. Content is intended to
  be durable engineering context, not a password vault.

- **MCP transport.** The MCP server speaks **stdio only** — it opens no
  network socket. Each agent host spawns its own process; there is no shared
  network listener to attack.

- **Dashboard.** `trailmem dashboard` binds to **loopback (`127.0.0.1`)
  only** and validates the `Host` and `Origin` headers on every request to
  reject DNS-rebinding and cross-site requests. It ships all assets locally
  (no CDN) and exposes no hard-delete. It is a local UI, not a remote API; do
  not expose the port to a network.

- **Model downloads.** Embedding models are downloaded on demand and verified
  against **pinned SHA-256 checksums** before use. A checksum mismatch aborts
  the install. Custom models installed via `--path` are trusted as supplied
  by the user.

- **`trailmem integrate`.** Writing to an agent host's MCP config is always
  **permission-gated** (an explicit `y/N` prompt), backs up every file it
  touches, and never rewrites a config it cannot parse losslessly.

- **No telemetry.** trailmem sends no analytics and phones no server. The
  only thing it writes outside its database is a local `hooks.log`
  diagnostic.

## Dependencies

trailmem depends on `sqlite-vec`, `onnxruntime`, `tokenizers`, `numpy`,
`orjson`, and `mcp`. Keep them current (`pip install --upgrade trailmem`
pulls compatible versions). Report suspected issues in a dependency to that
project directly, and to us if it affects trailmem's behavior.
