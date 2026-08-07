"""Command-line entrypoint."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from elsewhere import places, seeds, taxonomy

app = typer.Typer(
    help="Find the parallel places in an unfamiliar city.",
    no_args_is_help=True,
    add_completion=False,
)
taxonomy_app = typer.Typer(help="Inspect the role vocabulary.", no_args_is_help=True)
seeds_app = typer.Typer(help="Build and inspect the seed corpora.", no_args_is_help=True)
places_app = typer.Typer(help="Manage the Overture places substrate.", no_args_is_help=True)
generate_app = typer.Typer(help="Generate matches via the Batch API.", no_args_is_help=True)
app.add_typer(taxonomy_app, name="taxonomy")
app.add_typer(seeds_app, name="seeds")
app.add_typer(places_app, name="places")
app.add_typer(generate_app, name="generate")

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


@places_app.command("build")
def places_build(
    cities: list[str] = typer.Option(None, "--city", "-c", help="Repeatable. Default: all known."),
    release: str = typer.Option(None, "--release", help="Override the pinned Overture release."),
) -> None:
    """Pull bbox-filtered Overture places into the local DuckDB.

    Reads a few hundred MB over the network; takes a few minutes.
    """
    targets = list(cities) if cities else sorted(places.CITY_BBOXES)
    console.print(
        f"[dim]Overture release {release or places.OVERTURE_RELEASE} → {', '.join(targets)}[/dim]"
    )
    with console.status("querying Overture…"):
        try:
            counts = places.build(targets, release=release)
        except ValueError as exc:
            console.print(f"[red]✗ {exc}[/red]")
            raise typer.Exit(1) from exc

    for city, n in counts.items():
        console.print(f"[green]✓[/green] {city}: {n:,} places")


@places_app.command("status")
def places_status() -> None:
    """Show what's in the local places table."""
    if not places.is_built():
        console.print("[yellow]not built[/yellow] — run `elsewhere places build`")
        raise typer.Exit(1)
    for city, n in places.city_counts().items():
        console.print(f"{city}: {n:,} places")


@seeds_app.command("build")
def seeds_build(
    cities: list[str] = typer.Option(None, "--city", "-c", help="Repeatable. Default: all."),
) -> None:
    """Validate curated seeds, join them to Overture, and write JSONL."""
    targets = list(cities) if cities else seeds.available_cities()
    if not places.is_built():
        console.print(
            "[yellow]![/yellow] no places table — building without verification. "
            "Run `elsewhere places build` first to populate place ids."
        )
    else:
        try:
            places.check_fresh()
        except places.StaleTableError as exc:
            console.print(f"[red]✗ {exc}[/red]")
            raise typer.Exit(1) from exc

    for city in targets:
        try:
            corpus = seeds.load_seeds(city)
        except seeds.SeedsError as exc:
            console.print(f"[red]✗ {city}: {exc}[/red]")
            raise typer.Exit(1) from exc

        for place in corpus:
            hit = places.lookup(place.name, city, place.aliases, place.category)
            if hit:
                place.fsq_place_id = hit.fsq_place_id

        out = seeds.SEEDS_DIR / f"{city}.jsonl"
        seeds.write_jsonl(corpus, out)
        matched, total = seeds.coverage(corpus)
        pct = 100 * matched / total if total else 0
        console.print(
            f"[green]✓[/green] {city}: {total} places → {out.name} "
            f"[dim]({matched}/{total} matched to Overture, {pct:.0f}%)[/dim]"
        )


@seeds_app.command("unmatched")
def seeds_unmatched(
    city: str = typer.Argument(..., help="City slug, e.g. austin"),
) -> None:
    """List seeds with no Overture match.

    Unmatched is a flag, not a failure — Overture's coverage of small local
    businesses is uneven, and these are still valid queries. Read the list to
    tell 'genuinely absent' from 'name needs an alias'.
    """
    try:
        corpus = seeds.read_jsonl(seeds.SEEDS_DIR / f"{city}.jsonl")
    except seeds.SeedsError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1) from exc

    missing = [p for p in corpus if not p.fsq_place_id]
    for place in missing:
        console.print(f"[yellow]·[/yellow] {place.name} [dim]({place.category})[/dim]")
    console.print(f"\n[dim]{len(missing)}/{len(corpus)} unmatched[/dim]")


@generate_app.command("preflight")
def generate_preflight(
    source: str = typer.Option("austin", "--from"),
    target: str = typer.Option("chicago", "--to"),
    cost: bool = typer.Option(False, "--cost", help="Estimate cost (needs API access)."),
) -> None:
    """Check everything is in place before spending money on a batch."""
    from elsewhere import generate

    problems = generate.verify_ready(source, target)
    if problems:
        for p in problems:
            console.print(f"[red]✗[/red] {p}")
        raise typer.Exit(1)

    reqs = generate.build_requests(source, target)
    system = generate.build_system_prompt(target)
    console.print(f"[green]✓[/green] {len(reqs)} requests ready ({source} → {target})")
    console.print(f"[dim]model {generate.MODEL}, effort {generate.EFFORT}[/dim]")
    console.print(f"[dim]cached system prefix: {len(system):,} chars[/dim]")

    if cost:
        try:
            est = generate.estimate_cost(source, target)
        except Exception as exc:
            console.print(f"[yellow]![/yellow] cost estimate unavailable: {exc}")
            raise typer.Exit(0) from None
        console.print(
            f"[dim]~{est['system_tokens']:,} cached tokens/req · "
            f"est. ${est['total_usd_est']:.2f} total (batch-discounted)[/dim]"
        )


@generate_app.command("submit")
def generate_submit(
    source: str = typer.Option("austin", "--from"),
    target: str = typer.Option("chicago", "--to"),
) -> None:
    """Submit the generation batch."""
    from elsewhere import generate

    problems = generate.verify_ready(source, target)
    if problems:
        for p in problems:
            console.print(f"[red]✗[/red] {p}")
        raise typer.Exit(1)

    try:
        batch_id = generate.submit(source, target)
    except Exception as exc:
        console.print(f"[red]✗ submit failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[green]✓[/green] submitted [cyan]{batch_id}[/cyan]")
    console.print("[dim]check with `elsewhere generate status`[/dim]")


@generate_app.command("status")
def generate_status(
    source: str = typer.Option("austin", "--from"),
    target: str = typer.Option("chicago", "--to"),
) -> None:
    """Check batch progress."""
    from elsewhere import generate

    batch_id = generate.load_batch_id(source, target)
    if not batch_id:
        console.print("[yellow]no batch submitted[/yellow]")
        raise typer.Exit(1)

    batch = generate.status(batch_id)
    counts = batch.request_counts
    console.print(f"{batch_id}: [cyan]{batch.processing_status}[/cyan]")
    console.print(
        f"[dim]succeeded {counts.succeeded} · errored {counts.errored} · "
        f"processing {counts.processing}[/dim]"
    )


@generate_app.command("collect")
def generate_collect(
    source: str = typer.Option("austin", "--from"),
    target: str = typer.Option("chicago", "--to"),
) -> None:
    """Fetch batch results and write the raw match corpus."""
    from elsewhere import generate

    batch_id = generate.load_batch_id(source, target)
    if not batch_id:
        console.print("[yellow]no batch submitted[/yellow]")
        raise typer.Exit(1)

    matches, failures = generate.collect(source, target, batch_id)
    out = generate.raw_path(source, target)
    generate.write_matches(matches, out)

    console.print(f"[green]✓[/green] {len(matches)} matches → {out.name}")
    hard = [f for f in failures if not f.get("recoverable")]
    if hard:
        console.print(f"[red]✗[/red] {len(hard)} failed:")
        for f in hard[:10]:
            console.print(f"  [dim]{f.get('name', f['custom_id'])}: {f['error']}[/dim]")
    soft = [f for f in failures if f.get("recoverable")]
    if soft:
        console.print(f"[yellow]![/yellow] {len(soft)} with dropped role tags")


if __name__ == "__main__":
    app()
