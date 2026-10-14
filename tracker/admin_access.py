import logging
import os

from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)


def _promote(user):
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=['is_staff', 'is_superuser'])
    logger.info('[BillFlow] granted admin access to %s', user.email or user.username)


def _normalize_email(raw):
    """Trim whitespace and strip accidental surrounding quotes (a stack
    variable pasted as KEY="value" must still match)."""
    e = (raw or '').strip()
    if len(e) >= 2 and e[0] == e[-1] and e[0] in ('"', "'"):
        e = e[1:-1].strip()
    return e


def _account_emails():
    return [e or '(no email)' for e in
            get_user_model().objects.values_list('email', flat=True)]


def ensure_admin_access():
    """Give an account Django-admin access (the /admin/ section).

    Resolution order:
    1. The account whose email matches BILLFLOW_ADMIN_EMAIL exactly
       (case-insensitive; surrounding quotes/whitespace are ignored).
    2. Fallback: the oldest account on the deployment (the first Google
       account to sign up — the owner on a self-hosted instance). This
       covers a fresh deployment, an unset variable, and a stale or
       mistyped variable, so the owner can never be locked out of /admin/.

    Idempotent. Returns the number of users promoted, or None when no
    account exists yet.
    """
    User = get_user_model()
    email = _normalize_email(os.environ.get('BILLFLOW_ADMIN_EMAIL'))
    if email:
        updated = User.objects.filter(email__iexact=email).update(
            is_staff=True, is_superuser=True)
        if updated:
            logger.info('[BillFlow] ensured admin access for %s', email)
            return updated
        logger.warning(
            '[BillFlow] BILLFLOW_ADMIN_EMAIL=%s matched no account. '
            'Accounts that exist: %s', email, ', '.join(_account_emails()) or 'NONE')
    first = User.objects.filter(is_active=True).order_by('pk').first()
    if first is not None:
        if not (first.is_staff and first.is_superuser):
            logger.warning(
                '[BillFlow] granting admin to the oldest account (%s) — set '
                'BILLFLOW_ADMIN_EMAIL to a specific address to control this.',
                first.email or first.username)
        _promote(first)
        return 1
    return None