import os

from django.contrib.auth import get_user_model


def ensure_admin_access():
    """Give the account in BILLFLOW_ADMIN_EMAIL Django-admin access.

    Promotes the matching user to staff + superuser so they can open /admin/
    with their normal Google session (no password needed). Idempotent and a
    no-op when the variable is not set. Returns the number of users updated,
    or None when the variable is not set.
    """
    email = (os.environ.get('BILLFLOW_ADMIN_EMAIL') or '').strip()
    if not email:
        return None
    return get_user_model().objects.filter(email__iexact=email).update(
        is_staff=True, is_superuser=True)