# Remote client setup (Linux / Omarchy)

Use an existing Munin server; do not start containers or run migrations on the client.
The current deployment uses PostgreSQL `munin.local:5433`, database and user
`munin`, and the HTTP bases `http://munin.local/embed` and
`http://munin.local/rerank`. The client runs locally and connects directly to
these services; MCP is a local stdio process, not a remote HTTP endpoint.

## Install and update

Install Python 3.11+, pipx, Git, GnuPG and password-store (`pass`) using your
distribution's package manager. From the existing Munin checkout, update to the
same current revision as the other clients using your normal Git workflow, then:

```sh
cd /path/to/munin
pipx install -e .
munin --version
```

If already installed, run `pipx reinstall munin` after updating the checkout.
The package currently reports `0.1.0`; this version alone does **not** identify a
revision. Compare `git rev-parse HEAD` across checkouts and update/restart every
client before allowing writes, especially after database migrations. Editable
installs depend on the checkout remaining in place. Do not run the README's
local-service quickstart or migrations against the existing deployment.

## Provision the password store

The Mac has a `pass` entry named `munin/postgres`; its encrypted store changes
may **not yet be pushed**. On the source machine, inspect `pass git status` and
confirm the entry is committed and pushed to the intended private password-store
remote using the store's normal Git workflow before syncing. Never commit the
plaintext password to this repository or print it into a terminal log. Verify
the recipient and remote before pushing; an encrypted store still exposes
filenames and metadata, and anyone with the private key and its passphrase can
decrypt it.

On Linux, securely provision an authorized GPG private key and its passphrase
through your established trusted channel, verify its fingerprint independently,
and import it into your own GPG keyring. Alternatively, arrange for the store
owner to add your public key as a recipient and re-encrypt the store before
syncing. Do not copy private keys through the Munin repository, chat, or an
untrusted remote. Use a working GPG agent and pinentry; a GUI-launched process
must be able to unlock the key without relying on an interactive terminal.

Clone the existing password-store Git repository into `~/.password-store` only
if that directory does not already exist; otherwise use your existing store's
normal Git sync workflow and avoid overwriting local changes. Make sure the
remote actually contains the newly committed entry before pulling. Check access
without displaying the password:

```sh
pass show munin/postgres >/dev/null && echo 'Password entry readable'
```

If this fails, check the store revision, GPG key/recipient and pinentry. Do not
use `pass init` on an existing shared store: it can change its recipients.

## Launch CLI and MCP with per-process credentials

Create a private executable wrapper, for example `~/.local/bin/munin-remote`,
with the following contents. Replace `/home/YOU/.local/bin` with the actual
absolute path containing the pipx-installed `munin` and `munin-mcp` commands
(`command -v munin` and `command -v munin-mcp`). The wrapper reads only the
first line of the pass entry as the PostgreSQL password; it does not persist it.

```bash
#!/usr/bin/env bash
set -euo pipefail
secret=$(pass show munin/postgres)
export PGPASSWORD="${secret%%$'\n'*}"
unset secret
export MUNIN_DB_URL='postgresql://munin@munin.local:5433/munin'
export MUNIN_EMBED_URL='http://munin.local/embed'
export MUNIN_RERANK_URL='http://munin.local/rerank'
case "${1:-}" in
  cli) shift; exec /home/YOU/.local/bin/munin "$@" ;;
  mcp) shift; exec /home/YOU/.local/bin/munin-mcp "$@" ;;
  *) echo 'Usage: munin-remote {cli|mcp} [args...]' >&2; exit 2 ;;
esac
```

Set `chmod 700 ~/.local/bin/munin-remote`. Ensure `pass` and `gpg` are on the
GUI application's PATH, or use their absolute paths in the wrapper. Do not enable
shell tracing (`set -x`), log the environment, or put the password in MCP JSON.
`PGPASSWORD` is inherited by the child process and may be visible to privileged
or same-user processes on some systems; protect the account and avoid collecting
process environments in diagnostics. Environment overrides take precedence over
Munin's TOML configuration.

For a GUI agent that supports stdio MCP, add this to its MCP server settings,
substituting your actual absolute wrapper path (merge with existing entries):

```json
{
  "mcpServers": {
    "munin": {
      "command": "/home/YOU/.local/bin/munin-remote",
      "args": ["mcp"]
    }
  }
}
```

Restart the agent to launch the server. Its configuration format and GPG
pinentry integration depend on the client and desktop. The wrapper supplies its
own environment, so startup does not depend on a shell profile. Keep stdout free
for MCP protocol traffic; diagnostics belong on stderr.

## Validate and troubleshoot

From the client, with private network/VPN connected:

```sh
curl -fsS http://munin.local/embed/health
curl -fsS http://munin.local/rerank/health
~/.local/bin/munin-remote cli projects
```

These checks do not intentionally write memories. `projects` tests database
access; health endpoints check service availability, not embedding quality or
MCP integration. In the agent, confirm the Munin MCP tools appear; do not use
`remember`, `forget`, or a recall as a supposedly read-only test (recall can
update usage metadata).

If `munin.local` does not resolve, check mDNS/DNS or use the server's verified
private address in all three URLs. For connection failures, check VPN, firewall,
port 5433 and the HTTP proxy on the server. Authentication failures require
checking the pass entry and database credentials without printing either. If the
GUI cannot unlock GPG, configure a graphical pinentry and GPG agent for that
session; test the wrapper there. For missing MCP tools, check the agent's MCP
logs for startup errors without exposing environment variables. A healthy
reranker endpoint matters: Munin can otherwise fall back to hybrid-only recall.

## Security

Restrict PostgreSQL and the HTTP endpoints to trusted private LAN/VPN clients;
never expose them directly to the public internet. The listed HTTP URLs do not
encrypt embedding input, queries, or rerank documents in transit. PostgreSQL
transport encryption and server identity verification are not established by
this guide; a private LAN alone does not guarantee confidentiality. Use a
properly authenticated encrypted VPN or configure verified TLS for the services
before using an untrusted network. The database credential grants access under
its PostgreSQL role: treat this client and its agent as trusted with stored data.
