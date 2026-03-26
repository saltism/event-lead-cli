import click
from pathlib import Path
import re
import yaml

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


def _slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r'[^a-z0-9]+', '-', value)
    value = re.sub(r'-+', '-', value).strip('-')
    return value or 'new-event'


@cli.command('init-config')
@click.option('--type', 'template_type', type=click.Choice(['event', 'meetup']), default='event', show_default=True,
              help='Which starter template to use.')
@click.option('--name', required=True, help='Event name, used in config and output filename prefix.')
@click.option('--date', default='2026-06-15', show_default=True, help='Event date (YYYY-MM-DD).')
@click.option('--location', default='TBD', show_default=True, help='Event location.')
@click.option('--output-path', default=None, help='Output config path (default: configs/<slug>.yaml).')
@click.option('--force', is_flag=True, default=False, help='Overwrite file if it already exists.')
def init_config(template_type, name, date, location, output_path, force):
    """Generate a config file from event or meetup template."""
    repo_root = Path(__file__).resolve().parent.parent
    template_file = repo_root / 'configs' / f'{template_type}-template.yaml'
    if not template_file.exists():
        raise click.ClickException(f'Template not found: {template_file}')

    slug = _slugify(name)
    target = Path(output_path) if output_path else (repo_root / 'configs' / f'{slug}.yaml')
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and not force:
        raise click.ClickException(f'Config already exists: {target}. Use --force to overwrite.')

    with open(template_file, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)

    cfg.setdefault('event', {})
    cfg['event']['name'] = name
    cfg['event']['date'] = date
    cfg['event']['location'] = location
    cfg.setdefault('output', {})
    cfg['output']['filename_prefix'] = slug

    with open(target, 'w', encoding='utf-8') as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)

    click.echo(f'Config created: {target}')
    click.echo(f'Run with: ./run_enrich.sh {target}')


if __name__ == '__main__':
    cli()
