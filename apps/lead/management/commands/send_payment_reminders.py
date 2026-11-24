from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.lead.models import ReminderConfig
from apps.lead.reminders import send_payment_reminders


class Command(BaseCommand):
    help = 'Send today\'s payment reminders (or list them with --dry-run).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='List, send nothing.')
        parser.add_argument('--date', help='Run as if today were YYYY-MM-DD.')

    def handle(self, *args, **options):
        today = None
        if options['date']:
            try:
                today = date.fromisoformat(options['date'])
            except ValueError as exc:
                raise CommandError('Use --date YYYY-MM-DD.') from exc
        config = ReminderConfig.load()
        if not options['dry_run'] and not config.is_active:
            raise CommandError('Payment reminders are off. Turn them on in the admin site first.')
        report = send_payment_reminders(today=today, dry_run=options['dry_run'], config=config)
        for item in report:
            self.stdout.write(
                f"enrollment {item['enrollment']}: {item['step']} for {item['due_on']} "
                f"({item['amount']}, {item['days_late']} days late)"
            )
        verb = 'Would send' if options['dry_run'] else 'Sent'
        self.stdout.write(f'{verb} {len(report)} reminders.')
