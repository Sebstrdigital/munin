# Munin Home Lab Migration Handover

Date: 2026-06-16

## Decision

Move Munin support services off the MacBook Pro Podman VM and onto the home lab.

Reason: the local Podman VM is currently configured as a shared 8 GiB Linux VM, but its meaningful workload is Munin infrastructure. That competes with local LLM and development work on the MacBook. Munin is background memory infrastructure, so it belongs on an always-on machine with more RAM and disk.

## Current Local State

Observed on the MacBook:

```text
podman-machine-default
VM type: applehv
CPUs: 6
Memory limit: 8 GiB
Disk size: 100 GiB
State: running
```

Running containers:

```text
munin-postgres       docker.io/pgvector/pgvector:pg16   port 5433
munin-llama-embed    ghcr.io/ggml-org/llama.cpp:server  port 8088
munin-llama-rerank   ghcr.io/ggml-org/llama.cpp:server  port 8089
```

Observed memory:

```text
VM memory total:       7.7 GiB
VM memory used:        2.7 GiB
VM memory available:   5.1 GiB

munin-llama-rerank:   ~1.6 GiB
munin-llama-embed:    ~1.5 GiB
munin-postgres:       ~222 MiB
```

Interpretation: Activity Monitor showing roughly 8 GiB for "Virtual Machine Service for VFKIT" reflects the Podman VM configuration. Actual useful memory pressure is mostly the Munin containers, roughly 3-3.5 GiB by container stats.

Disk note:

```text
Podman images: 39.33 GB total, 38.26 GB reclaimable
Podman volumes: 689 MB total, 640 MB reclaimable
```

That is disk cleanup potential, not the main RAM issue.

Follow-up observation on 2026-06-16:

```text
podman-machine-default: 6 CPU, 8 GiB RAM, 100 GB disk, running
munin-postgres: healthy, up 3 days
munin-llama-embed: healthy, up 2 hours
munin-llama-rerank: healthy, up 2 hours

models/: 1.0 GB
pgdata/: 228 MB
thoughts: 4,888
database size: 66 MB
```

Exact local model files:

```text
models/embeddinggemma-300M-Q8_0.gguf      318 MB
models/bge-reranker-v2-m3-Q8_0.gguf      606 MB
models/nomic-embed-text-v1.5.Q4_K_M.gguf  80 MB
```

The active compose file uses `embeddinggemma-300M-Q8_0.gguf` for embeddings and
`bge-reranker-v2-m3-Q8_0.gguf` for reranking. Treat those as canonical unless
there is a deliberate model-quality change.

## Known Munin Service Contract

Indexed Munin repo: `/Users/sebastianstrandberg/work/git/munin`

Compose shape from `docker-compose.yml`:

```yaml
postgres:
  image: pgvector/pgvector:pg16
  container_name: munin-postgres
  ports:
    - "5433:5432"
  environment:
    POSTGRES_USER: munin
    POSTGRES_PASSWORD: munin
    POSTGRES_DB: munin
  volumes:
    - ./pgdata:/var/lib/postgresql/data

llama-embed:
  image: ghcr.io/ggml-org/llama.cpp:server
  container_name: munin-llama-embed
  ports:
    - "8088:8080"
  command includes:
    --embedding
    --pooling mean
    --ctx-size 2048
  volumes:
    - ./models:/models:ro

llama-rerank:
  image: ghcr.io/ggml-org/llama.cpp:server
  container_name: munin-llama-rerank
  ports:
    - "8089:8080"
```

Current code defaults from `src/munin/core/config.py` include local endpoints:

```text
MUNIN_DB_URL
MUNIN_EMBED_URL
MUNIN_RERANK_URL
```

Default database URL is expected to be local Postgres on port `5433`.

Important code paths:

```text
src/munin/core/config.py::load
src/munin/core/embed.py::embed
src/munin/core/embed.py::embed_batch
src/munin/core/db.py::get_pool
src/munin/core/memory.py::remember
src/munin/core/memory.py::recall
src/munin/cli/main.py
src/munin/mcp/server.py
```

## Home Lab Target

Pick an always-on home lab host with enough RAM and disk. Existing memory mentions these possible targets:

```text
devbox: Ubuntu 24.04 unprivileged LXC, 6 CPU, 16 GB RAM, 120 GB disk
cicd.local / 192.168.100.28: Debian 13 VM, 6 vCPU, 8 GB max RAM
```

Preferred target: `devbox`, if it exists and is stable, because 16 GB RAM gives more headroom for embedding/rerank services.

Home lab source material:

```text
/Users/sebastianstrandberg/work/home-lab/Homelab Plans.md
/Users/sebastianstrandberg/work/home-lab/Dua Factory CICD VM.md
```

Live reachability check on 2026-06-16:

```text
Proxmox host: root@debian.local works
CI/CD VM: debian@cicd.local works
devbox: does not resolve from the MacBook
```

Live Proxmox inventory on 2026-06-16:

```text
Host: debian.local
Host IP: 192.168.100.12
OS: Debian 13 / Proxmox VE 9.1.9
CPU: Intel i5-14500T, 20 logical CPUs
RAM: 31 GiB total, 17 GiB available
Root disk: 906 GB, 797 GB available
External disk: /mnt/pve/ssd-5tb, 4.1 TB available

LXC 100: jellyfin, Ubuntu, 2 cores, 2 GB RAM, 16 GB disk, nesting=1
VM 110: dua-factory, Debian, 6 cores, 8 GB max RAM, 80 GB disk, IP 192.168.100.27
VM 120: cicd, Debian, 6 cores, 8 GB max RAM, 200 GB disk, IP 192.168.100.28
```

Note: older homelab notes mention host IP `192.168.100.26` and 64 GB RAM; live
state currently reports `192.168.100.12` and 31 GiB RAM. Treat live Proxmox as
authoritative until the hardware/RAM discrepancy is explained.

CI/CD VM live state:

```text
cicd.local: 192.168.100.28
SSH user: debian
Runtime: podman installed
RAM: 7.8 GiB total, 7.0 GiB available
Disk: 197 GB total, 133 GB available
Running service: zot registry behind Caddy
```

Do not place Munin on the CI/CD VM unless this is only a temporary shakedown.
CI/CD has enough spare capacity, but its build cache, registry, and runner
workloads are operationally separate from memory infrastructure.

## Recommended Deployment Shape

Start with a parallel homelab Munin stack, not an immediate cutover.

Run the home lab services side-by-side with the MacBook stack, import a dump,
point only the local CLI/MCP config at the homelab endpoint for validation, and
leave the MacBook stack untouched until `remember` and `recall` are proven.

Recommended host type: a small dedicated VM.

Rationale:

- Munin is a small stateful service, but it is valuable memory infrastructure.
- Postgres plus two llama.cpp sidecars fit comfortably in 4-8 GB RAM; 8 GB is
  workable, 16 GB is preferred for headroom.
- A VM is simpler and more predictable for Docker/Podman, bind mounts, service
  restarts, and backups.
- An unprivileged LXC is efficient, but container-in-container operation depends
  on host settings such as nesting, user namespaces, fuse-overlayfs, and storage
  driver support.
- The existing LXC is Jellyfin-specific. The existing VMs have clear owners:
  `dua-factory` for the app and `cicd` for registry/build work.

Use LXC if:

- `devbox` already exists, is stable, and can run Docker or Podman cleanly.
- The home lab already backs up the LXC data path.
- You want minimal overhead and accept a little container-runtime setup friction.

Use a VM if:

- You are creating a new host specifically for Munin.
- You want the lowest operational surprise.
- You want clean separation from other homelab services.
- You may later add a Munin API, reverse proxy, metrics, or stricter firewalling.

Initial VM sizing:

```text
Name: munin
DNS: munin.local
VMID: 130, if free
OS: Debian 13 or Ubuntu 24.04 LTS
vCPU: 4 minimum, 6 preferred
RAM: 8 GB minimum, 12-16 GB preferred
Disk: 40 GB minimum, 80-120 GB preferred
Runtime: Docker Compose or Podman Compose
Network: private LAN/VPN only
```

Recommended initial allocation for this homelab:

```text
VMID: 130
Name: munin
OS: Debian 13 minimal
vCPU: 4
RAM: 8 GB max, 4 GB balloon minimum
Disk: 80 GB on internal NVMe
IP/DNS: static DHCP or static IP, `munin.local`
SSH user: debian
Runtime: Podman Compose, matching the local MacBook stack
Service path: /srv/munin
Backup path: /srv/backups/munin or Proxmox backup plus logical pg_dump
```

Run the same three services there with persistent storage:

```text
Postgres data: persistent volume or bind mount
Model files: persistent read-only model directory
Ports: 5433, 8088, 8089, preferably reachable only on private LAN/VPN
Restart policy: unless-stopped
Backups: include Postgres pgdata or scheduled logical dump
```

Security baseline:

```text
Do not expose these ports to the public internet.
Prefer private LAN/VPN access.
Replace default postgres password if the service is reachable beyond localhost.
Document the chosen host, IP/DNS name, data path, and backup path.
```

## Migration Steps

0. Provision the dedicated homelab VM.

Recommended Proxmox target:

```text
VMID: 130
Name: munin
OS: Debian 13 minimal cloud image or installer
CPU: host, 4 cores
RAM: 8192 MB, balloon minimum 4096 MB
Disk: 80 GB on `local` internal NVMe storage
Network: vmbr0
IP: static DHCP reservation or static guest config
DNS: munin.local
User: debian
SSH: install the MacBook public key
QEMU guest agent: enabled
On boot: enabled
```

Do not reuse:

```text
LXC 100 jellyfin: media-specific workload
VM 110 dua-factory: app runtime
VM 120 cicd: registry/build/runner workload
```

1. Prepare the home lab host.

```bash
podman --version || docker --version
sudo mkdir -p /srv/munin/models /srv/munin/pgdata /srv/backups/munin
sudo chown -R "$USER:$USER" /srv/munin /srv/backups/munin
```

2. Copy or recreate the Munin compose file on the home lab.

Use the Munin repo compose file as the starting point:

```bash
cd /path/to/munin
podman compose up -d
```

For a service-only host, the repo does not need to live there permanently. A
minimal `/srv/munin/docker-compose.yml` plus `/srv/munin/models` and
`/srv/munin/pgdata` is enough.

3. Ensure model files exist on the home lab.

```bash
scp models/embeddinggemma-300M-Q8_0.gguf debian@munin.local:/srv/munin/models/
scp models/bge-reranker-v2-m3-Q8_0.gguf debian@munin.local:/srv/munin/models/
```

The old `nomic-embed-text-v1.5.Q4_K_M.gguf` file can be skipped unless a
rollback to the earlier embedding model is desired.

The running local containers currently reference these model roles:

```text
embedding model: /models/embeddinggemma-300M-Q8_0.gguf
rerank model:    /models/bge-reranker-v2-m3-Q8_0.gguf
```

4. Migrate Postgres data.

Preferred logical export/import:

```bash
podman exec munin-postgres pg_dump -U munin -d munin --format=custom --file=/tmp/munin.dump
podman cp munin-postgres:/tmp/munin.dump ./munin.dump
scp ./munin.dump debian@munin.local:/srv/backups/munin/munin-pre-homelab.dump
```

On the home lab after Postgres is running:

```bash
ssh debian@munin.local
cd /srv/munin
podman compose up -d postgres
podman cp /srv/backups/munin/munin-pre-homelab.dump munin-postgres:/tmp/munin.dump
podman exec munin-postgres pg_restore -U munin -d munin --clean --if-exists /tmp/munin.dump
podman compose up -d
```

If using Docker on either side, replace `podman` with `docker`.

5. Point local Munin CLI/MCP clients at the home lab.

Create or update `~/.config/munin/config.toml` on the MacBook:

```toml
db_url = "postgresql://munin:<password>@munin.local:5433/munin"
embed_url = "http://munin.local:8088"
rerank_url = "http://munin.local:8089"
```

Alternatively set environment variables:

```bash
export MUNIN_DB_URL="postgresql://munin:<password>@munin.local:5433/munin"
export MUNIN_EMBED_URL="http://munin.local:8088"
export MUNIN_RERANK_URL="http://munin.local:8089"
```

6. Validate from the MacBook.

```bash
curl -fsS http://munin.local:8088/health
curl -fsS http://munin.local:8089/health
munin projects
munin remember "home lab migration validation thought" --project munin --scope migration --tag validation
munin recall "home lab migration validation" --project munin --limit 3
```

7. Stop local Munin containers after validation.

```bash
podman stop munin-llama-rerank munin-llama-embed munin-postgres
```

If no other Podman workloads need the VM:

```bash
podman machine stop
```

## Required Fallback Behavior

Goal: when Munin is unreachable, agents should not lose thoughts. They should write markdown spool files locally and import them later when Munin is reachable.

Implement in the Munin repo, not just in agent instructions.

Recommended behavior:

```text
If `munin remember ...` cannot reach DB or embedding service:
  create a markdown file in a local spool directory
  print the file path and clear "queued for later import" message
  exit successfully only if the spool write succeeded

If `munin recall ...` cannot reach Munin:
  print a clear unreachable message
  optionally search local spool markdown files as a weak fallback
```

Suggested spool path:

```text
~/.local/share/munin/spool/
```

Suggested filename:

```text
YYYYMMDD-HHMMSS-<project>-<scope-or-general>-<short-hash>.md
```

Suggested markdown format:

```markdown
---
project: munin
scope: migration
tags:
  - offline
  - queued
created_at: 2026-06-16T00:00:00+08:00
source: munin-offline-spool
---

Thought content goes here.
```

Add an import command or reuse the in-progress bulk import work:

```bash
munin import ~/.local/share/munin/spool --project <project> --scope <scope>
```

After successful import, move files to:

```text
~/.local/share/munin/spool/imported/
```

Do not delete queued markdown until import success is confirmed.

## Acceptance Criteria

- Munin services run on the home lab with persistent Postgres data and model files.
- MacBook `munin projects`, `munin remember`, and `munin recall` work while the local Podman VM is stopped.
- Local `podman machine stop` frees the `Virtual Machine Service for VFKIT` memory pressure.
- If the home lab Munin endpoint is offline, `munin remember` writes a markdown spool file instead of losing the thought.
- Queued markdown thoughts can be imported into Munin later.
- The chosen host/IP, service paths, credentials location, and backup approach are documented.

## Open Questions For Next Session

- Which home lab host should own Munin permanently: `devbox` or `cicd.local`?
- Should Postgres be reachable directly from the MacBook, or should the CLI/MCP talk to a small Munin API instead?
- Should the embedding/rerank endpoints be exposed on the LAN, or bound behind a reverse proxy/VPN-only address?
- Which exact embedding model should be canonical: the currently running `embeddinggemma-300M-Q8_0.gguf` or the repo compose default `nomic-embed-text-v1.5.Q4_K_M.gguf`?
- Should local Markdown spool support live in Munin core, the CLI only, the MCP server, or all entry points?
