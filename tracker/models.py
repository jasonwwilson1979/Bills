from django.conf import settings
from django.db import models


class UserData(models.Model):
    """Per-user BillFlow state.

    The browser app does its heavy lifting client-side (filters, charts,
    savings calculator all run in JS), so the server stores each user's
    state as one JSON document — bills, accounts, app name and budgets —
    which the SPA reads at page load and writes back (debounced) whenever
    anything changes.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='billflow_data',
    )
    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'User data'
        verbose_name_plural = 'User data'

    def __str__(self):
        return f'BillFlow data for {self.user}'


class Membership(models.Model):
    """Access grant: a 'member' user sees the page owned by another user.

    Editors can change everything on the owner's page; viewers get
    read-only access (enforced server-side in the state API).
    """

    ROLE_EDITOR = 'editor'
    ROLE_VIEWER = 'viewer'
    ROLE_CHOICES = [(ROLE_EDITOR, 'Editor'), (ROLE_VIEWER, 'Viewer')]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='billflow_owned_memberships',
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='billflow_memberships',
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_EDITOR)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['owner', 'member'], name='unique_owner_member'),
        ]

    def __str__(self):
        return f'{self.member} -> {self.owner} ({self.role})'


class PendingInvite(models.Model):
    """An invite for someone who has not signed in yet.

    There is no SMTP server in this app, so "inviting" stores the email
    address; the next time a Google account with that email signs in, the
    login signal converts the invite into a real Membership.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='billflow_pending_invites',
    )
    email = models.EmailField()
    role = models.CharField(max_length=10, choices=Membership.ROLE_CHOICES, default=Membership.ROLE_EDITOR)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['owner', 'email'], name='unique_owner_invite_email'),
        ]

    def __str__(self):
        return f'{self.email} -> {self.owner} ({self.role})'
