from __future__ import annotations

from typing import Optional

import typer

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
    text: str = typer.Argument(..., help="Text to store as a memory."),
) -> None:
    """Store a memory in the local store."""
    typer.echo("not implemented yet")


@app.command()
def recall(
    query: str = typer.Argument(..., help="Query to search memories."),
) -> None:
    """Search stored memories by semantic similarity."""
    typer.echo("not implemented yet")


@app.command()
def projects() -> None:
    """List all projects with stored memories."""
    typer.echo("not implemented yet")


@app.command()
def show(
    project: str = typer.Argument(..., help="Project name to inspect."),
) -> None:
    """Show memories for a specific project."""
    typer.echo("not implemented yet")


@app.command()
def forget(
    thought_id: str = typer.Argument(..., help="ID of the memory to delete."),
) -> None:
    """Delete a stored memory by ID."""
    typer.echo("not implemented yet")


@app.command()
def stats() -> None:
    """Show storage statistics."""
    typer.echo("not implemented yet")
