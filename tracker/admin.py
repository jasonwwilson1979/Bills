from django.contrib import admin

from .models import ChatMessage, UserData


@admin.register(UserData)
class UserDataAdmin(admin.ModelAdmin):
    list_display = ('user', 'updated_at')
    search_fields = ('user__username', 'user__email')


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('page_owner', 'sender', 'created_at')
    search_fields = ('sender__email', 'page_owner__email', 'body')
