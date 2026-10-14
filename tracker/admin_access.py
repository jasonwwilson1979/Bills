import logging
import os

from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)


def _promote(user):
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=['is_staff', 'is_superuser'])
    logger.info('[BillFlow] granted admin access to %s', user.email or user.username)


def _account_emails():
    return [e or '(no email)' for e in
            get_user_model().objects.values_list('email', flat=True)]


def ensure_admin_access():
    """Give an account Django-admin access (the /admin/ section).

    Resolution order:
    1. The email in BILLFLOW_ADMIN_EMAIL (the deployed stack sets this).
    2. Bootstrap fallback: when the variable is unset and exactly ONE
       account exists, that account is promoted (a fresh single-user
       deployment should just work; the first Google account to sign in
       is the owner).

    Idempotent. Returns the number of users promoted, or None when no
    rule applied (variable unset with 0 or 2+ accounts).
    """
    User = get_user_model()
    email = (os.environ.get('BILLFLOW_ADMIN_EMAIL') or '').strip()
    if not email:
        users = list(User.objects.order_by('pk'))
        if len(users) == 1:
            _promote(users[0])
            return 1
        return None
    updated = User.objects.filter(email__iexact=email).update(
        is_staff=True, is_superuser=True)
    if not updated:
        logger.warning(
            '[BillFlow] BILLFLOW_ADMIN_EMAIL=%s matched no account. '
            'Accounts that exist: %s', email, ', '.join(_account_emails()) or 'NONE')
    else:
        logger.info('[BillFlow] ensured admin access for %s', email)
    return updated