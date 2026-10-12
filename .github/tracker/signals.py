import logging

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .admin_access import ensure_admin_access
from .models import Membership, PendingInvite

logger = logging.getLogger(__name__)


def _consume_pending_invites(user):
    """Turn invites by email into real memberships once the person signs in."""
    email = (user.email or '').strip().lower()
    if not email:
        return 0
    invites = list(PendingInvite.objects.filter(email__iexact=email))
    for invite in invites:
        Membership.objects.update_or_create(
            owner=invite.owner, member=user, defaults={'role': invite.role})
        invite.delete()
    return len(invites)


@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    """After any login: activate pending invites + keep env-configured admin."""
    try:
        granted = _consume_pending_invites(user)
        if granted:
            logger.info('[BillFlow] activated %d pending share invite(s) for %s',
                        granted, user.email)
        ensure_admin_access()
    except Exception:
        logger.exception('[BillFlow] post-login processing failed')