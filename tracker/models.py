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


class ChatMessage(models.Model):
    """One message in a chat conversation.

    Conversations live inside a page's chat space (see ChatConversation):
    the team chat shared by the page owner and everyone with access, and
    1-on-1 direct chats between two of those people.
    """

    conversation = models.ForeignKey(
        'ChatConversation',
        on_delete=models.CASCADE,
        related_name='messages',
        null=True,  # backfilled to non-null by migration 0005
    )
    page_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='billflow_chat_messages',
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='billflow_chat_sent',
    )
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        indexes = [
            models.Index(fields=['page_owner', 'id'], name='chat_page_id_idx'),
            models.Index(fields=['conversation', 'id'], name='chat_conv_id_idx'),
        ]

    def __str__(self):
        return f'{self.sender} -> {self.page_owner} page: {self.body[:30]}'


class ChatConversation(models.Model):
    """A chat conversation inside a page's chat space.

    kind 'page' is the team chat shared by the page owner and everyone
    with access to their page (one per page). kind 'direct' is a 1-on-1
    conversation between two of those people (see ChatParticipant).
    """

    KIND_PAGE = 'page'
    KIND_DIRECT = 'direct'
    KIND_CHOICES = [(KIND_PAGE, 'Team'), (KIND_DIRECT, 'Direct')]

    page_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='billflow_chat_conversations',
    )
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=KIND_PAGE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['page_owner'], condition=models.Q(kind='page'),
                                    name='unique_team_chat_per_page'),
        ]

    def __str__(self):
        if self.kind == self.KIND_DIRECT:
            return f'Direct chat on {self.page_owner} page'
        return f'Team chat for {self.page_owner}'

    def participants_for(self, user):
        """The other participant of a direct conversation (None for team)."""
        if self.kind != self.KIND_DIRECT:
            return None
        p = self.participants.exclude(user=user).select_related('user').first()
        return p.user if p else None


class ChatParticipant(models.Model):
    """Membership of a user in a direct chat conversation.

    Team chats are implicit (everyone with access to the page); direct
    chats have exactly two participant rows.
    """

    conversation = models.ForeignKey(
        ChatConversation,
        on_delete=models.CASCADE,
        related_name='participants',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='billflow_chat_participations',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['conversation', 'user'], name='unique_chat_participant'),
        ]

    def __str__(self):
        return f'{self.user} in {self.conversation}'


class ChatRead(models.Model):
    """Per-user read marker for a conversation (exact unread counts)."""

    conversation = models.ForeignKey(
        ChatConversation,
        on_delete=models.CASCADE,
        related_name='reads',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='billflow_chat_reads',
    )
    last_read_id = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['conversation', 'user'], name='unique_chat_read'),
        ]

    def __str__(self):
        return f'{self.user} read {self.conversation} up to {self.last_read_id}'
