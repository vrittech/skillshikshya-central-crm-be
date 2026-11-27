from django.core.management.base import BaseCommand

from apps.lead.voice_sweep import MIN_AGE, sweep_orphan_files


class Command(BaseCommand):
    help = 'Delete voice note recordings that no voice note points to (or list them with --dry-run).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='List, delete nothing.')
        parser.add_argument(
            '--min-age-hours', type=float, default=MIN_AGE.total_seconds() / 3600,
            help='Leave files younger than this alone (default 24).',
        )
        parser.add_argument(
            '--force', action='store_true',
            help='Delete even when the safety stop says too many files look orphaned.',
        )

    def handle(self, *args, **options):
        from datetime import timedelta

        result = sweep_orphan_files(
            dry_run=options['dry_run'],
            force=options['force'],
            min_age=timedelta(hours=options['min_age_hours']),
        )
        for key, modified in result.orphans:
            self.stdout.write(f'  no voice note: {key}  (stored {modified:%d %b %Y %H:%M})')
        for key, error in result.failed:
            self.stderr.write(f'  could not delete {key}: {error}')
        self.stdout.write(result.summary())
        if result.stopped:
            self.stderr.write(
                'Safety stop: too many files have no voice note. Check the database and bucket, '
                'then run again with --force if this is right.'
            )
        elif options['dry_run']:
            self.stdout.write('Dry run: nothing deleted.')
