from django.core.management.base import BaseCommand

from tracker.admin_access import ensure_admin_access


class Command(BaseCommand):
    help = "Grant Django-admin access to the email in BILLFLOW_ADMIN_EMAIL."

    def handle(self, *args, **options):
        updated = ensure_admin_access()
        if updated is None:
            self.stdout.write('BILLFLOW_ADMIN_EMAIL not set - nothing to do')
        else:
            self.stdout.write(f'admin access ensured for {updated} user(s)')