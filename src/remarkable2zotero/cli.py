from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

from remarkable2zotero.config import ConfigError, get_cache_dir, load_config

log = logging.getLogger("remarkable2zotero")


def _prompt_select_documents(docs: list) -> list:
    """Prompt the user to select which documents to process."""
    if not docs:
        return []

    click.echo("\nAnnotated documents on device:\n")
    for i, doc in enumerate(docs, 1):
        click.echo(f"  {i}. {doc.visible_name} ({doc.file_type})")

    click.echo("\n  a. All")
    selection = click.prompt(
        "\nSelect documents to sync (e.g. 1,3 or 2-4 or a for all)",
        default="a",
    )

    selection = selection.strip().lower()
    if selection == "a":
        return docs

    selected = set()
    for part in selection.split(","):
        part = part.strip()
        if "-" in part:
            try:
                start, end = part.split("-", 1)
                for n in range(int(start), int(end) + 1):
                    selected.add(n)
            except ValueError:
                continue
        else:
            try:
                selected.add(int(part))
            except ValueError:
                continue

    return [docs[i - 1] for i in sorted(selected) if 1 <= i <= len(docs)]


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    fmt = "%(levelname)s: %(message)s"
    if verbose:
        fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    handler.setFormatter(logging.Formatter(fmt))
    root = logging.getLogger("remarkable2zotero")
    root.setLevel(level)
    root.addHandler(handler)


@click.group()
@click.option("--config", "-c", type=click.Path(), default=None, help="Config file path")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
@click.pass_context
def main(ctx, config, verbose):
    """Sync annotated PDFs from reMarkable to Zotero."""
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = Path(config) if config else None


@main.command()
@click.pass_context
def list(ctx):
    """List annotated PDFs on the reMarkable."""
    from remarkable2zotero import remarkable

    try:
        config = load_config(ctx.obj["config_path"])
    except ConfigError as e:
        log.error(str(e))
        raise SystemExit(1)

    rm_cfg = config["remarkable"]
    try:
        client = remarkable.connect(rm_cfg["host"], rm_cfg.get("user", "root"), rm_cfg["password"])
    except ConnectionError as e:
        log.error(str(e))
        raise SystemExit(1)

    sftp = client.open_sftp()
    try:
        docs = remarkable.discover_documents(sftp)
    finally:
        sftp.close()
        client.close()

    if not docs:
        click.echo("No annotated PDFs found on device.")
        return

    click.echo(f"{'Name':<50} {'Pages':>5}  {'UUID'}")
    click.echo("-" * 90)
    for doc in docs:
        click.echo(f"{doc.visible_name:<50} {len(doc.page_uuids):>5}  {doc.uuid}")


@main.command()
@click.option("--document", "-d", help="Process only this document (by name)")
@click.option("--output-dir", "-o", type=click.Path(), default=None, help="Output directory")
@click.pass_context
def extract(ctx, document, output_dir):
    """Extract annotations and embed into PDF locally (no Zotero upload)."""
    from remarkable2zotero import annotations, pdf, remarkable

    try:
        config = load_config(ctx.obj["config_path"])
    except ConfigError as e:
        log.error(str(e))
        raise SystemExit(1)

    cache_dir = get_cache_dir(config)
    out_dir = Path(output_dir) if output_dir else cache_dir / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    rm_cfg = config["remarkable"]
    try:
        client = remarkable.connect(rm_cfg["host"], rm_cfg.get("user", "root"), rm_cfg["password"])
    except ConnectionError as e:
        log.error(str(e))
        raise SystemExit(1)

    sftp = client.open_sftp()
    try:
        docs = remarkable.discover_documents(sftp)
        if document:
            docs = [d for d in docs if d.visible_name.lower() == document.lower()]
            if not docs:
                log.error("Document '%s' not found on device.", document)
                raise SystemExit(1)

        for doc in docs:
            remarkable.download_document(sftp, doc, cache_dir)
            highlights = annotations.extract_highlights(doc)
            if not highlights:
                log.info("No highlights in '%s', skipping.", doc.visible_name)
                continue

            output_path = out_dir / f"{doc.visible_name}.pdf"
            pdf.embed_highlights(doc.local_pdf_path, highlights, output_path)
            click.echo(f"  → {output_path}")
    finally:
        sftp.close()
        client.close()


@main.command()
@click.option("--document", "-d", help="Process only this document (by name)")
@click.option("--dry-run", is_flag=True, help="Show what would be done without uploading")
@click.option("--no-interact", is_flag=True, help="Skip ambiguous matches instead of prompting")
@click.option("--force-create", is_flag=True, help="Create new Zotero items if no match found")
@click.pass_context
def sync(ctx, document, dry_run, no_interact, force_create):
    """Full pipeline: extract annotations from reMarkable and push to Zotero."""
    from remarkable2zotero import annotations, remarkable, zotero_client

    try:
        config = load_config(ctx.obj["config_path"])
    except ConfigError as e:
        log.error(str(e))
        raise SystemExit(1)

    cache_dir = get_cache_dir(config)
    match_strategy = config.get("sync", {}).get("match_strategy", "filename")

    # Connect to reMarkable
    rm_cfg = config["remarkable"]
    try:
        client = remarkable.connect(rm_cfg["host"], rm_cfg.get("user", "root"), rm_cfg["password"])
    except ConnectionError as e:
        log.error(str(e))
        raise SystemExit(1)

    sftp = client.open_sftp()
    try:
        docs = remarkable.discover_documents(sftp)
        if document:
            docs = [d for d in docs if d.visible_name.lower() == document.lower()]
            if not docs:
                log.error("Document '%s' not found on device.", document)
                raise SystemExit(1)
        elif not no_interact:
            docs = _prompt_select_documents(docs)
            if not docs:
                click.echo("No documents selected.")
                return

        log.info("Processing %d annotated document(s).", len(docs))

        # Connect to Zotero
        zot_cfg = config["zotero"]
        zot = zotero_client.create_client(zot_cfg["api_key"], int(zot_cfg["group_id"]))

        results = {"updated": 0, "created": 0, "skipped": 0, "failed": 0}

        for doc in docs:
            click.echo(f"\nProcessing: {doc.visible_name}")

            # Download
            remarkable.download_document(sftp, doc, cache_dir)

            # Extract highlights
            highlights = annotations.extract_highlights(doc)
            if not highlights:
                log.info("  No highlights, skipping.")
                results["skipped"] += 1
                continue

            click.echo(f"  {len(highlights)} highlight(s) found")

            if dry_run:
                click.echo("  [dry-run] Would sync to Zotero")
                continue

            # Find match in Zotero
            match = zotero_client.find_matching_item(zot, doc, strategy=match_strategy)

            if match:
                click.echo(f"  Found in Zotero: '{match.parent_title}' ({match.attachment_key})")
                zotero_client.create_highlights_note(
                    zot, match.parent_item_key, highlights, doc.visible_name
                )
                click.echo(f"  Highlights note created ({len(highlights)} excerpts)")
                results["updated"] += 1
            else:
                # No match found — prompt to create or skip
                create = force_create
                if not create and not no_interact:
                    click.echo(f"  No match found in Zotero for '{doc.visible_name}'.")
                    create = click.confirm(
                        "  Create new item in library root?", default=True
                    )

                if create:
                    item_key = zotero_client.create_new_item(zot, doc, doc.local_pdf_path)
                    if item_key:
                        zotero_client.create_highlights_note(
                            zot, item_key, highlights, doc.visible_name
                        )
                        click.echo(
                            f"  Created in library root with highlights note "
                            f"({len(highlights)} excerpts) — file manually into a collection"
                        )
                        results["created"] += 1
                    else:
                        results["failed"] += 1
                else:
                    results["skipped"] += 1

        # Summary
        click.echo(f"\nDone: {results['updated']} updated, {results['created']} created, "
                    f"{results['skipped']} skipped, {results['failed']} failed")

    finally:
        sftp.close()
        client.close()
