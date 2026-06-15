#!/usr/bin/env bash
# scripts/migrate.sh — Apply all sql/NNN_*.sql migrations in order.
#
# Strategy:
#   - Tracks applied migrations in a schema_migrations table (idempotent runner).
#   - Uses `podman exec -i` against the munin-postgres container (falls back to
#     docker exec -i, then host psql on port 5433 if neither container runtime
#     has the container). SQL is always fed via stdin or -c — never via -f with a
#     host path, because host paths do not exist inside the container.
#   - Legacy back-fill: if schema_migrations is created for the first time AND a
#     `thoughts` table already exists (DB predating migration tracking), only
#     the pre-tracking baseline migrations 001–005 are recorded as applied.
#     Migrations 006 and later always apply via the normal path, and since
#     those files are idempotent (ADD COLUMN IF NOT EXISTS / CREATE OR REPLACE
#     FUNCTION), re-applying them on an already-migrated DB is a safe no-op.
#   - Exits non-zero and prints the failing file name if any migration errors.
#   - After applying, verifies thoughts table and core RPC functions are present.
#
# Usage:
#   bash scripts/migrate.sh
#   bash scripts/migrate.sh --dry-run   (read-only: shows planned actions, ZERO writes)

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONTAINER="munin-postgres"
DB_USER="munin"
DB_NAME="${MUNIN_PG_DB:-munin}"
HOST_PORT="5433"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_DIR="$(cd "${SCRIPT_DIR}/../sql" && pwd)"
DRY_RUN=false

if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
fi

# ---------------------------------------------------------------------------
# Detect container runtime
# ---------------------------------------------------------------------------

EXEC_CMD=""

if command -v podman &>/dev/null && podman inspect "${CONTAINER}" &>/dev/null 2>&1; then
    EXEC_CMD="podman exec -i ${CONTAINER}"
elif command -v docker &>/dev/null && docker inspect "${CONTAINER}" &>/dev/null 2>&1; then
    EXEC_CMD="docker exec -i ${CONTAINER}"
fi

# psql wrapper — routes through container (stdin) or host psql. All callers pass
# SQL via -c (inline) or piped stdin; never via -f with a host path (host paths
# do not exist inside the container).
run_psql() {
    if [[ -n "${EXEC_CMD}" ]]; then
        ${EXEC_CMD} psql -U "${DB_USER}" -d "${DB_NAME}" "$@"
    elif command -v psql &>/dev/null; then
        PGPASSWORD=munin psql -h localhost -p "${HOST_PORT}" -U "${DB_USER}" -d "${DB_NAME}" "$@"
    else
        echo "ERROR: Neither podman/docker container '${CONTAINER}' is running, nor is 'psql' available on PATH." >&2
        exit 1
    fi
}

# Convenience: scalar query returning a single trimmed value.
psql_scalar() {
    run_psql -t -A -c "$1" 2>/dev/null | tr -d '[:space:]'
}

# ---------------------------------------------------------------------------
# Collect and sort migration files
# ---------------------------------------------------------------------------

mapfile -t MIGRATION_FILES < <(find "${SQL_DIR}" -maxdepth 1 -name '[0-9][0-9][0-9]_*.sql' | sort)

if [[ ${#MIGRATION_FILES[@]} -eq 0 ]]; then
    echo "No migration files found in ${SQL_DIR}" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Inspect current state (read-only)
# ---------------------------------------------------------------------------

migrations_table_exists=$(psql_scalar "SELECT to_regclass('public.schema_migrations') IS NOT NULL;" || echo "f")
thoughts_table_exists=$(psql_scalar "SELECT to_regclass('public.thoughts') IS NOT NULL;" || echo "f")

# ---------------------------------------------------------------------------
# Dry run: read-only. Report planned actions, make ZERO changes.
# ---------------------------------------------------------------------------

if $DRY_RUN; then
    echo "DRY RUN (read-only — no changes will be made)"
    echo ""

    if [[ "${migrations_table_exists}" != "t" ]]; then
        echo "  schema_migrations table: MISSING (would be created)"
        if [[ "${thoughts_table_exists}" == "t" ]]; then
            echo "  Legacy DB detected (thoughts table present): would back-fill"
            echo "  schema_migrations with baseline migrations 001-005 only."
            echo "  Migrations 006+ would then apply via normal path (idempotent)."
            for filepath in "${MIGRATION_FILES[@]}"; do
                filename="$(basename "${filepath}")"
                num="${filename:0:3}"
                if [[ "$((10#${num}))" -gt 5 ]]; then
                    echo "    WOULD APPLY       ${filename} (006+ — not back-filled)"
                    continue
                fi
                echo "    WOULD MARK APPLIED  ${filename}"
            done
        else
            echo "  Fresh DB: would apply all migrations."
            for filepath in "${MIGRATION_FILES[@]}"; do
                echo "    WOULD APPLY  $(basename "${filepath}")"
            done
        fi
    else
        echo "  schema_migrations table: present"
        for filepath in "${MIGRATION_FILES[@]}"; do
            filename="$(basename "${filepath}")"
            already=$(psql_scalar "SELECT COUNT(*) FROM schema_migrations WHERE filename = '${filename}';" || echo "0")
            if [[ "${already}" == "1" ]]; then
                echo "    SKIP         ${filename} (already applied)"
            else
                echo "    WOULD APPLY  ${filename}"
            fi
        done
    fi

    echo ""
    echo "Dry run complete. No changes applied."
    exit 0
fi

# ---------------------------------------------------------------------------
# Bootstrap schema_migrations table (live run only)
# ---------------------------------------------------------------------------

run_psql -v ON_ERROR_STOP=1 -c "
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT        PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);" >/dev/null

# Legacy back-fill: tracking table did not exist before this run, but thoughts
# does. Seed ONLY the pre-tracking baseline migrations (001–005) as applied.
# Migrations 006 and later are intentionally omitted here so that they run
# via the normal apply path below — their SQL files are idempotent
# (ADD COLUMN IF NOT EXISTS / CREATE OR REPLACE FUNCTION), making re-application
# on an already-migrated DB a harmless no-op.
if [[ "${migrations_table_exists}" != "t" && "${thoughts_table_exists}" == "t" ]]; then
    echo "Legacy DB detected (thoughts table present, no migration tracking)."
    echo "Back-filling schema_migrations with baseline migrations (001-005)..."
    for filepath in "${MIGRATION_FILES[@]}"; do
        filename="$(basename "${filepath}")"
        # Only seed the pre-tracking baseline (001 through 005).
        # Extract the numeric prefix (first 3 chars) and compare as integer.
        num="${filename:0:3}"
        if [[ "$((10#${num}))" -gt 5 ]]; then
            break
        fi
        run_psql -v ON_ERROR_STOP=1 -c \
            "INSERT INTO schema_migrations (filename) VALUES ('${filename}') ON CONFLICT DO NOTHING;" \
            >/dev/null
        echo "  MARK APPLIED  ${filename}"
    done
    echo ""
fi

# ---------------------------------------------------------------------------
# Apply migrations
# ---------------------------------------------------------------------------

APPLIED=0
SKIPPED=0

for filepath in "${MIGRATION_FILES[@]}"; do
    filename="$(basename "${filepath}")"

    already_applied=$(psql_scalar "SELECT COUNT(*) FROM schema_migrations WHERE filename = '${filename}';" || echo "0")

    if [[ "${already_applied}" == "1" ]]; then
        echo "  SKIP  ${filename} (already applied)"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    echo "  APPLY ${filename}"

    # Apply the migration via stdin; on failure print file name and exit non-zero.
    if ! run_psql -v ON_ERROR_STOP=1 < "${filepath}"; then
        echo "" >&2
        echo "ERROR: Migration failed: ${filename}" >&2
        exit 1
    fi

    run_psql -v ON_ERROR_STOP=1 -c \
        "INSERT INTO schema_migrations (filename) VALUES ('${filename}') ON CONFLICT DO NOTHING;" \
        >/dev/null

    APPLIED=$((APPLIED + 1))
done

echo ""
echo "Migrations: ${APPLIED} applied, ${SKIPPED} skipped."

# ---------------------------------------------------------------------------
# Verification: thoughts table + RPC functions
# ---------------------------------------------------------------------------

echo ""
echo "Verifying schema..."

VERIFY_OUTPUT=$(run_psql -t -A -c "
SELECT 'table:' || table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name = 'thoughts'
UNION ALL
SELECT 'routine:' || routine_name
FROM information_schema.routines
WHERE routine_schema = 'public'
  AND routine_name IN ('match_thoughts', 'upsert_thought', 'set_updated_at');")

MISSING=()

echo "${VERIFY_OUTPUT}" | grep -q "^table:thoughts$"          || MISSING+=("table: thoughts")
echo "${VERIFY_OUTPUT}" | grep -q "^routine:match_thoughts$"  || MISSING+=("function: match_thoughts")
echo "${VERIFY_OUTPUT}" | grep -q "^routine:upsert_thought$"  || MISSING+=("function: upsert_thought")

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "ERROR: Post-migration verification failed. Missing:" >&2
    for item in "${MISSING[@]}"; do
        echo "  - ${item}" >&2
    done
    exit 1
fi

echo "  OK  thoughts table present"
echo "  OK  match_thoughts() present"
echo "  OK  upsert_thought() present"
echo ""
echo "Migration complete."
