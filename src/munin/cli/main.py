from __future__ import annotations

import json
import sys
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from munin.core.config import load as _load_config
from munin.core.db import get_pool as _get_pool
from munin.core.embed import embed as _embed
from munin.core.errors import MuninDBError, MuninEmbedError, MuninError
from munin.core.memory import forget as _forget
from munin.core.memory import list_projects as _list_projects
from munin.core.memory import recall as _recall
from munin.core.memory import remember as _remember
from munin.core.memory import show as _show

app = typer.Typer(name="munin", help="Local memory store for coding agents.")


def version_callback(value: bool) -> None:
    if value:
        typer.echo("munin 0.1.0")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
) -> None:
    pass


@app.command()
def remember(
    content: Annotated[Optional[str], typer.Argument(default=None)] = None,
    project: Annotated[Optional[str], typer.Option("--project", "-p")] = None,
    scope: Annotated[Optional[str], typer.Option("--scope", "-s")] = None,
    tag: Annotated[Optional[list[str]], typer.Option("--tag", "-t")] = None,
    metadata: Annotated[Optional[list[str]], typer.Option("--metadata", "-m")] = None,
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
            parsed_metadata[key] = value

    try:
        thought_id = _remember(
            content,
            project=project,
            scope=scope,
            tags=list(tag) if tag else None,
            metadata=parsed_metadata if parsed_metadata else None,
        )
        typer.echo(str(thought_id))
    except (MuninDBError, MuninEmbedError) as e:
        typer.echo(f"Error: {e} (check that docker services are running)", err=True)
        raise typer.Exit(code=2)
    except MuninError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def recall(
    query: str = typer.Argument(..., help="Query to search memories."),
    project: Annotated[Optional[str], typer.Option("--project", "-p", help="Filter by project.")] = None,
    scope: Annotated[Optional[str], typer.Option("--scope", "-s", help="Filter by scope.")] = None,
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max results.")] = 10,
    threshold: Annotated[float, typer.Option("--threshold", help="Min similarity score.")] = 0.0,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON array.")] = False,
) -> None:
    """Search stored memories by semantic similarity."""
    try:
        results = _recall(query, project=project, scope=scope, limit=limit, threshold=threshold)
    except (MuninDBError, MuninEmbedError) as e:
        typer.echo(f"Error: {e} (check that docker services are running)", err=True)
        raise typer.Exit(code=2)
    except MuninError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

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
    except (MuninDBError, MuninEmbedError) as e:
        typer.echo(f"Error: {e} (check that docker services are running)", err=True)
        raise typer.Exit(code=2)

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
    except (MuninDBError, MuninEmbedError) as e:
        typer.echo(f"Error: {e} (check that docker services are running)", err=True)
        raise typer.Exit(code=2)

    if thought is None:
        typer.echo("Error: thought not found", err=True)
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(
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
    except (MuninDBError, MuninEmbedError) as e:
        typer.echo(f"Error: {e} (check that docker services are running)", err=True)
        raise typer.Exit(code=2)

    if not deleted:
        typer.echo("Error: thought not found", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Deleted {thought_id}")


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
    except MuninDBError as e:
        typer.echo(f"Error: {e} (check that docker services are running)", err=True)
        raise typer.Exit(code=2)

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
