"""Command-line entrypoint."""

from __future__ import annotations

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from elsewhere import places, seeds, taxonomy
from elsewhere.taxonomy import REPO_ROOT

# Load .env from the repo root and from pipeline/, without overriding anything
# already exported — an explicit `ANTHROPIC_API_KEY=... elsewhere ...` should
# always win over a checked-out file.
for _env in (REPO_ROOT / ".env", REPO_ROOT / "pipeline" / ".env"):
    load_dotenv(_env, override=False)

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
    live: bool = typer.Option(
        False, "--live", help="Send one real request to prove the batch is accepted."
    ),
) -> None:
    """Check everything is in place before spending money on a batch."""
    import os

    from elsewhere import generate

    if os.environ.get("ANTHROPIC_API_KEY"):
        console.print("[green]✓[/green] ANTHROPIC_API_KEY found")
    else:
        console.print(
            "[yellow]![/yellow] no ANTHROPIC_API_KEY — get one at "
            "https://platform.claude.com/settings/keys and put it in "
            f"{REPO_ROOT / '.env'} (see .env.example)"
        )

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

    if live:
        with console.status("probing with one real request…"):
            ok, detail = generate.probe(source, target)
        if not ok:
            console.print(f"[red]✗ probe failed:[/red] {detail}")
            console.print(
                "[dim]a batch would fail this way 117 times — fix before submitting[/dim]"
            )
            raise typer.Exit(1)
        console.print(f"[green]✓[/green] {detail}")

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


@app.command("setup-key")
def setup_key(
    check: bool = typer.Option(True, "--check/--no-check", help="Verify against the real API."),
) -> None:
    """Store your Anthropic API key securely in .env.

    The key is typed at a hidden prompt: it never appears on screen and never
    lands in shell history, unlike `export ANTHROPIC_API_KEY=...`.
    """
    from elsewhere import secrets

    console.print("\n[bold]Anthropic API key[/bold]")
    console.print("[dim]Get one at https://platform.claude.com/settings/keys[/dim]")
    console.print(
        "[dim]Buy credits first — a key with no credits fails every call. "
        "A Claude.ai subscription does NOT include API credits.[/dim]\n"
    )

    if current := secrets.existing_key():
        console.print(
            f"[yellow]![/yellow] {secrets.ENV_PATH} already has a key: "
            f"[dim]{secrets.redact(current)}[/dim]"
        )
        if not typer.confirm("Replace it?", default=False):
            console.print("[dim]unchanged[/dim]")
            raise typer.Exit(0)

    # hide_input keeps it off the screen; it is never echoed or logged.
    value = typer.prompt("Paste your key", hide_input=True).strip()

    ok, reason = secrets.looks_like_key(value)
    if not ok:
        console.print(f"[red]✗[/red] {reason}")
        raise typer.Exit(1)

    if check:
        with console.status("verifying against the API…"):
            result = secrets.check_key(value)
        if not result.ok:
            console.print(f"[red]✗[/red] {result.detail}")
            if result.needs_credits:
                console.print(
                    "[dim]Add credits at https://platform.claude.com/settings/billing "
                    "then run this again.[/dim]"
                )
            if not typer.confirm("Save it anyway?", default=False):
                raise typer.Exit(1)
        else:
            console.print(f"[green]✓[/green] {result.detail}")

    path = secrets.write_key(value)
    console.print(f"[green]✓[/green] saved to {path} [dim](permissions 600, gitignored)[/dim]")
    console.print("[dim]next: `elsewhere generate preflight --cost`[/dim]")


@app.command("check-key")
def check_key_cmd() -> None:
    """Re-test the stored key without re-entering it.

    Useful after adding credits: the key doesn't change, only the account's
    ability to pay for requests does.
    """
    from elsewhere import secrets

    key = secrets.existing_key()
    if not key:
        console.print("[yellow]no key stored[/yellow] — run `elsewhere setup-key`")
        raise typer.Exit(1)

    console.print(f"[dim]stored: {secrets.redact(key)}[/dim]")
    with console.status("checking…"):
        result = secrets.check_key(key)

    if result.ok:
        console.print(f"[green]✓[/green] {result.detail}")
        console.print("[dim]next: `elsewhere generate preflight --cost`[/dim]")
        return

    console.print(f"[red]✗[/red] {result.detail}")
    if result.needs_credits:
        console.print(
            "[dim]Add credits at https://platform.claude.com/settings/billing, "
            "then run this again. Note a Claude.ai subscription is billed "
            "separately and does not fund API usage.[/dim]"
        )
    raise typer.Exit(1)


@app.command("verify")
def verify_cmd(
    source: str = typer.Option("austin", "--from"),
    target: str = typer.Option("chicago", "--to"),
) -> None:
    """Check generated candidates exist in the places substrate."""
    from elsewhere import generate, verify

    try:
        matches = generate.read_matches(generate.raw_path(source, target))
    except FileNotFoundError:
        console.print("[yellow]no matches yet[/yellow] — run `elsewhere generate collect` first")
        raise typer.Exit(1) from None

    try:
        matches, rejects, report = verify.verify_matches(matches)
    except (RuntimeError, places.StaleTableError) as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1) from exc

    generate.write_matches(matches, verify.verified_path(source, target))
    verify.write_rejects(rejects, verify.rejects_path(source, target))

    console.print(f"[green]✓[/green] {report.summary()}")
    top_ok, top_n = verify.top_candidate_health(matches)
    console.print(f"[dim]top-ranked candidates verified: {top_ok}/{top_n}[/dim]")
    if rejects:
        console.print(
            f"[yellow]![/yellow] {len(rejects)} unverified → "
            f"{verify.rejects_path(source, target).name} [dim](read it — an "
            f"absence in Overture is not proof of a hallucination)[/dim]"
        )


@app.command("ask")
def ask_cmd(
    query: str = typer.Argument(..., help='e.g. "HEB"'),
    source: str = typer.Option("austin", "--from"),
    target: str = typer.Option("chicago", "--to"),
) -> None:
    """Look up the equivalent of a place in another city."""
    from elsewhere import generate, resolve, verify

    hit = resolve.resolve(query, source)
    if hit is None:
        console.print(f"[yellow]no match for[/yellow] {query!r} in {source}")
        if near := resolve.suggest(query, source):
            console.print("[dim]did you mean: " + ", ".join(p.name for p in near) + "?[/dim]")
        raise typer.Exit(1)

    path = verify.verified_path(source, target)
    if not path.exists():
        path = generate.raw_path(source, target)
    try:
        matches = generate.read_matches(path)
    except FileNotFoundError:
        console.print(
            f"[green]✓[/green] resolved to [cyan]{hit.place.name}[/cyan] "
            f"[dim]({hit.how})[/dim]\n[yellow]no matches generated yet[/yellow]"
        )
        raise typer.Exit(1) from None

    match = next((m for m in matches if m.source.name == hit.place.name), None)
    if match is None:
        console.print(f"[yellow]no generated match for {hit.place.name}[/yellow]")
        raise typer.Exit(1)

    console.print(
        f"\n[bold]{hit.place.name}[/bold] ({source.title()}) → [bold]{target.title()}[/bold]"
    )
    console.print(f"[dim]roles: {', '.join(match.role_tags)}[/dim]\n")
    for i, cand in enumerate(match.candidates, 1):
        mark = "" if cand.verified is not False else " [yellow](unverified)[/yellow]"
        style = "bold cyan" if i == 1 else "cyan"
        console.print(f"  {i}. [{style}]{cand.name}[/{style}]{mark}")
        console.print(f"     [dim]{cand.reasoning}[/dim]")


@app.command("eval")
def eval_cmd(
    source: str = typer.Option("austin", "--from"),
    target: str = typer.Option("chicago", "--to"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show every miss."),
) -> None:
    """Score generated matches against ground truth."""
    from elsewhere import evaluate, generate

    truth = evaluate.load_ground_truth()
    if not truth:
        console.print("[yellow]no ground truth[/yellow] — see data/eval/ground_truth.jsonl")
        raise typer.Exit(1)

    try:
        matches = generate.read_matches(generate.raw_path(source, target))
    except FileNotFoundError:
        console.print(
            "[yellow]no matches yet[/yellow] — run `elsewhere generate submit` then `collect`"
        )
        raise typer.Exit(1) from None

    scores = evaluate.score(matches, truth)

    for provenance in ("mined", "provisional"):
        s = scores.get(provenance)
        if not s or not s.total:
            continue
        label = "[green]mined[/green]" if provenance == "mined" else "[yellow]provisional[/yellow]"
        console.print(f"\n{label}  n={s.total}")
        console.print(f"  top-1  {s.top1_rate:6.1%}   [dim](target ≥60%)[/dim]")
        console.print(f"  top-3  {s.top3_rate:6.1%}   [dim](target ≥85%)[/dim]")
        if s.misranked:
            console.print(f"  [dim]{len(s.misranked)} mis-ranked (right answer, wrong order)[/dim]")
        if verbose:
            for name, got, want in s.misranked:
                console.print(
                    f"    [yellow]~[/yellow] {name}: ranked {got} over {' / '.join(want)}"
                )
            for name, got, want in s.wrong:
                console.print(f"    [red]✗[/red] {name}: got {got}, wanted {' / '.join(want)}")
        if s.missing:
            console.print(f"  [dim]{len(s.missing)} in ground truth but not generated[/dim]")

    if not evaluate.is_reportable(scores):
        console.print(
            "\n[yellow]![/yellow] Not a reportable result. The provisional set was "
            "written from the same model knowledge that generates matches, so "
            "scoring against it is circular and flatters the model. Mine ≥30 "
            "real pairs from local subreddits before trusting a number."
        )


@app.command("mining-plan")
def mining_plan_cmd(
    source: str = typer.Option("austin", "--from"),
    target: str = typer.Option("chicago", "--to"),
) -> None:
    """Print the subreddit search matrix for collecting real ground truth."""
    from elsewhere import evaluate

    plan = evaluate.mining_plan(source, target)
    for item in plan:
        console.print(f"r/{item['subreddit']}: [cyan]{item['query']}[/cyan]")
    console.print(f"\n[dim]{len(plan)} searches → {evaluate.stub_mined_file(source, target)}[/dim]")


if __name__ == "__main__":
    app()
