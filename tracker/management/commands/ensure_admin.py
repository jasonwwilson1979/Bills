import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from tracker.admin_access import _account_emails, ensure_admin_access


class Command(BaseCommand):
    help = ("Grant Django-admin access: to BILLFLOW_ADMIN_EMAIL, to an "
            "explicit --email address, or (bootstrap) to the only existing "
            "account when the variable is unset.")

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            help='Promote this specific email (one-off, overrides '
                 'BILLFLOW_ADMIN_EMAIL for this run).')

    def handle(self, *args, **options):
        explicit = (options.get('email') or '').strip()
        if explicit:
            updated = get_user_model().objects.filter(email__iexact=explicit).update(
                is_staff=True, is_superuser=True)
            if updated:
                self.stdout.write(self.style.SUCCESS(
                    f'admin access granted to {explicit}'))
            else:
                self.stdout.write(self.style.WARNING(
                    f'no user found with email {explicit}'))
                self.stdout.write('existing account emails: '
                                  + (', '.join(_account_emails()) or 'NONE'))
            return

        updated = ensure_admin_access()
        if updated is None:
            self.stdout.write(
                'BILLFLOW_ADMIN_EMAIL not set (and no single-account '
                'bootstrap applied) - nothing to do')
        elif updated == 0:
            self.stdout.write(self.style.WARNING(
                f'no user found with email '
                f'{os.environ.get("BILLFLOW_ADMIN_EMAIL", "")}'))
            self.stdout.write('existing account emails: '
                              + (', '.join(_account_emails()) or 'NONE'))
        else:
            self.stdout.write(f'admin access ensured for {updated} user(s)')