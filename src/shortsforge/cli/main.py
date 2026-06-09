"""ShortsForge CLI — Click-based command surface."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table

console = Console()


@click.group()
def cli() -> None:
    """ShortsForge — AI-powered short-form video studio."""
    from shortsforge.security.secrets import configure_logging
    configure_logging()


@cli.command()
@click.argument("src", type=click.Path(exists=True))
@click.option("--niche", required=True, help="Content niche (e.g. 'AI devtools')")
@click.option("--count", default=3, show_default=True, help="Number of Shorts to generate")
@click.option("--preset", default="bold-pop", show_default=True,
              type=click.Choice(["bold-pop", "subtle-bottom", "glow-center", "meme"]))
@click.option("--publish", is_flag=True, help="Prompt to publish each clip after rendering")
@click.option("--kb-id", default=None, help="Foundry IQ knowledge base ID for grounding")
def repurpose(src: str, niche: str, count: int, preset: str, publish: bool, kb_id: str | None) -> None:
    """Repurpose a long-form video into YouTube Shorts."""
    async def _run():
        from shortsforge.pipeline.repurpose import repurpose as _repurpose

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"Repurposing {Path(src).name} → {count} Shorts", total=None)
            results = await _repurpose(
                Path(src),
                niche=niche,
                count=count,
                caption_preset=preset,
                kb_id=kb_id,
            )
            progress.update(task, completed=True)

        # Print results table
        table = Table(title="ShortsForge Results", show_lines=True)
        table.add_column("Clip ID", style="cyan", max_width=12)
        table.add_column("Title", max_width=40)
        table.add_column("Retention", justify="right")
        table.add_column("Citations", justify="right")
        table.add_column("Path")

        for r in results:
            table.add_row(
                r.clip_id[:10] + "…",
                r.title[:38],
                f"{r.predicted_retention:.0%}",
                str(len(r.citations)),
                str(r.path.name),
            )
        console.print(table)

        if publish:
            for r in results:
                vis = click.prompt(
                    f"\nVisibility for '{r.title[:40]}'",
                    type=click.Choice(["private", "unlisted", "public"]),
                    default="unlisted",
                )
                if vis == "public":
                    console.print("[yellow]⚠ Public upload requires explicit consent.[/yellow]")
                    confirmed = click.confirm(f"Confirm public publish of '{r.title}'?")
                    if not confirmed:
                        console.print("Skipped.")
                        continue
                console.print(f"[dim]Publishing {r.clip_id} as {vis}…[/dim]")
                # Would call publish_youtube here

    asyncio.run(_run())


@cli.command()
@click.option("--prompt", required=True, help="Story idea or topic")
@click.option("--audience", default="general", help="Target audience")
@click.option("--length", default=30, type=int, help="Target duration in seconds")
@click.option("--tone", default="uplifting",
              type=click.Choice(["soothing", "punchy", "mysterious", "uplifting", "educational"]))
@click.option("--kb-id", default=None, help="Foundry IQ KB for grounding")
def story(prompt: str, audience: str, length: int, tone: str, kb_id: str | None) -> None:
    """Generate a short-form story."""
    async def _run():
        from shortsforge.pipeline.story import generate_story
        with console.status("Generating story…"):
            s = await generate_story(
                prompt, audience=audience, length_seconds=length,
                tone=tone, kb_id=kb_id,  # type: ignore[arg-type]
            )
        console.print(f"[green]✓[/green] [bold]{s.title}[/bold]")
        console.print(f"[dim]{s.logline}[/dim]\n")
        for i, scene in enumerate(s.scenes, 1):
            console.print(f"[cyan]Scene {i}[/cyan] ({scene.duration_s:.1f}s) — {scene.beat}")
            console.print(f"  VO: {scene.voiceover_text[:80]}…")
        if s.citations:
            console.print(f"\n[dim]Sources: {', '.join(s.citations[:5])}[/dim]")

    asyncio.run(_run())


@cli.command()
@click.option("--logline", required=True)
@click.option("--genre", default="drama")
@click.option("--characters", multiple=True, default=["Narrator"])
@click.option("--format", "fmt", default="voiceover",
              type=click.Choice(["screenplay", "dialogue", "voiceover"]))
@click.option("--kb-id", default=None)
def script(logline: str, genre: str, characters: tuple, fmt: str, kb_id: str | None) -> None:
    """Generate a short-form script."""
    async def _run():
        from shortsforge.pipeline.script import generate_script
        with console.status("Writing script…"):
            sc = await generate_script(
                logline, genre=genre, characters=list(characters),
                format=fmt, kb_id=kb_id,  # type: ignore[arg-type]
            )
        console.print(f"[green]✓[/green] [bold]{sc.title}[/bold]\n")
        if fmt == "screenplay":
            console.print(sc.to_fountain())
        else:
            for line in sc.lines:
                prefix = f"[{line.speaker}] " if line.speaker else ""
                console.print(f"{prefix}{line.text}")

    asyncio.run(_run())


@cli.group()
def auth() -> None:
    """Authentication commands."""


@auth.command("youtube")
def auth_youtube() -> None:
    """Authenticate with YouTube using OAuth."""
    from shortsforge.publishing.youtube_auth import run_oauth_flow
    console.print("[dim]Starting YouTube OAuth flow…[/dim]")
    run_oauth_flow()
    console.print("[green]✓ YouTube authentication complete.[/green]")


if __name__ == "__main__":
    cli()
