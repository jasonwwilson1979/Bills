from django.contrib import admin

from .models import ChatConversation, ChatMessage, ChatParticipant, ChatRead, UserData


@admin.register(UserData)
class UserDataAdmin(admin.ModelAdmin):
    list_display = ('user', 'updated_at')
    search_fields = ('user__username', 'user__email')


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('page_owner', 'sender', 'conversation', 'created_at')
    search_fields = ('sender__email', 'page_owner__email', 'body')


@admin.register(ChatConversation)
class ChatConversationAdmin(admin.ModelAdmin):
    list_display = ('page_owner', 'kind', 'created_at')
    search_fields = ('page_owner__email',)


@admin.register(ChatParticipant)
class ChatParticipantAdmin(admin.ModelAdmin):
    list_display = ('conversation', 'user')


@admin.register(ChatRead)
class ChatReadAdmin(admin.ModelAdmin):
    list_display = ('conversation', 'user', 'last_read_id')
