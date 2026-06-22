from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from image_helper.config import Settings
from image_helper.hashstore import HashStore
from image_helper.immich import ImmichClient, ImmichError
from image_helper.models import WiggleGroup
from image_helper.service import detect_groups, export_groups, index_asset, process_webhook_asset

app = typer.Typer(
    name="image-helper",
    help="Detect stereoscopic wiggle sequences in Immich and export GIFs.",
    no_args_is_help=True,
)
console = Console()
logger = logging.getLogger(__name__)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def _load_settings() -> Settings:
    return Settings()


def _print_groups(groups: list[WiggleGroup], *, store: HashStore) -> None:
    if not groups:
        console.print("No wiggle groups detected.")
        return

    table = Table(title="Detected wiggle groups")
    table.add_column("Start time")
    table.add_column("Frames", justify="right")
    table.add_column("Avg dist", justify="right")
    table.add_column("Asset IDs")
    table.add_column("Exported")

    for group in groups:
        asset_ids = ", ".join(asset.asset_id for asset in group.assets)
        table.add_row(
            group.assets[0].local_datetime.isoformat(),
            str(len(group.assets)),
            f"{group.average_distance:.1f}",
            asset_ids,
            "yes" if store.is_exported(group.group_key) else "no",
        )

    console.print(table)


@app.command()
def index(
    force: bool = typer.Option(False, "--force", help="Re-hash assets even if checksum is unchanged."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Index Immich images and store perceptual hashes."""
    _setup_logging(verbose)
    settings = _load_settings()
    store = HashStore(settings.hash_db_path)

    added = 0
    skipped = 0
    errors = 0

    with ImmichClient(settings.immich_base_url, settings.immich_api_key) as client:
        for asset in client.iter_all_images():
            try:
                if index_asset(client, store, asset, force=force):
                    added += 1
                    console.print(f"[green]indexed[/green] {asset['id']} ({asset.get('originalFileName', '')})")
                else:
                    skipped += 1
            except ImmichError as exc:
                errors += 1
                console.print(f"[red]error[/red] {asset['id']}: {exc}")

    console.print(
        f"Done. indexed={added} skipped={skipped} errors={errors} total_in_store={store.count()}"
    )


@app.command()
def detect(
    dry_run: bool = typer.Option(True, "--dry-run/--upload", help="Report only; use --upload to export."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Detect wiggle groups from the hash store."""
    _setup_logging(verbose)
    settings = _load_settings()
    store = HashStore(settings.hash_db_path)
    groups = detect_groups(settings, store)

    _print_groups(groups, store=store)

    if dry_run:
        console.print("[yellow]Dry run only. Re-run with --upload to export GIFs.[/yellow]")
        return

    summary = export_groups(settings, store, groups)
    console.print(
        f"Export complete. exported={summary.exported} skipped={summary.skipped} errors={summary.errors}"
    )


@app.command(name="export")
def export_cmd(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Detect and export wiggle GIFs (uploads to Immich)."""
    _setup_logging(verbose)
    settings = _load_settings()
    store = HashStore(settings.hash_db_path)
    groups = detect_groups(settings, store)
    _print_groups(groups, store=store)
    summary = export_groups(settings, store, groups)
    console.print(
        f"Export complete. exported={summary.exported} skipped={summary.skipped} errors={summary.errors}"
    )


@app.command()
def daemon(
    once: bool = typer.Option(False, "--once", help="Run a single poll cycle and exit."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Poll Immich for updated assets, index, detect, and export."""
    _setup_logging(verbose)
    settings = _load_settings()
    store = HashStore(settings.hash_db_path)

    def run_cycle() -> None:
        now = datetime.now(timezone.utc)
        cursor = store.get_daemon_cursor()
        if cursor is None:
            cursor = now - timedelta(days=1)

        console.print(f"Polling assets updated after {cursor.isoformat()}")

        indexed = 0
        with ImmichClient(settings.immich_base_url, settings.immich_api_key) as client:
            for asset in client.search_images(updated_after=cursor, order="asc"):
                try:
                    if index_asset(client, store, asset):
                        indexed += 1
                except ImmichError as exc:
                    console.print(f"[red]index error[/red] {asset['id']}: {exc}")

        groups = detect_groups(settings, store)
        pending = [group for group in groups if not store.is_exported(group.group_key)]
        console.print(f"Indexed {indexed} assets; {len(pending)} new export candidate(s).")

        if pending:
            summary = export_groups(settings, store, pending)
            console.print(
                f"Export complete. exported={summary.exported} skipped={summary.skipped} errors={summary.errors}"
            )

        store.set_daemon_cursor(now)

    if once:
        run_cycle()
        return

    console.print(
        f"Daemon started. poll_interval={settings.daemon_poll_interval_seconds}s "
        "(Ctrl+C to stop)"
    )
    while True:
        try:
            run_cycle()
            time.sleep(settings.daemon_poll_interval_seconds)
        except KeyboardInterrupt:
            console.print("Daemon stopped.")
            break


@app.command()
def webhook(
    host: Optional[str] = typer.Option(None, help="Override WEBHOOK_HOST."),
    port: Optional[int] = typer.Option(None, help="Override WEBHOOK_PORT."),
) -> None:
    """Start Phase 2 webhook receiver stub (requires optional [webhook] deps)."""
    try:
        from image_helper.webhook import run_webhook_server
    except ImportError as exc:
        raise typer.Exit(
            "Webhook extras not installed. Run: pip install 'image-helper[webhook]'"
        ) from exc

    settings = _load_settings()
    run_webhook_server(
        settings,
        host=host or settings.webhook_host,
        port=port or settings.webhook_port,
    )


if __name__ == "__main__":
    app()
