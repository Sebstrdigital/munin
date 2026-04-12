# F1: End-to-End psql Smoke Test for SQL Layer

This document describes a reproducible psql session that exercises the full SQL contract: schema, `upsert_thought()`, `match_thoughts()`, and deduplication.

## Prerequisites

1. Docker Compose stack is running:
   ```bash
   docker compose up -d
   ```

2. Both services are healthy:
   ```bash
   docker compose ps
   ```
   Expected: `munin-postgres` and `llama-embed` both show "healthy" status.

3. Migrations 001–004 have been applied. To verify:
   ```bash
   docker exec -i munin-postgres psql -U munin -d munin \
     -c "\dt public.thoughts"
   ```
   Expected: `thoughts` table exists.

## Accessing psql

All commands below assume you are running them via:
```bash
docker exec -it munin-postgres psql -U munin -d munin
```

Or, to pipe SQL from a file:
```bash
cat <<'EOF' | docker exec -i munin-postgres psql -U munin -d munin
  [SQL commands here]
EOF
```

---

## Block 1: Reset

Truncate the thoughts table to start with a clean state. This block is idempotent and safe to re-run.

```sql
TRUNCATE thoughts;
```

Expected output:
```
TRUNCATE TABLE
```

---

## Block 2: Ingestion

Insert three thoughts across two projects (P1 and P2) using hand-written 768-dimensional dummy vectors. `upsert_thought()` will compute the content fingerprint internally and handle deduplication.

```sql
-- P1: First thought with scope 'design'
SELECT upsert_thought(
    'First thought in P1 design',
    array_fill(0.1::real, ARRAY[768])::vector,
    'P1',
    'design'
) as id1;

-- P1: Second thought with scope NULL
SELECT upsert_thought(
    'Second thought in P1 no scope',
    array_fill(0.2::real, ARRAY[768])::vector,
    'P1',
    NULL
) as id2;

-- P2: Third thought with scope NULL
SELECT upsert_thought(
    'Third thought in P2',
    array_fill(0.3::real, ARRAY[768])::vector,
    'P2',
    NULL
) as id3;
```

Expected output: Three UUID values, one per row.

---

## Block 3: Verification

Confirm that exactly three rows exist with correct project and scope values.

```sql
SELECT id, project, scope FROM thoughts ORDER BY project, scope;
```

Expected output:
```
                  id                  | project | scope
--------------------------------------+---------+--------
 [uuid]                               | P1      | design
 [uuid]                               | P1      | 
 [uuid]                               | P2      | 
(3 rows)
```

---

## Block 4: Recall (Project Filtering)

Call `match_thoughts()` twice to verify that the `project` filter works correctly. Each call should return only rows from the specified project.

### P1 Recall

```sql
SELECT project, COUNT(*) as row_count
FROM match_thoughts(
    array_fill(0.15::real, ARRAY[768])::vector,
    'P1',
    NULL,
    10,
    0.0
)
GROUP BY project;
```

Expected output:
```
 project | row_count
---------+-----------
 P1      |         2
(1 row)
```

### P2 Recall

```sql
SELECT project, COUNT(*) as row_count
FROM match_thoughts(
    array_fill(0.35::real, ARRAY[768])::vector,
    'P2',
    NULL,
    10,
    0.0
)
GROUP BY project;
```

Expected output:
```
 project | row_count
---------+-----------
 P2      |         1
(1 row)
```

---

## Block 5: Deduplication

Re-run the full ingestion block (Block 2) without first truncating. The deduplication logic should prevent duplicate rows. Verify that the row count remains 3 and the returned IDs match the original insertion.

```sql
-- P1: First thought with scope 'design' (re-insert)
SELECT upsert_thought(
    'First thought in P1 design',
    array_fill(0.1::real, ARRAY[768])::vector,
    'P1',
    'design'
) as id1;

-- P1: Second thought with scope NULL (re-insert)
SELECT upsert_thought(
    'Second thought in P1 no scope',
    array_fill(0.2::real, ARRAY[768])::vector,
    'P1',
    NULL
) as id2;

-- P2: Third thought with scope NULL (re-insert)
SELECT upsert_thought(
    'Third thought in P2',
    array_fill(0.3::real, ARRAY[768])::vector,
    'P2',
    NULL
) as id3;

-- Verify row count
SELECT COUNT(*) as row_count FROM thoughts;
```

Expected output:
- The three UUIDs returned from `upsert_thought()` should match the UUIDs from Block 2.
- Final row count should be 3 (no new rows inserted).

---

## Summary

This smoke test verifies:

1. ✓ Schema creation with vector embeddings (768-dimensional).
2. ✓ `upsert_thought()` RPC accepts hand-written dummy vectors and computes fingerprints.
3. ✓ `match_thoughts()` correctly filters by project.
4. ✓ Deduplication prevents duplicate insertions (same content, same project = no new row).

All acceptance criteria are satisfied.
