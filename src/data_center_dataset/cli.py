"""Command-line interface."""

from __future__ import annotations

import json
import logging

import pandas as pd
import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from . import pipeline
from .config import PROCESSED_DIR, ensure_dirs, processed_dir
from .http import CachedClient
from .states import STATES
from .states import get as get_state_profile

app = typer.Typer(
    add_completion=False,
    help=(
        "Build a dataset of US data centers with tiered power estimates. "
        f"Supported states: {', '.join(sorted(STATES))}."
    ),
)
console = Console()

STATE_OPTION = typer.Option("CA", "--state", "-s", help="Two-letter state code.")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )


def _validate_state(state: str) -> str:
    try:
        return get_state_profile(state).code
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@app.command()
def fetch(
    state: str = STATE_OPTION,
    refresh: bool = typer.Option(False, help="Bypass caches and re-download."),
    with_ceqanet: bool = typer.Option(
        False, help="Also crawl CEQAnet for attested MW figures (CA only, slow, low recall)."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Download and snapshot all sources for a state without building outputs."""
    _setup_logging(verbose)
    state = _validate_state(state)
    ensure_dirs()
    with CachedClient() as client:
        result = pipeline.ingest(
            client, state=state, refresh=refresh, with_ceqanet=with_ceqanet
        )
    console.print(f"[green]Ingested {len(result['records'])} source records for {state}.[/green]")


@app.command()
def build(
    state: str = STATE_OPTION,
    refresh: bool = typer.Option(False, help="Bypass caches and re-download."),
    with_ceqanet: bool = typer.Option(
        False, help="Include CEQAnet Tier A crawl (CA only)."
    ),
    apply_calibration: bool = typer.Option(
        False,
        help="Rescale non-attested tiers to the statewide anchor when out of range.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run the full pipeline and write all outputs for a state."""
    _setup_logging(verbose)
    state = _validate_state(state)
    built = pipeline.run(
        state=state,
        refresh=refresh,
        with_ceqanet=with_ceqanet,
        apply_calibration=apply_calibration,
    )
    _summarise(built)


@app.command()
def build_all(
    refresh: bool = typer.Option(False, help="Bypass caches and re-download."),
    apply_calibration: bool = typer.Option(False, help="See `build --help`."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run the full pipeline for every supported state, one after another."""
    _setup_logging(verbose)
    summary = Table(title="All states")
    summary.add_column("State")
    summary.add_column("Facilities", justify="right")
    summary.add_column("With power est.", justify="right")
    summary.add_column("Sum IT MW", justify="right")
    summary.add_column("Within anchor?")

    for code in sorted(STATES):
        console.rule(f"[bold]{get_state_profile(code).name} ({code})[/bold]")
        try:
            built = pipeline.run(
                state=code, refresh=refresh, apply_calibration=apply_calibration
            )
        except Exception as exc:
            console.print(f"[red]{code} failed: {exc}[/red]")
            summary.add_row(code, "-", "-", "-", "ERROR")
            continue
        fac = built["facilities"]
        recon = built.get("reconciliation") or {}
        summary.add_row(
            code,
            str(len(fac)),
            str(int(pd.to_numeric(fac.get("best_power_mw"), errors="coerce").notna().sum())),
            f"{recon.get('bottom_up_it_load_mw', 0):,.0f}",
            "yes" if recon.get("within_anchor_range") else "no",
        )

    console.rule("[bold]Summary[/bold]")
    console.print(summary)


@app.command()
def validate(
    state: str = STATE_OPTION, verbose: bool = typer.Option(False, "--verbose", "-v")
) -> None:
    """Check the published tables against their schema contracts."""
    _setup_logging(verbose)
    state = _validate_state(state)
    from .normalize.schema import FacilitySchema, PowerEstimateSchema

    outdir = processed_dir(state)
    facilities = pd.read_csv(outdir / "facilities.csv")
    estimates = pd.read_csv(outdir / "power_estimates.csv")

    failures = []
    for name, df, schema in (
        ("facilities", facilities, FacilitySchema),
        ("power_estimates", estimates, PowerEstimateSchema),
    ):
        try:
            schema.validate(df, lazy=True)
            console.print(f"[green]OK[/green]  {name} ({len(df)} rows)")
        except Exception as exc:
            failures.append(name)
            console.print(f"[red]FAIL[/red] {name}")
            console.print(str(exc)[:3000])

    if failures:
        raise typer.Exit(code=1)


@app.command(name="map")
def map_(
    state: str = STATE_OPTION,
    output: str = typer.Option(
        None, "--output", "-o", help="Destination HTML path."
    ),
    open_browser: bool = typer.Option(
        False, "--open", help="Open the map in a browser when done."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Render an interactive satellite map of the facilities."""
    _setup_logging(verbose)
    state = _validate_state(state)
    from pathlib import Path

    from . import viz

    outdir = processed_dir(state)
    if not (outdir / "facilities.csv").exists():
        console.print(f"[red]No outputs found for {state}. Run `build --state {state}` first.[/red]")
        raise typer.Exit(code=1)

    path = viz.write_map(
        Path(output) if output else None, state=state, processed_dir=outdir
    )
    console.print(f"[green]Wrote[/green] {path}")

    if open_browser:
        import webbrowser

        webbrowser.open(path.resolve().as_uri())


@app.command()
def charts(
    state: str = STATE_OPTION,
    outdir: str = typer.Option(None, "--outdir", "-o", help="Destination directory."),
    fetch_history: bool = typer.Option(
        True,
        "--fetch-history/--no-fetch-history",
        help="Harvest OSM element history for the provenance figure.",
    ),
    refresh: bool = typer.Option(False, help="Bypass caches."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Render the analysis figures as PNG and SVG."""
    _setup_logging(verbose)
    state = _validate_state(state)
    from pathlib import Path

    from . import charts as charts_mod
    from .sources import osm_history

    state_processed = processed_dir(state)
    if not (state_processed / "facilities.csv").exists():
        console.print(f"[red]No outputs found for {state}. Run `build --state {state}` first.[/red]")
        raise typer.Exit(code=1)

    history = None
    if fetch_history:
        with CachedClient(min_interval=1.05) as client:
            try:
                history = osm_history.fetch(client, state=state, refresh=refresh)
            except Exception as exc:
                console.print(f"[yellow]OSM history unavailable: {exc}[/yellow]")

    written = charts_mod.build_all(
        Path(outdir) if outdir else (state_processed / "figures"),
        history=history,
        state=state,
        processed_dir=state_processed,
    )
    console.print(f"[green]Wrote {len(written)} files[/green]")
    for path in written:
        if path.suffix == ".png":
            console.print(f"  {path}")


@app.command()
def report(state: str = STATE_OPTION) -> None:
    """Print a summary of the built dataset."""
    state = _validate_state(state)
    outdir = processed_dir(state)
    path = outdir / "facilities.csv"
    if not path.exists():
        console.print(f"[red]No outputs found for {state}. Run `build --state {state}` first.[/red]")
        raise typer.Exit(code=1)
    facilities = pd.read_csv(path)
    recon_path = outdir / "reconciliation.json"
    recon = json.loads(recon_path.read_text()) if recon_path.exists() else {}
    _summarise({"state": state, "facilities": facilities, "reconciliation": recon})


def _summarise(built: dict) -> None:
    facilities: pd.DataFrame = built["facilities"]
    profile = get_state_profile(built.get("state", "CA"))

    table = Table(title=f"{profile.name} data centers", show_lines=False)
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Facilities", str(len(facilities)))
    table.add_row(
        "With footprint geometry",
        str(int(pd.to_numeric(facilities.get("footprint_sqft"), errors="coerce").notna().sum())),
    )
    table.add_row(
        "With a power estimate",
        str(int(pd.to_numeric(facilities.get("best_power_mw"), errors="coerce").notna().sum())),
    )
    table.add_row(
        "Multi-source records",
        str(int((pd.to_numeric(facilities.get("n_sources"), errors="coerce") > 1).sum())),
    )
    console.print(table)

    if "power_tier" in facilities.columns:
        tiers = Table(title="Power evidence tiers")
        tiers.add_column("Tier")
        tiers.add_column("Facilities", justify="right")
        tiers.add_column("Sum IT MW", justify="right")
        for tier, group in facilities.groupby("power_tier"):
            tiers.add_row(
                str(tier),
                str(len(group)),
                f"{pd.to_numeric(group.best_power_mw, errors='coerce').sum():,.0f}",
            )
        console.print(tiers)

    if "operator" in facilities.columns:
        ops = Table(title="Top operators")
        ops.add_column("Operator")
        ops.add_column("Sites", justify="right")
        ops.add_column("Sum IT MW", justify="right")
        grouped = (
            facilities.assign(
                _mw=pd.to_numeric(facilities.best_power_mw, errors="coerce")
            )
            .groupby("operator")
            .agg(sites=("facility_id", "count"), mw=("_mw", "sum"))
            .sort_values("sites", ascending=False)
            .head(12)
        )
        for name, row in grouped.iterrows():
            ops.add_row(str(name), str(int(row.sites)), f"{row.mw:,.0f}")
        console.print(ops)

    recon = built.get("reconciliation") or {}
    if recon:
        console.print("\n[bold]Reconciliation vs top-down anchor[/bold]")
        for key, value in recon.items():
            console.print(f"  {key}: {value}")
        if not recon.get("within_anchor_range", True):
            console.print(
                "[yellow]Bottom-up total is outside the plausible statewide "
                "range. Treat absolute magnitudes with caution.[/yellow]"
            )


if __name__ == "__main__":  # pragma: no cover
    app()
