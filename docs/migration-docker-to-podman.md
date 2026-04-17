# Migration: Docker → Podman (munin local stack)

**Date:** 2026-04-17
**Status:** Completed 2026-04-17 — rootless podman machine, 4303 thoughts preserved
**Critical constraint:** Zero data loss — 4,288 thoughts in postgres must survive.

## Current State

- Postgres (pgvector/pgvector:pg16) on port 5433, data bind-mounted to `./pgdata`
- llama.cpp embed server on port 8088, models bind-mounted to `./models:ro`
- No Docker-managed volumes — all data on disk
- Compose file: `docker-compose.yml` (standard OCI, should work with Podman)

## Steps

### 1. Backup database
```bash
docker exec munin-postgres pg_dump -U munin -d munin -F c -f /tmp/munin_backup.dump
docker cp munin-postgres:/tmp/munin_backup.dump ./munin_backup_pre_podman.dump
```
Verify backup:
```bash
ls -lh ./munin_backup_pre_podman.dump
```

### 2. Stop Docker containers
```bash
docker compose down
```
Data stays in `./pgdata/` on disk.

### 3. Verify Podman installed
```bash
podman --version
podman compose --version  # or podman-compose --version
```
If missing, install: `brew install podman podman-compose` and `podman machine init && podman machine start`.

### 4. Start with Podman
```bash
podman compose up -d
```
Watch for:
- Rootless permission issues on `./pgdata` — fix with `podman unshare chown`
- Image pull differences (docker.io prefix may be needed)
- Health check curl might need `curl` in container

### 5. Verify data intact
```bash
podman exec munin-postgres psql -U munin -d munin -c "SELECT count(*) FROM thoughts;"
podman exec munin-postgres psql -U munin -d munin -c "SELECT project, count(*) FROM thoughts GROUP BY project ORDER BY count DESC LIMIT 5;"
```
Compare counts with pre-migration values.

### 6. Verify services working
```bash
curl -s http://localhost:8088/health
munin stats
munin recall "test query"
```

### 7. Cleanup (optional, after confidence)
```bash
docker rmi pgvector/pgvector:pg16 ghcr.io/ggml-org/llama.cpp:server
```

### 8. Update CLAUDE.md
- Change "docker compose" references to "podman compose"
- Update README if applicable

## Rollback
If anything goes wrong:
```bash
podman compose down
docker compose up -d
```
Data is on disk bind mount — works with either runtime.

If pgdata corrupted (unlikely):
```bash
# Restore from backup
podman exec -i munin-postgres pg_restore -U munin -d munin -c /tmp/munin_backup.dump
```
