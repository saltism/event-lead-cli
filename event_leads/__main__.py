import click
from pathlib import Path
import re
import yaml

from .pipeline import run
from .cards_ocr import run_cards_ocr


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


@cli.command('cards-ocr')
@click.option('--input-dir', default='data/cards', show_default=True, help='Directory containing business card images.')
@click.option('--output-csv', default='data/business-card.csv', show_default=True, help='Output CSV file path.')
@click.option('--model', default='gpt-4o-mini', show_default=True, help='Vision-capable model for OCR extraction.')
def cards_ocr(input_dir, output_csv, model):
    """Extract card contacts from images into a CSV file."""
    run_cards_ocr(input_dir=input_dir, output_csv=output_csv, model=model)


def _upsert_card_source(config_path: Path, output_csv: str) -> None:
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}

    data_dir = (config_path.parent / cfg.get('data_dir', '.')).resolve()
    out_path = Path(output_csv).expanduser()
    if not out_path.is_absolute():
        out_path = (Path.cwd() / out_path).resolve()
    else:
        out_path = out_path.resolve()

    try:
        file_value = str(out_path.relative_to(data_dir))
    except ValueError:
        file_value = str(out_path)

    cfg.setdefault('sources', {})
    source_name = 'business_card_ocr'
    source_cfg = cfg['sources'].get(source_name, {})
    source_cfg.update({
        'file': file_value,
        'type': 'csv',
        'encoding': 'utf-8',
        'mapping': {
            'name': 'name',
            'email': 'email',
            'company_title': 'company_title',
            'phone': 'phone',
        },
        'attendance_status': 'attended',
    })
    cfg['sources'][source_name] = source_cfg

    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)


@cli.command('cards-ocr-and-run')
@click.argument('config', type=click.Path(exists=True))
@click.option('--input-dir', default='data/cards', show_default=True, help='Directory containing business card images.')
@click.option('--output-csv', default='data/business-card.csv', show_default=True, help='Output CSV file path.')
@click.option('--model', default='gpt-4o-mini', show_default=True, help='Vision-capable model for OCR extraction.')
@click.option('--no-update-config', is_flag=True, default=False, help='Do not inject OCR source into config.')
@click.option('--resume', is_flag=True, default=False, help='Resume pipeline from checkpoints where possible.')
@click.option('--output', '-o', default=None, help='Output directory (default: output/ next to config)')
def cards_ocr_and_run(config, input_dir, output_csv, model, no_update_config, resume, output):
    """Run card OCR, optionally patch config source, then execute full enriched pipeline."""
    run_cards_ocr(input_dir=input_dir, output_csv=output_csv, model=model)

    cfg_path = Path(config).resolve()
    if not no_update_config:
        _upsert_card_source(cfg_path, output_csv)
        click.echo(f'Config updated with source: business_card_ocr -> {output_csv}')

    run(str(cfg_path), output, enrich=True, resume=resume)


if __name__ == '__main__':
    cli()
