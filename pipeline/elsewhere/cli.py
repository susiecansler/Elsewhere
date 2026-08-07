"""Command-line entrypoint."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from elsewhere import taxonomy

app = typer.Typer(
    help="Find the parallel places in an unfamiliar city.",
    no_args_is_help=True,
    add_completion=False,
)
taxonomy_app = typer.Typer(help="Inspect the role vocabulary.", no_args_is_help=True)
app.add_typer(taxonomy_app, name="taxonomy")

console = Console()


@taxonomy_app.command("list")
def taxonomy_list(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show definitions."),
) -> None:
    """List every role in the vocabulary."""
    try:
        roles = taxonomy.load_roles()
    except taxonomy.TaxonomyError as exc:
        console.print(f"[red]taxonomy error:[/red] {exc}")
        raise typer.Exit(1) from exc

    if not verbose:
        for role in roles:
            console.print(role.id)
        console.print(f"\n[dim]{len(roles)} roles[/dim]")
        return

    table = Table(show_lines=True)
    table.add_column("role", style="cyan", no_wrap=True)
    table.add_column("definition")
    table.add_column("exemplars", style="dim")
    for role in roles:
        table.add_row(
            role.id,
            " ".join(role.definition.split()),
            "\n".join(role.exemplars),
        )
    console.print(table)
    console.print(f"[dim]{len(roles)} roles[/dim]")


@taxonomy_app.command("check")
def taxonomy_check() -> None:
    """Validate the role vocabulary."""
    try:
        roles = taxonomy.load_roles()
    except taxonomy.TaxonomyError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1) from exc

    missing = [r.id for r in roles if not r.exemplars]
    console.print(f"[green]✓[/green] {len(roles)} roles, no duplicates")
    if missing:
        # Not fatal: exemplars steer the model but a role is usable without them.
        console.print(f"[yellow]![/yellow] no exemplars: {', '.join(missing)}")


if __name__ == "__main__":
    app()
