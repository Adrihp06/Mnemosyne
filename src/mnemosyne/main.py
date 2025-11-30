"""
Mnemosyne CLI - Bug Bounty Duplicate Detector

Main entry point for the command-line interface.
"""

import typer
from rich.console import Console
from loguru import logger
import sys

app = typer.Typer(
    name="mnemosyne",
    help="CLI tool for detecting duplicate Bug Bounty reports using AI and vector search",
    add_completion=False,
)

console = Console()


def setup_logging(verbose: bool = False):
    """Configure logging with loguru."""
    logger.remove()  # Remove default handler

    log_level = "DEBUG" if verbose else "INFO"

    # Console logging with colors
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level=log_level,
        colorize=True,
    )


@app.command()
def init(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging")
):
    """
    Initialize the Qdrant collection and verify connections.

    This command:
    - Checks connection to Qdrant
    - Verifies Anthropic API key
    - Creates the security_reports collection if it doesn't exist
    - Verifies embedding model can be loaded
    """
    setup_logging(verbose)
    console.print("[bold cyan]Initializing Mnemosyne...[/bold cyan]\n")

    try:
        from mnemosyne.config import get_settings
        from mnemosyne.db.qdrant import get_qdrant_client, initialize_collection
        from mnemosyne.core.embeddings import get_embedding_generator
        from rich.table import Table

        settings = get_settings()
        checks_table = Table(title="System Checks", show_header=True)
        checks_table.add_column("Component", style="cyan")
        checks_table.add_column("Status")
        checks_table.add_column("Details")

        # 1. Check Anthropic API Key
        console.print("[yellow]→[/yellow] Checking Anthropic API key...")
        if settings.anthropic_api_key:
            checks_table.add_row(
                "Anthropic API",
                "[green]✓ Configured[/green]",
                f"Key: {settings.anthropic_api_key[:15]}..."
            )
        else:
            checks_table.add_row(
                "Anthropic API",
                "[red]✗ Missing[/red]",
                "Set ANTHROPIC_API_KEY in .env"
            )
            console.print(checks_table)
            console.print("\n[red]Error:[/red] ANTHROPIC_API_KEY not configured")
            raise typer.Exit(1)

        # 2. Check Qdrant connection
        console.print("[yellow]→[/yellow] Connecting to Qdrant...")
        try:
            qdrant = get_qdrant_client()
            if qdrant.check_connection():
                checks_table.add_row(
                    "Qdrant DB",
                    "[green]✓ Connected[/green]",
                    f"{settings.qdrant_url}"
                )
            else:
                checks_table.add_row(
                    "Qdrant DB",
                    "[red]✗ Not accessible[/red]",
                    "Is docker-compose running?"
                )
                console.print(checks_table)
                console.print("\n[red]Error:[/red] Cannot connect to Qdrant")
                raise typer.Exit(1)
        except Exception as e:
            checks_table.add_row(
                "Qdrant DB",
                "[red]✗ Error[/red]",
                str(e)[:50]
            )
            console.print(checks_table)
            console.print(f"\n[red]Error:[/red] {e}")
            raise typer.Exit(1)

        # 3. Create/verify collection
        console.print("[yellow]→[/yellow] Initializing collection...")
        if initialize_collection():
            checks_table.add_row(
                "Collection",
                "[green]✓ Ready[/green]",
                f"'{settings.qdrant_collection_name}'"
            )
        else:
            checks_table.add_row(
                "Collection",
                "[red]✗ Failed[/red]",
                "Could not create collection"
            )
            console.print(checks_table)
            console.print("\n[red]Error:[/red] Failed to initialize collection")
            raise typer.Exit(1)

        # 4. Load embedding model
        console.print("[yellow]→[/yellow] Loading embedding model...")
        try:
            generator = get_embedding_generator()
            checks_table.add_row(
                "Embeddings",
                "[green]✓ Loaded[/green]",
                f"{settings.embedding_model} ({settings.embedding_dimension} dims)"
            )
        except Exception as e:
            checks_table.add_row(
                "Embeddings",
                "[red]✗ Error[/red]",
                str(e)[:50]
            )
            console.print(checks_table)
            console.print(f"\n[red]Error:[/red] {e}")
            raise typer.Exit(1)

        # Display results
        console.print()
        console.print(checks_table)
        console.print()
        console.print("[bold green]✓ Mnemosyne initialized successfully![/bold green]")
        console.print("\n[dim]Next steps:[/dim]")
        console.print("  • [cyan]mnemosyne ingest <file>[/cyan] - Ingest a security report")
        console.print("  • [cyan]mnemosyne stats[/cyan] - View collection statistics")

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        raise typer.Exit(130)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"\n[red]Unexpected error:[/red] {e}")
        if verbose:
            import traceback
            console.print(f"\n[dim]{traceback.format_exc()}[/dim]")
        raise typer.Exit(1)


@app.command()
def ingest(
    file_path: str = typer.Argument(..., help="Path to the report file (markdown/txt)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
):
    """
    Ingest a single security report into the database.

    Process: Read file → Normalize (Claude) → Embed → Store (Qdrant)
    """
    setup_logging(verbose)
    console.print(f"[bold cyan]Ingesting report:[/bold cyan] {file_path}\n")

    try:
        from pathlib import Path
        from mnemosyne.llm.client import ClaudeClient
        from mnemosyne.db.qdrant import ingest_report as db_ingest
        from rich.progress import Progress, SpinnerColumn, TextColumn

        # Read file
        report_path = Path(file_path)
        if not report_path.exists():
            console.print(f"[red]Error:[/red] File not found: {file_path}")
            raise typer.Exit(1)

        with open(report_path, "r", encoding="utf-8") as f:
            raw_text = f.read()

        console.print(f"[dim]File size: {len(raw_text):,} characters[/dim]\n")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            # Step 1: Normalize
            task1 = progress.add_task("Normalizing report with Claude...", total=None)
            client = ClaudeClient()
            normalized = client.normalize_report(raw_text)
            progress.update(task1, completed=True)

            # Step 2: Ingest to Qdrant
            task2 = progress.add_task("Storing in Qdrant...", total=None)
            result = db_ingest(raw_text, normalized)
            progress.update(task2, completed=True)

        # Show result
        console.print()
        if result.success:
            console.print(f"[bold green]✓ Report ingested successfully![/bold green]")
            console.print(f"\n[dim]Report ID:[/dim] {result.report_id[:16]}...")
            console.print(f"[dim]Title:[/dim] {normalized.title}")
            console.print(f"[dim]Type:[/dim] {normalized.vulnerability_type}")
            console.print(f"[dim]Severity:[/dim] {normalized.severity.upper()}")
        elif result.already_exists:
            console.print(f"[yellow]⚠ Report already exists in database[/yellow]")
            console.print(f"\n[dim]Report ID:[/dim] {result.report_id[:16]}...")
        else:
            console.print(f"[red]✗ Ingestion failed:[/red] {result.message}")
            raise typer.Exit(1)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        raise typer.Exit(130)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"\n[red]Error during ingestion:[/red] {e}")
        if verbose:
            import traceback
            console.print(f"\n[dim]{traceback.format_exc()}[/dim]")
        raise typer.Exit(1)


@app.command()
def ingest_batch(
    directory: str = typer.Argument(..., help="Directory containing report files"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
    skip_existing: bool = typer.Option(True, "--skip-existing/--no-skip-existing", help="Skip reports that already exist"),
):
    """
    Ingest multiple security reports from a directory.

    More efficient than single ingestion due to prompt caching.
    """
    setup_logging(verbose)
    console.print(f"[bold cyan]Batch ingesting reports from:[/bold cyan] {directory}\n")

    try:
        from pathlib import Path
        from mnemosyne.llm.client import ClaudeClient
        from mnemosyne.db.qdrant import ingest_report as db_ingest
        from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
        from rich.table import Table
        import time

        # Find all .md and .txt files
        dir_path = Path(directory)
        if not dir_path.exists() or not dir_path.is_dir():
            console.print(f"[red]Error:[/red] Directory not found: {directory}")
            raise typer.Exit(1)

        report_files = list(dir_path.glob("*.md")) + list(dir_path.glob("*.txt"))

        if not report_files:
            console.print(f"[yellow]No .md or .txt files found in {directory}[/yellow]")
            raise typer.Exit(0)

        console.print(f"[dim]Found {len(report_files)} report file(s)[/dim]\n")

        # Initialize Claude client once (reuses prompt cache)
        client = ClaudeClient()

        # Track results
        results = {
            "success": 0,
            "skipped": 0,
            "failed": 0,
            "errors": []
        }

        # Process reports
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            main_task = progress.add_task(
                "Processing reports...",
                total=len(report_files)
            )

            for i, report_file in enumerate(report_files, 1):
                try:
                    # Update progress
                    progress.update(
                        main_task,
                        description=f"Processing {report_file.name}...",
                        completed=i-1
                    )

                    # Read file
                    with open(report_file, "r", encoding="utf-8") as f:
                        raw_text = f.read()

                    # Normalize (benefits from prompt caching after first call)
                    normalized = client.normalize_report(raw_text)

                    # Ingest to Qdrant
                    result = db_ingest(raw_text, normalized)

                    if result.success:
                        results["success"] += 1
                        logger.info(f"✓ {report_file.name}: {normalized.title[:50]}...")
                    elif result.already_exists and skip_existing:
                        results["skipped"] += 1
                        logger.info(f"⊙ {report_file.name}: Already exists, skipped")
                    elif result.already_exists and not skip_existing:
                        results["failed"] += 1
                        results["errors"].append((report_file.name, "Already exists"))
                    else:
                        results["failed"] += 1
                        results["errors"].append((report_file.name, result.message))

                except KeyboardInterrupt:
                    console.print("\n[yellow]Batch processing interrupted by user[/yellow]")
                    raise typer.Exit(130)
                except Exception as e:
                    results["failed"] += 1
                    results["errors"].append((report_file.name, str(e)))
                    logger.error(f"✗ {report_file.name}: {e}")

                # Update progress
                progress.update(main_task, completed=i)

        # Display summary
        console.print()
        summary_table = Table(title="Batch Ingestion Summary", show_header=True)
        summary_table.add_column("Status", style="cyan")
        summary_table.add_column("Count", justify="right", style="bold")

        summary_table.add_row("✓ Successful", f"[green]{results['success']}[/green]")
        summary_table.add_row("⊙ Skipped (duplicates)", f"[yellow]{results['skipped']}[/yellow]")
        summary_table.add_row("✗ Failed", f"[red]{results['failed']}[/red]")
        summary_table.add_row("Total", f"{len(report_files)}")

        console.print(summary_table)
        console.print()

        # Show errors if any
        if results["errors"]:
            console.print("[bold red]Errors:[/bold red]")
            for filename, error in results["errors"]:
                console.print(f"  • [dim]{filename}:[/dim] {error}")
            console.print()

        if results["success"] > 0:
            console.print(f"[bold green]✓ Successfully ingested {results['success']} report(s)![/bold green]")

        if results["success"] == 0 and results["skipped"] > 0:
            console.print(f"[yellow]All {results['skipped']} report(s) were already in the database[/yellow]")

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        raise typer.Exit(130)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"\n[red]Error during batch ingestion:[/red] {e}")
        if verbose:
            import traceback
            console.print(f"\n[dim]{traceback.format_exc()}[/dim]")
        raise typer.Exit(1)


@app.command()
def scan(
    file_path: str = typer.Argument(..., help="Path to the new report to scan"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
    show_candidates: bool = typer.Option(False, "--show-candidates", help="Show all candidates found during search"),
):
    """
    Scan a new report for duplicates using the ReAct agent.

    Process: Read file → Normalize → Search (ReAct Agent) → Re-rank → Report results

    Status codes:
    🔴 Duplicate (score > 0.85)
    🟡 Similar (score > 0.65)
    🟢 New (score < 0.65)
    """
    setup_logging(verbose)
    console.print(f"[bold cyan]Scanning report for duplicates:[/bold cyan] {file_path}\n")

    try:
        from pathlib import Path
        from mnemosyne.llm.client import ClaudeClient
        from mnemosyne.agents.react import detect_duplicates
        from mnemosyne.db.qdrant import get_qdrant_client
        from rich.progress import Progress, SpinnerColumn, TextColumn
        from rich.table import Table
        from rich.panel import Panel

        # Read file
        report_path = Path(file_path)
        if not report_path.exists():
            console.print(f"[red]Error:[/red] File not found: {file_path}")
            raise typer.Exit(1)

        with open(report_path, "r", encoding="utf-8") as f:
            raw_text = f.read()

        console.print(f"[dim]File size: {len(raw_text):,} characters[/dim]\n")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            # Step 1: Normalize
            task1 = progress.add_task("Normalizing report with Claude...", total=None)
            client = ClaudeClient()
            normalized = client.normalize_report(raw_text)
            progress.update(task1, completed=True)

            # Step 2: Detect duplicates with ReAct agent
            task2 = progress.add_task("Searching for duplicates (ReAct agent)...", total=None)
            result = detect_duplicates(normalized, raw_text)
            progress.update(task2, completed=True)

        # Display results
        console.print()

        # Determine status emoji and color
        status_map = {
            "duplicate": ("🔴", "red", "DUPLICATE"),
            "similar": ("🟡", "yellow", "SIMILAR"),
            "new": ("🟢", "green", "NEW"),
        }
        emoji, color, label = status_map.get(result.status, ("⚪", "white", "UNKNOWN"))

        # Title panel
        console.print(Panel(
            f"[bold {color}]{emoji} {label}[/bold {color}]\n\n"
            f"[bold]{normalized.title}[/bold]\n"
            f"{normalized.vulnerability_type} in {normalized.affected_component}",
            title="Duplicate Detection Result",
            border_style=color
        ))

        # Details table
        details_table = Table(show_header=False, box=None, padding=(0, 2))
        details_table.add_column("Field", style="cyan")
        details_table.add_column("Value")

        details_table.add_row("Status", f"[bold {color}]{label}[/bold {color}]")
        details_table.add_row("Is Duplicate", "Yes" if result.is_duplicate else "No")
        details_table.add_row("Similarity Score", f"{result.similarity_score:.3f}")

        if result.candidates:
            # Filter for duplicates/similar reports
            duplicates = [c for c in result.candidates if c.get("score", 0) > 0.85]

            if duplicates:
                console.print(f"\n[bold red]Found {len(duplicates)} Potential Duplicate(s):[/bold red]")

                dup_table = Table(show_header=True, box=None, padding=(0, 2))
                dup_table.add_column("Score", style="bold")
                dup_table.add_column("Title")
                dup_table.add_column("Report ID", style="dim")

                for dup in duplicates:
                    score = dup.get("score", 0)
                    report_data = dup.get("report", {})
                    # Handle both dict and object access for report data
                    title = report_data.get("title", "Unknown") if isinstance(report_data, dict) else getattr(report_data, "title", "Unknown")
                    report_id = dup.get("report_id", "Unknown")

                    color = "red" if score > 0.9 else "yellow"
                    dup_table.add_row(
                        f"[{color}]{score:.3f}[/{color}]",
                        title,
                        f"{report_id[:16]}..."
                    )

                console.print(dup_table)

            # If we have a matched report ID but it wasn't in candidates (e.g. early stopping might return it differently)
            # or if we just want to show details of the primary match
            elif result.matched_report_id:
                 # Fetch matched report details from Qdrant
                try:
                    qdrant = get_qdrant_client()
                    matched_points = qdrant.client.retrieve(
                        collection_name=qdrant.collection_name,
                        ids=[result.matched_report_id]
                    )

                    # Display matched report ID (truncated for display only)
                    details_table.add_row("Matched Report ID", result.matched_report_id[:16] + "...")
                    if matched_points:
                        from mnemosyne.models.schema import NormalizedReport
                        matched_report = NormalizedReport(**matched_points[0].payload)
                        details_table.add_row("Matched Report Title", matched_report.title)
                        details_table.add_row("Matched Report Type", matched_report.vulnerability_type)
                        details_table.add_row("Matched Component", matched_report.affected_component)
                except Exception as e:
                    logger.warning(f"Could not fetch matched report details: {e}")

        elif result.matched_report_id:
             # Fallback for when candidates list is empty but we have a match (e.g. from early stopping)
            try:
                qdrant = get_qdrant_client()
                matched_points = qdrant.client.retrieve(
                    collection_name=qdrant.collection_name,
                    ids=[result.matched_report_id]
                )

                # Display matched report ID (truncated for display only)
                details_table.add_row("Matched Report ID", result.matched_report_id[:16] + "...")
                if matched_points:
                    from mnemosyne.models.schema import NormalizedReport
                    matched_report = NormalizedReport(**matched_points[0].payload)
                    details_table.add_row("Matched Report Title", matched_report.title)
                    details_table.add_row("Matched Report Type", matched_report.vulnerability_type)
                    details_table.add_row("Matched Component", matched_report.affected_component)
            except Exception as e:
                logger.warning(f"Could not fetch matched report details: {e}")

        console.print(details_table)
        console.print()

        # Show recommendation
        if result.status == "duplicate":
            console.print(
                Panel(
                    "[bold red]⚠ This report appears to be a duplicate.[/bold red]\n\n"
                    "Recommendation: Review the matched report before submitting.",
                    title="Recommendation",
                    border_style="red"
                )
            )
        elif result.status == "similar":
            console.print(
                Panel(
                    "[bold yellow]⚠ Similar report(s) found.[/bold yellow]\n\n"
                    "Recommendation: Review the matched reports to ensure this is a distinct finding.",
                    title="Recommendation",
                    border_style="yellow"
                )
            )
        else:
            console.print(
                Panel(
                    "[bold green]✓ This appears to be a new, unique report.[/bold green]\n\n"
                    "No duplicates detected in the database.",
                    title="Recommendation",
                    border_style="green"
                )
            )

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        raise typer.Exit(130)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"\n[red]Error during scan:[/red] {e}")
        if verbose:
            import traceback
            console.print(f"\n[dim]{traceback.format_exc()}[/dim]")
        raise typer.Exit(1)


@app.command()
def stats(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging")
):
    """
    Display statistics about the indexed reports.

    Shows:
    - Total number of reports
    - Collection size
    - Database status
    """
    setup_logging(verbose)
    console.print("[bold cyan]Database Statistics[/bold cyan]\n")

    try:
        from mnemosyne.db.qdrant import get_collection_info
        from rich.table import Table
        from rich.panel import Panel

        info = get_collection_info()

        if not info.get("exists"):
            console.print(
                Panel(
                    "[yellow]Collection does not exist yet.[/yellow]\n\n"
                    "Run [cyan]mnemosyne init[/cyan] to create it.",
                    title="No Collection",
                    border_style="yellow",
                )
            )
            raise typer.Exit(0)

        # Create stats table
        stats_table = Table(show_header=False, box=None, padding=(0, 2))
        stats_table.add_column("Metric", style="cyan")
        stats_table.add_column("Value", style="bold")

        stats_table.add_row("Collection Name", info["name"])
        stats_table.add_row("Total Reports", f"{info['points_count']:,}")
        stats_table.add_row("Vectors Indexed", f"{info['vectors_count']:,}")
        stats_table.add_row("Status", f"[green]{info['status'].upper()}[/green]")
        stats_table.add_row(
            "Vector Dimensions",
            f"{info['config']['vector_size']} ({info['config']['distance']})",
        )

        console.print(stats_table)
        console.print()

        if info["points_count"] == 0:
            console.print(
                "[dim]No reports indexed yet. Use [cyan]mnemosyne ingest <file>[/cyan] to add reports.[/dim]"
            )

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        raise typer.Exit(130)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"\n[red]Error:[/red] {e}")
        if verbose:
            import traceback
            console.print(f"\n[dim]{traceback.format_exc()}[/dim]")
        raise typer.Exit(1)


@app.command()
def test_normalization(
    file_path: str = typer.Argument(..., help="Path to a test report file"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
    show_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
):
    """
    Test the normalization process on a report file.

    This is a development/testing command that shows the normalized JSON output.
    """
    setup_logging(verbose)
    console.print(f"[bold cyan]Testing normalization on:[/bold cyan] {file_path}\n")

    try:
        from pathlib import Path
        from mnemosyne.llm.client import ClaudeClient
        from rich.json import JSON
        from rich.panel import Panel
        from rich.table import Table

        # Read the file
        report_path = Path(file_path)
        if not report_path.exists():
            console.print(f"[red]Error:[/red] File not found: {file_path}")
            raise typer.Exit(1)

        with open(report_path, "r", encoding="utf-8") as f:
            raw_text = f.read()

        console.print(f"[dim]File size: {len(raw_text):,} characters[/dim]\n")

        # Normalize
        console.print("[yellow]Calling Claude API for normalization...[/yellow]")
        client = ClaudeClient()
        normalized = client.normalize_report(raw_text)

        # Display results
        if show_json:
            # Raw JSON output
            console.print(JSON(normalized.model_dump_json(indent=2)))
        else:
            # Pretty formatted output
            console.print("\n[bold green]✓ Normalization successful![/bold green]\n")

            # Title and summary
            console.print(Panel(
                f"[bold]{normalized.title}[/bold]\n\n{normalized.summary}",
                title="Report Summary",
                border_style="cyan"
            ))

            # Basic info table
            info_table = Table(show_header=False, box=None, padding=(0, 2))
            info_table.add_column("Field", style="cyan")
            info_table.add_column("Value")

            info_table.add_row("Type", normalized.vulnerability_type)
            info_table.add_row("Severity", f"[bold red]{normalized.severity.upper()}[/bold red]" if normalized.severity in ["critical", "high"] else normalized.severity)
            info_table.add_row("Component", normalized.affected_component)
            info_table.add_row("Technologies", ", ".join(normalized.technologies) if normalized.technologies else "None")

            console.print(info_table)
            console.print()

            # Reproduction steps
            console.print("[bold cyan]Reproduction Steps:[/bold cyan]")
            for i, step in enumerate(normalized.reproduction_steps, 1):
                console.print(f"  {i}. {step}")
            console.print()

            # Technical artifacts
            if normalized.technical_artifacts:
                console.print(f"[bold cyan]Technical Artifacts:[/bold cyan] {len(normalized.technical_artifacts)} found")
                for i, artifact in enumerate(normalized.technical_artifacts, 1):
                    lang = artifact.language or "text"
                    console.print(f"  {i}. [{artifact.type}] {artifact.description} ({lang})")
            console.print()

            # Impact
            console.print(Panel(normalized.impact, title="Impact", border_style="yellow"))

            # Remediation
            if normalized.remediation:
                console.print(Panel(normalized.remediation, title="Remediation", border_style="green"))

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        raise typer.Exit(130)
    except Exception as e:
        console.print(f"\n[red]Error during normalization:[/red] {e}")
        if verbose:
            import traceback
            console.print(f"\n[dim]{traceback.format_exc()}[/dim]")
        raise typer.Exit(1)


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
