import logging

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .admin_access import ensure_admin_access
from .models import Membership, PendingInvite

logger = logging.getLogger(__name__)


def _consume_pending_invites(user):
    """Turn invites by email into real memberships once the person signs in.

    Returns the page owners whose invites were activated."""
    email = (user.email or '').strip().lower()
    if not email:
        return []
    invites = list(PendingInvite.objects.filter(email__iexact=email))
    for invite in invites:
        Membership.objects.update_or_create(
            owner=invite.owner, member=user, defaults={'role': invite.role})
        invite.delete()
    return [invite.owner for invite in invites]


def _select_shared_page(user, request, preferred_owner=None):
    """Land the user directly on a page shared with them when their session
    has no active page yet (e.g. first sign-in). Otherwise they would only
    see their own fresh/demo page and might not realise the share exists."""
    if request is None or request.session.get('bf_active_page'):
        return
    owner_id = preferred_owner.pk if preferred_owner is not None else None
    if owner_id is None:
        membership = Membership.objects.filter(member=user).order_by('created_at').first()
        if membership:
            owner_id = membership.owner_id
    if owner_id:
        request.session['bf_active_page'] = str(owner_id)


@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    """After any login: activate pending invites, land on a shared page,
    keep env-configured admin."""
    try:
        activated = _consume_pending_invites(user)
        if activated:
            logger.info('[BillFlow] activated %d pending share invite(s) for %s',
                        len(activated), user.email)
        _select_shared_page(user, request,
                            preferred_owner=activated[0] if activated else None)
        ensure_admin_access()
    except Exception:
        logger.exception('[BillFlow] post-login processing failed')