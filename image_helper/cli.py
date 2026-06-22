from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from image_helper.config import (
    Settings,
    SettingsLoadError,
    default_env_file_path,
    describe_settings,
    find_env_example,
    load_settings,
    resolve_env_file,
)
from image_helper.doctor import REQUIRED_PERMISSIONS, run_doctor
from image_helper.hashstore import HashStore
from image_helper.immich import ImmichClient, ImmichError
from image_helper.models import AssetRecord, WiggleGroup
from image_helper.service import (
    INDEX_BATCH_SIZE,
    detect_groups,
    export_groups,
    flush_index_batch,
    prepare_index_record,
)

app = typer.Typer(
    name="image-helper",
    help="Detect stereoscopic wiggle sequences in Immich and export GIFs.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Inspect and initialize configuration.")
app.add_typer(config_app, name="config")

console = Console()
logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    env_file: Path | None = None


def _ctx(ctx: typer.Context) -> AppContext:
    return ctx.obj or AppContext()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def _require_settings(ctx: typer.Context) -> Settings:
    try:
        return load_settings(_ctx(ctx).env_file)
    except SettingsLoadError as exc:
        console.print(str(exc), style="red")
        raise typer.Exit(code=1) from exc


@app.callback()
def main(
    ctx: typer.Context,
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        help="Path to env file (overrides IMAGE_HELPER_ENV_FILE, .env, and XDG config).",
        envvar="IMAGE_HELPER_ENV_FILE",
    ),
) -> None:
    """Global options for all image-helper commands."""
    ctx.obj = AppContext(env_file=env_file)


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


def _index_assets(
    client: ImmichClient,
    store: HashStore,
    assets,
    *,
    force: bool = False,
    verbose: bool = False,
) -> tuple[int, int, int]:
    added = 0
    skipped = 0
    errors = 0
    batch: list[AssetRecord] = []

    for asset in assets:
        try:
            record = prepare_index_record(client, store, asset, force=force)
            if record is None:
                skipped += 1
                continue

            batch.append(record)
            if len(batch) >= INDEX_BATCH_SIZE:
                added += flush_index_batch(store, batch)
                if verbose:
                    console.print(
                        f"[green]indexed[/green] {asset['id']} ({asset.get('originalFileName', '')})"
                    )
            elif verbose:
                console.print(
                    f"[green]indexed[/green] {asset['id']} ({asset.get('originalFileName', '')})"
                )
        except ImmichError as exc:
            errors += 1
            console.print(f"[red]error[/red] {asset['id']}: {exc}")

    added += flush_index_batch(store, batch)
    return added, skipped, errors


@config_app.command("init")
def config_init(
    ctx: typer.Context,
    output: Path = typer.Option(
        default_env_file_path(),
        "--output",
        "-o",
        help="Where to write the env file.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing env file."),
) -> None:
    """Create an env file from .env.example."""
    output = output.expanduser()
    if output.exists() and not force:
        console.print(f"[red]Refusing to overwrite existing file:[/red] {output}")
        console.print("Use --force to replace it.")
        raise typer.Exit(code=1)

    example = find_env_example()
    if example is None:
        console.print("[red]Could not find .env.example in the project.[/red]")
        raise typer.Exit(code=1)

    output.parent.mkdir(parents=True, exist_ok=True)
    content = example.read_text(encoding="utf-8")

    if sys.stdin.isatty():
        url = typer.prompt("Immich API URL", default="http://localhost:2283/api")
        api_key = typer.prompt("Immich API key", hide_input=True)
        lines = []
        for line in content.splitlines():
            if line.startswith("IMMICH_URL="):
                lines.append(f"IMMICH_URL={url}")
            elif line.startswith("IMMICH_API_KEY="):
                lines.append(f"IMMICH_API_KEY={api_key}")
            else:
                lines.append(line)
        content = "\n".join(lines) + "\n"

    output.write_text(content, encoding="utf-8")
    console.print(f"[green]Wrote[/green] {output}")
    console.print("Next: image-helper doctor")


@config_app.command("show")
def config_show(ctx: typer.Context) -> None:
    """Show effective configuration and each value's source."""
    env_file = resolve_env_file(_ctx(ctx).env_file)
    settings = _require_settings(ctx)
    rows = describe_settings(settings, env_file=_ctx(ctx).env_file)

    if env_file is not None:
        console.print(f"Env file: {env_file}")
    else:
        console.print("Env file: (none found; using defaults and shell environment)")

    table = Table(title="Effective configuration")
    table.add_column("Setting")
    table.add_column("Value")
    table.add_column("Source")

    for row in rows:
        table.add_row(row["name"], row["value"], row["source"])

    console.print(table)


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Verify Immich connectivity and API key permissions."""
    settings = _require_settings(ctx)
    console.print(f"Checking Immich at {settings.immich_base_url} ...")

    result = run_doctor(settings.immich_base_url, settings.immich_api_key)
    checks = Table(title="Doctor")
    checks.add_column("Check")
    checks.add_column("Status")

    checks.add_row("Server ping", "ok" if result["ping_ok"] else "failed")
    checks.add_row("API key auth", "ok" if result["auth_ok"] else "failed")
    checks.add_row("Permissions", "ok" if result["permissions_ok"] else "failed")
    console.print(checks)

    if result["missing_permissions"]:
        console.print("[yellow]Missing permissions:[/yellow]")
        for permission in result["missing_permissions"]:
            console.print(f"  - {permission}")
        console.print("\nRequired permissions:")
        for permission in REQUIRED_PERMISSIONS:
            console.print(f"  - {permission}")

    if result["error"]:
        console.print(f"[red]{result['error']}[/red]")
        raise typer.Exit(code=1)

    console.print("[green]All checks passed.[/green]")


@app.command()
def index(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force", help="Re-hash assets even if checksum is unchanged."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Index Immich images and store perceptual hashes."""
    _setup_logging(verbose)
    settings = _require_settings(ctx)
    store = HashStore(settings.hash_db_path)

    with ImmichClient(settings.immich_base_url, settings.immich_api_key) as client:
        added, skipped, errors = _index_assets(
            client,
            store,
            client.iter_all_images(),
            force=force,
            verbose=verbose,
        )

    console.print(
        f"Done. indexed={added} skipped={skipped} errors={errors} total_in_store={store.count()}"
    )


@app.command()
def detect(
    ctx: typer.Context,
    dry_run: bool = typer.Option(True, "--dry-run/--upload", help="Report only; use --upload to export."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Detect wiggle groups from the hash store."""
    _setup_logging(verbose)
    settings = _require_settings(ctx)
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
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Detect and export wiggle GIFs (uploads to Immich)."""
    _setup_logging(verbose)
    settings = _require_settings(ctx)
    store = HashStore(settings.hash_db_path)
    groups = detect_groups(settings, store)
    _print_groups(groups, store=store)
    summary = export_groups(settings, store, groups)
    console.print(
        f"Export complete. exported={summary.exported} skipped={summary.skipped} errors={summary.errors}"
    )


@app.command()
def daemon(
    ctx: typer.Context,
    once: bool = typer.Option(False, "--once", help="Run a single poll cycle and exit."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Poll Immich for updated assets, index, detect, and export."""
    _setup_logging(verbose)
    settings = _require_settings(ctx)
    store = HashStore(settings.hash_db_path)

    def run_cycle() -> None:
        now = datetime.now(timezone.utc)
        cursor = store.get_daemon_cursor()
        if cursor is None:
            cursor = now - timedelta(days=1)

        console.print(f"Polling assets updated after {cursor.isoformat()}")

        with ImmichClient(settings.immich_base_url, settings.immich_api_key) as client:
            indexed, _, _ = _index_assets(
                client,
                store,
                client.search_images(updated_after=cursor, order="asc"),
            )

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
    ctx: typer.Context,
    host: Optional[str] = typer.Option(None, help="Override WEBHOOK_HOST."),
    port: Optional[int] = typer.Option(None, help="Override WEBHOOK_PORT."),
) -> None:
    """Start Phase 2 webhook receiver stub (requires optional [webhook] deps)."""
    try:
        from image_helper.webhook import run_webhook_server
    except ImportError as exc:
        raise typer.Exit(
            "Webhook extras not installed. Run: uv sync --extra webhook"
        ) from exc

    settings = _require_settings(ctx)
    run_webhook_server(
        settings,
        host=host or settings.webhook_host,
        port=port or settings.webhook_port,
    )


if __name__ == "__main__":
    app()
