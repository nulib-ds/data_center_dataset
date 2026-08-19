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
from .config import PROCESSED_DIR, ensure_dirs
from .http import CachedClient

app = typer.Typer(
    add_completion=False,
    help="Build a dataset of California data centers with tiered power estimates.",
)
console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )


@app.command()
def fetch(
    refresh: bool = typer.Option(False, help="Bypass caches and re-download."),
    with_ceqanet: bool = typer.Option(
        False, help="Also crawl CEQAnet for attested MW figures (slow, low recall)."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Download and snapshot all sources without building outputs."""
    _setup_logging(verbose)
    ensure_dirs()
    with CachedClient() as client:
        result = pipeline.ingest(client, refresh=refresh, with_ceqanet=with_ceqanet)
    console.print(f"[green]Ingested {len(result['records'])} source records.[/green]")


@app.command()
def build(
    refresh: bool = typer.Option(False, help="Bypass caches and re-download."),
    with_ceqanet: bool = typer.Option(False, help="Include CEQAnet Tier A crawl."),
    apply_calibration: bool = typer.Option(
        False,
        help="Rescale non-attested tiers to the statewide anchor when out of range.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run the full pipeline and write all outputs."""
    _setup_logging(verbose)
    built = pipeline.run(
        refresh=refresh,
        with_ceqanet=with_ceqanet,
        apply_calibration=apply_calibration,
    )
    _summarise(built)


@app.command()
def validate(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """Check the published tables against their schema contracts."""
    _setup_logging(verbose)
    from .normalize.schema import FacilitySchema, PowerEstimateSchema

    facilities = pd.read_csv(PROCESSED_DIR / "facilities.csv")
    estimates = pd.read_csv(PROCESSED_DIR / "power_estimates.csv")

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


@app.command()
def report() -> None:
    """Print a summary of the built dataset."""
    path = PROCESSED_DIR / "facilities.csv"
    if not path.exists():
        console.print("[red]No outputs found. Run `build` first.[/red]")
        raise typer.Exit(code=1)
    facilities = pd.read_csv(path)
    recon_path = PROCESSED_DIR / "reconciliation.json"
    recon = json.loads(recon_path.read_text()) if recon_path.exists() else {}
    _summarise({"facilities": facilities, "reconciliation": recon})


def _summarise(built: dict) -> None:
    facilities: pd.DataFrame = built["facilities"]

    table = Table(title="California data centers", show_lines=False)
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
