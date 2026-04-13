from __future__ import annotations

import importlib.metadata
import json
import logging
import os
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated, NoReturn

import frontmatter
import typer
from rich.console import Console
from rich.table import Table

from munin.core.config import load as _load_config
from munin.core.db import get_pool as _get_pool
from munin.core.embed import embed as _embed
from munin.core.errors import MuninDBError, MuninEmbedError
from munin.core.ingest import ingest as _ingest
from munin.core.logging import setup_logging as _setup_logging
from munin.core.memory import forget as _forget
from munin.core.memory import list_projects as _list_projects
from munin.core.memory import recall as _recall
from munin.core.memory import remember as _remember
from munin.core.memory import show as _show
from munin.core.scope import current_project as _current_project

logger = logging.getLogger(__name__)

app = typer.Typer(name="munin", help="Local memory store for coding agents.")

completion_app = typer.Typer(name="completion", help="Manage shell completion scripts.")
app.add_typer(completion_app)


def _handle_error(e: Exception) -> NoReturn:
    if isinstance(e, (MuninDBError, MuninEmbedError)):
        component = "database" if isinstance(e, MuninDBError) else "embed server"
        typer.echo(
            f"Error: {component} unreachable: {e}\nHint: run `docker compose up -d`",
            err=True,
        )
        raise typer.Exit(code=2)
    typer.echo(f"Error: {e}", err=True)
    raise typer.Exit(code=1)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"munin {importlib.metadata.version('munin')}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool | None = typer.Option(
        None,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG-level file logging."),
) -> None:
    _setup_logging(verbose=verbose)


@app.command()
def remember(
    content: Annotated[str | None, typer.Argument()] = None,
    project: Annotated[str | None, typer.Option("--project", "-p")] = None,
    scope: Annotated[str | None, typer.Option("--scope", "-s")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", "-t")] = None,
    metadata: Annotated[list[str] | None, typer.Option("--metadata", "-m")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """Store a memory in the local store."""
    if content is None:
        if not sys.stdin.isatty():
            content = sys.stdin.read().strip()

    if not content:
        typer.echo("Error: no content provided", err=True)
        raise typer.Exit(code=1)

    parsed_metadata: dict[str, str] = {}
    if metadata:
        for item in metadata:
            key, _, value = item.partition("=")
            if not key:
                raise typer.BadParameter(f"Invalid metadata format: '{item}'. Expected KEY=VALUE")
            parsed_metadata[key] = value

    resolved_project = project or _current_project()

    try:
        thought_id = _remember(
            content,
            project=resolved_project,
            scope=scope,
            tags=list(tag) if tag else None,
            metadata=parsed_metadata if parsed_metadata else None,
        )
    except Exception as e:
        _handle_error(e)

    if json_output:
        print(json.dumps({"id": str(thought_id), "project": resolved_project}))
    else:
        typer.echo(str(thought_id))


@app.command()
def recall(
    query: str = typer.Argument(..., help="Query to search memories."),
    project: Annotated[
        str | None, typer.Option("--project", "-p", help="Filter by project.")
    ] = None,
    scope: Annotated[str | None, typer.Option("--scope", "-s", help="Filter by scope.")] = None,
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max results.")] = 10,
    threshold: Annotated[float, typer.Option("--threshold", help="Min similarity score.")] = 0.0,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON array.")] = False,
) -> None:
    """Search stored memories by semantic similarity."""
    try:
        results = _recall(query, project=project, scope=scope, limit=limit, threshold=threshold)
    except Exception as e:
        _handle_error(e)

    if json_output:
        print(
            json.dumps(
                [
                    {
                        "id": str(r.id),
                        "content": r.content,
                        "project": r.project,
                        "scope": r.scope,
                        "tags": r.tags,
                        "similarity": r.similarity,
                        "created_at": r.created_at.isoformat(),
                    }
                    for r in results
                ],
                indent=2,
            )
        )
        return

    console = Console()
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", style="dim", width=3)
    table.add_column("sim", width=6)
    table.add_column("project/scope", style="dim")
    table.add_column("content")

    for idx, r in enumerate(results, start=1):
        proj_scope = r.project if not r.scope else f"{r.project}/{r.scope}"
        raw = r.content.replace("\n", " ")
        truncated = raw[:120] + "\u2026" if len(raw) > 120 else raw
        table.add_row(
            str(idx),
            f"{r.similarity:.3f}",
            proj_scope,
            f"[bold]{truncated}[/bold]",
        )

    console.print(table)


@app.command()
def projects(
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON array.")] = False,
) -> None:
    """List all projects with stored memories."""
    try:
        results = _list_projects()
    except Exception as e:
        _handle_error(e)

    if json_output:
        print(json.dumps([{"project": p, "count": c} for p, c in results], indent=2))
        return

    console = Console()
    table = Table(show_header=True, header_style="bold")
    table.add_column("Project")
    table.add_column("Count", justify="right")
    for p, c in results:
        table.add_row(p, str(c))
    console.print(table)


@app.command()
def show(
    thought_id: str = typer.Argument(..., help="ID of the thought to show."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show a stored memory by ID."""
    try:
        thought = _show(thought_id)
    except ValueError:
        typer.echo("Error: thought not found", err=True)
        raise typer.Exit(code=1)
    except Exception as e:
        _handle_error(e)

    if thought is None:
        typer.echo("Error: thought not found", err=True)
        raise typer.Exit(code=1)

    if json_output:
        print(
            json.dumps(
                {
                    "id": str(thought.id),
                    "content": thought.content,
                    "project": thought.project,
                    "scope": thought.scope,
                    "tags": thought.tags,
                    "metadata": thought.metadata,
                    "created_at": thought.created_at.isoformat(),
                    "updated_at": thought.updated_at.isoformat(),
                }
            )
        )
    else:
        typer.echo(f"Id:       {thought.id}")
        typer.echo(f"Project:  {thought.project}")
        typer.echo(f"Scope:    {thought.scope}")
        typer.echo(f"Tags:     {', '.join(thought.tags)}")
        typer.echo(f"Content:  {thought.content}")
        typer.echo(f"Created:  {thought.created_at.isoformat()}")
        typer.echo(f"Updated:  {thought.updated_at.isoformat()}")
        typer.echo(f"Metadata: {json.dumps(thought.metadata)}")


@app.command()
def forget(
    thought_id: str = typer.Argument(..., help="ID of the memory to delete."),
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    """Delete a stored memory by ID."""
    if not yes:
        typer.confirm(f"Delete thought {thought_id}?", abort=True)

    try:
        deleted = _forget(thought_id)
    except ValueError:
        typer.echo("Error: thought not found", err=True)
        raise typer.Exit(code=1)
    except Exception as e:
        _handle_error(e)

    if not deleted:
        typer.echo("Error: thought not found", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Deleted {thought_id}")


def _print_import_summary(imported: int, skipped: int, failed: int, json_output: bool) -> None:
    if json_output:
        print(json.dumps({"imported": imported, "skipped": skipped, "failed": failed}))
    else:
        typer.echo(f"Imported: {imported} | Skipped: {skipped} | Failed: {failed}")


def _import_markdown(folder: Path, json_output: bool) -> None:
    imported = skipped = failed = 0
    for md_file in sorted(folder.glob("*.md")):
        try:
            post = frontmatter.load(str(md_file))
            content = post.content.strip()
            if not content:
                skipped += 1
                continue
            project = post.get("project") or _current_project()
            scope = post.get("scope")
            tags = post.get("tags", [])
            metadata = post.get("metadata", {})
            _remember(
                content,
                project=project,
                scope=scope,
                tags=list(tags) if tags else None,
                metadata=metadata if isinstance(metadata, dict) else None,
            )
            imported += 1
        except Exception as e:
            logger.warning("Failed to import %s: %s", md_file.name, e)
            failed += 1
    _print_import_summary(imported, skipped, failed, json_output)
    if imported == 0:
        raise typer.Exit(code=1)


@app.command(name="import")
def import_cmd(
    path: Annotated[Path, typer.Argument(help="Path to .jsonl file or markdown folder")],
    fmt: Annotated[
        str | None, typer.Option("--format", "-f", help="Force format: jsonl or markdown")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Import memories from a .jsonl file or markdown folder."""
    # Determine format
    if fmt is None:
        if path.is_dir():
            fmt = "markdown"
        elif path.suffix == ".jsonl":
            fmt = "jsonl"
        else:
            typer.echo(
                "Error: cannot detect format; use --format jsonl or --format markdown", err=True
            )
            raise typer.Exit(code=1)

    if fmt == "markdown":
        _import_markdown(path, json_output)
        return

    if fmt != "jsonl":
        typer.echo(f"Error: unknown format '{fmt}'; use jsonl or markdown", err=True)
        raise typer.Exit(code=1)

    if not path.exists():
        typer.echo(f"Error: file not found: {path}", err=True)
        raise typer.Exit(code=1)

    imported = 0
    skipped = 0
    failed = 0

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        _handle_error(e)

    for line in lines:
        if not line.strip():
            continue

        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            typer.echo(f"Warning: skipping invalid JSON line: {line[:80]}", err=True)
            skipped += 1
            continue

        content = row.get("content")
        if not content:
            typer.echo("Warning: skipping row missing 'content' field", err=True)
            skipped += 1
            continue

        proj = row.get("project") or _current_project()
        scope = row.get("scope")
        tags = row.get("tags")
        metadata = row.get("metadata")

        try:
            _remember(
                content,
                project=proj,
                scope=scope,
                tags=list(tags) if tags else None,
                metadata=metadata if isinstance(metadata, dict) else None,
            )
            imported += 1
        except Exception as e:
            typer.echo(f"Warning: failed to store row: {e}", err=True)
            failed += 1

    _print_import_summary(imported, skipped, failed, json_output)

    if imported == 0:
        raise typer.Exit(code=1)


@app.command(name="ingest")
def ingest_cmd(
    sources: Annotated[
        Path | None,
        typer.Option("--sources", help="Path to sources.toml"),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview without storing")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """Ingest knowledge from configured sources in sources.toml."""
    try:
        result = _ingest(sources_path=sources, dry_run=dry_run)
    except Exception as e:
        _handle_error(e)

    if json_output:
        print(
            json.dumps(
                {
                    "files_scanned": result.files_scanned,
                    "chunks_stored": result.chunks_stored,
                    "chunks_skipped": result.chunks_skipped,
                    "failures": result.failures,
                }
            )
        )
    else:
        typer.echo(
            f"Files scanned: {result.files_scanned} | "
            f"Stored: {result.chunks_stored} | "
            f"Skipped: {result.chunks_skipped} | "
            f"Failed: {result.failures}"
        )


@app.command()
def stats(
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON object.")] = False,
) -> None:
    """Show storage statistics."""
    try:
        project_rows = _list_projects()
        total_projects = len(project_rows)

        pool = _get_pool()
        pool.open(wait=True)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM thoughts")
                count_row = cur.fetchone()
                total_thoughts = int(count_row[0]) if count_row else 0
                cur.execute("SELECT pg_total_relation_size('thoughts')")
                size_row = cur.fetchone()
                db_size = int(size_row[0]) if size_row else 0
    except Exception as e:
        _handle_error(e)

    cfg = _load_config()
    embed_url = cfg.embed_url

    try:
        _embed("ping")
        embed_reachable = True
    except MuninEmbedError:
        embed_reachable = False

    if json_output:
        print(
            json.dumps(
                {
                    "total_thoughts": total_thoughts,
                    "total_projects": total_projects,
                    "db_size_bytes": db_size,
                    "embed_url": embed_url,
                    "embed_reachable": embed_reachable,
                },
                indent=2,
            )
        )
        return

    typer.echo(f"Thoughts:        {total_thoughts}")
    typer.echo(f"Projects:        {total_projects}")
    typer.echo(f"DB size:         {db_size:,} bytes")
    typer.echo(f"Embed URL:       {embed_url}")
    typer.echo(f"Embed reachable: {'yes' if embed_reachable else 'no'}")


@completion_app.command()
def install(
    shell: Annotated[str, typer.Option("--shell", help="Shell: bash, zsh, or fish.")] = "zsh",
) -> None:
    """Install shell completion for munin.

    Writes a completion script to the standard shell completion directory and
    prints activation instructions.

    \b
    bash  -> ~/.bash_completion.d/munin.bash
             Activate: source the file from ~/.bashrc
    zsh   -> ~/.zsh/completions/_munin
             Activate: add fpath+=~/.zsh/completions before compinit in ~/.zshrc
    fish  -> ~/.config/fish/completions/munin.fish
             Auto-loaded on next session (no manual step needed)
    """
    from click.shell_completion import get_completion_class
    from typer.main import get_command as _get_click_command

    valid_shells = {"bash", "zsh", "fish"}
    if shell not in valid_shells:
        typer.echo(
            f"Error: unsupported shell '{shell}'. Supported: bash, zsh, fish",
            err=True,
        )
        raise typer.Exit(code=1)

    home = Path.home()
    if shell == "bash":
        script_path = home / ".bash_completion.d" / "munin.bash"
        activation = f"Add to ~/.bashrc:\n  source {script_path}"
    elif shell == "zsh":
        script_path = home / ".zsh" / "completions" / "_munin"
        activation = (
            "Add to ~/.zshrc before compinit:\n"
            "  fpath+=~/.zsh/completions\n"
            "  autoload -Uz compinit && compinit"
        )
    else:
        script_path = home / ".config" / "fish" / "completions" / "munin.fish"
        activation = "Fish auto-loads completions. Open a new terminal to activate."

    comp_class = get_completion_class(shell)
    if comp_class is None:
        typer.echo(f"Error: no completion backend for '{shell}'.", err=True)
        raise typer.Exit(code=1)
    click_cmd = _get_click_command(app)
    comp = comp_class(
        cli=click_cmd,
        ctx_args={},
        prog_name="munin",
        complete_var="_MUNIN_COMPLETE",
    )
    script = comp.source()

    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script, encoding="utf-8")

    typer.echo(f"Written: {script_path}")
    typer.echo(activation)


@app.command()
def doctor(
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """Run self-diagnosis checks on the munin stack."""
    _TIMEOUT = 2.0

    def _check_config_loaded() -> None:
        _load_config()

    def _check_db_reachable() -> None:
        cfg = _load_config()
        pool = _get_pool(cfg)
        if pool.closed:
            pool.open(wait=True)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")

    def _check_schema_present() -> None:
        cfg = _load_config()
        pool = _get_pool(cfg)
        if pool.closed:
            pool.open(wait=True)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'thoughts'")
                if cur.fetchone() is None:
                    raise RuntimeError("thoughts table not found")

    def _check_functions_present() -> None:
        cfg = _load_config()
        pool = _get_pool(cfg)
        if pool.closed:
            pool.open(wait=True)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT proname FROM pg_proc"
                    " WHERE proname IN ('match_thoughts', 'upsert_thought')"
                )
                found = {row[0] for row in cur.fetchall()}
                missing = {"match_thoughts", "upsert_thought"} - found
                if missing:
                    raise RuntimeError(f"Missing functions: {missing}")

    def _check_embed_reachable() -> None:
        _embed("ping")

    def _check_embed_dim_matches() -> None:
        cfg = _load_config()
        vec = _embed("test")
        if len(vec) != cfg.embed_dim:
            raise RuntimeError(f"dim {len(vec)} != config {cfg.embed_dim}")

    def _check_log_dir_writable() -> None:
        log_dir = Path.home() / ".local" / "state" / "munin"
        log_dir.mkdir(parents=True, exist_ok=True)
        test_file = log_dir / ".munin_doctor_test"
        test_file.write_text("ok")
        test_file.unlink()

    _checks: list[tuple[str, Callable[[], None], str]] = [
        ("config_loaded", _check_config_loaded, "check ~/.config/munin/config.toml"),
        ("db_reachable", _check_db_reachable, "run `docker compose up -d`"),
        ("schema_present", _check_schema_present, "apply `sql/*.sql`"),
        (
            "functions_present",
            _check_functions_present,
            "apply `sql/003_match_thoughts.sql` and `sql/004_upsert_thought.sql`",
        ),
        ("embed_reachable", _check_embed_reachable, "run `docker compose up -d`"),
        ("embed_dim_matches", _check_embed_dim_matches, "embed dim mismatch — check config"),
        (
            "log_dir_writable",
            _check_log_dir_writable,
            "check permissions on ~/.local/state/munin/",
        ),
    ]

    results: list[tuple[str, bool, str]] = []
    with ThreadPoolExecutor(max_workers=len(_checks)) as executor:
        futures = {executor.submit(fn): (name, hint) for name, fn, hint in _checks}
        for future, (name, hint) in futures.items():
            try:
                future.result(timeout=_TIMEOUT)
                results.append((name, True, ""))
            except (TimeoutError, Exception):
                results.append((name, False, hint))

    all_passed = all(passed for _, passed, _ in results)

    if json_output:
        print(
            json.dumps(
                {
                    "checks": [{"name": name, "passed": passed} for name, passed, _ in results],
                    "all_passed": all_passed,
                },
                indent=2,
            )
        )
    else:
        no_color = bool(os.environ.get("NO_COLOR"))
        console = Console(no_color=no_color)
        for name, passed, err_hint in results:
            if passed:
                console.print(f"[green]\u2713[/green] {name}")
            else:
                console.print(f"[red]\u2717[/red] {name} \u2014 {err_hint}")

    raise typer.Exit(code=0 if all_passed else 1)
