import click
from pathlib import Path

from .pipeline import run


@click.group()
def cli():
    """Event Lead CLI — process event leads from multiple sources."""
    pass


@cli.command()
@click.argument('config', type=click.Path(exists=True))
@click.option('--output', '-o', default=None, help='Output directory (default: output/ next to config)')
@click.option('--enrich', is_flag=True, default=False, help='Run LLM enrichment + segment report (requires OPENAI_API_KEY)')
@click.option('--resume', is_flag=True, default=False, help='Resume from last checkpoint (skip completed stages)')
def process(config, output, enrich, resume):
    """Run the lead processing pipeline."""
    run(config, output, enrich=enrich, resume=resume)


if __name__ == '__main__':
    cli()
