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
