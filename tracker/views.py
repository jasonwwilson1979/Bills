import datetime
import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.validators import validate_email
from django.http import HttpResponseBadRequest, HttpResponseNotFound, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from .models import ChatMessage, Membership, PendingInvite, UserData

MAX_STATE_BYTES = 5 * 1024 * 1024  # 5 MB is plenty for thousands of bills


def _user_data(user):
    return UserData.objects.filter(user=user).first()


def _google_ready():
    app = settings.SOCIALACCOUNT_PROVIDERS.get('google', {}).get('APP', {})
    return bool(app.get('client_id')) and bool(app.get('secret'))


@login_required
@ensure_csrf_cookie
def home(request):
    """The BillFlow single-page app (requires a signed-in account)."""
    owner, role = _resolve_active_page(request)
    ud = UserData.objects.filter(user=owner).first()
    state = ud.data if ud else None
    if state:
        # Escape "<" so a crafted bill name could never close the script tag.
        state_json = json.dumps(state, separators=(',', ':')).replace('<', '\\u003c')
    else:
        state_json = 'null'

    display = (request.user.get_full_name() or '').strip()
    if not display:
        display = request.user.email.split('@')[0] if request.user.email else request.user.username

    owner_name = ''
    if owner != request.user:
        owner_name = (owner.get_full_name() or '').strip() or owner.email or owner.username

    sharing = {
        'viewing_shared': owner != request.user,
        'owner_name': owner_name,
        'role': role,
        'readOnly': role == 'viewer',
        'my_pages': [
            {'id': 'mine', 'name': 'My BillFlow', 'active': owner == request.user},
        ] + [
            {'id': m.owner_id,
             'name': (m.owner.get_full_name() or '').strip() or m.owner.email or m.owner.username,
             'active': m.owner_id == owner.id}
            for m in Membership.objects.filter(member=request.user)
                                       .select_related('owner').order_by('created_at')
        ],
    }
    sharing_json = json.dumps(
        {'viewing_shared': sharing['viewing_shared'], 'owner_name': owner_name,
         'owner_id': owner.id, 'role': role, 'readOnly': sharing['readOnly']},
        separators=(',', ':')).replace('<', '\\u003c')

    return render(request, 'tracker/app.html', {
        'state_json': state_json,
        'sharing': sharing,
        'sharing_json': sharing_json,
        'display_name': display,
        'user_initial': (display[:1] or 'U').upper(),
        'google_ready': _google_ready(),
    })


@login_required
@require_http_methods(['GET', 'PUT'])
def api_state(request):
    """Read / replace the state document of the page currently being viewed."""
    owner, role = _resolve_active_page(request)

    if request.method == 'GET':
        ud = UserData.objects.filter(user=owner).first()
        return JsonResponse({'exists': ud is not None, 'data': ud.data if ud else None})

    if role == 'viewer':
        return JsonResponse({'error': 'This page is shared with you as view-only.'}, status=403)

    if len(request.body) > MAX_STATE_BYTES:
        return HttpResponseBadRequest('State payload too large')
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest('Invalid JSON')

    if not isinstance(payload, dict):
        return HttpResponseBadRequest('Expected a JSON object')
    bills = payload.get('bills')
    accounts = payload.get('accounts')
    if not isinstance(bills, list) or not isinstance(accounts, list):
        return HttpResponseBadRequest("'bills' and 'accounts' must be lists")
    if len(bills) > 5000 or len(accounts) > 200:
        return HttpResponseBadRequest('Too many records')

    existing = UserData.objects.filter(user=owner).first()
    try:
        if 'paycheckPlans' in payload:
            plans = _validate_paycheck_plans(payload['paycheckPlans'])
        elif 'paycheckPlan' in payload:
            # older single-plan client -> migrate into the list
            single = _validate_plan_item(payload['paycheckPlan']) if payload['paycheckPlan'] is not None else None
            plans = [single] if single else []
        else:
            # Key omitted (e.g. an older tab) -> keep whatever is stored
            plans = (existing.data or {}).get('paycheckPlans') if existing else None
        if 'customCategories' in payload:
            custom_categories = _validate_custom_categories(payload['customCategories'])
        else:
            custom_categories = (existing.data or {}).get('customCategories') if existing else None
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    doc = {
        'appName': str(payload.get('appName') or 'BillFlow')[:60],
        'budgets': payload.get('budgets') if isinstance(payload.get('budgets'), dict) else {},
        'bills': bills,
        'accounts': accounts,
        'paycheckPlans': plans,
        'customCategories': custom_categories,
    }
    obj, _created = UserData.objects.get_or_create(user=owner, defaults={'data': doc})
    if obj.data != doc:
        obj.data = doc
        obj.save(update_fields=['data', 'updated_at'])
    return JsonResponse({'ok': True})


def login_view(request):
    """Styled sign-in page offering Google authentication."""
    if request.user.is_authenticated:
        return redirect('home')
    return render(request, 'account/login.html', {'google_ready': _google_ready()})


def _resolve_active_page(request):
    """Return (page_owner, role) for the page the user is currently viewing.

    role 'owner' means the user's own page; otherwise the role granted by
    the membership (editor/viewer). Falls back to the user's own page when
    the session points at a page they no longer have access to.
    """
    active = request.session.get('bf_active_page')
    if active and active != 'mine':
        try:
            membership = Membership.objects.select_related('owner').get(
                member=request.user, owner__pk=int(active))
            return membership.owner, membership.role
        except (Membership.DoesNotExist, TypeError, ValueError):
            pass
    return request.user, 'owner'


def _json_body(request):
    """Parse a JSON object body; None when missing/malformed."""
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _validate_plan_item(raw):
    """Validate one paycheck plan; returns a cleaned dict or raises ValueError."""
    if not isinstance(raw, dict):
        raise ValueError('each paycheck plan must be an object')
    amount = raw.get('amount', 0)
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0 or amount > 10_000_000:
        raise ValueError('paycheck amount must be a number between 0 and 10,000,000')
    def _clean_date(value, label):
        if not value:
            return ''
        if not isinstance(value, str):
            raise ValueError(f'{label} must be a string')
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            raise ValueError(f'{label} must be a YYYY-MM-DD date')
        return value[:10]

    pdate = _clean_date(raw.get('date') or '', 'paycheck date')
    ndate = _clean_date(raw.get('nextDate') or '', 'next paycheck date')
    allocations = raw.get('allocations')
    if allocations is None:
        allocations = {}
    if not isinstance(allocations, dict):
        raise ValueError('allocations must be an object of bill id -> amount')
    if len(allocations) > 5000:
        raise ValueError('Too many allocations')
    clean = {}
    for key, value in allocations.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or value > 1_000_000:
            raise ValueError('allocation amounts must be numbers between 0 and 1,000,000')
        clean[str(key)[:40]] = round(float(value), 2)
    plan = {'amount': round(float(amount), 2), 'date': pdate, 'nextDate': ndate, 'allocations': clean}
    pid = raw.get('id')
    if isinstance(pid, int) and 0 < pid < 2_147_483_647:
        plan['id'] = pid
    return plan


def _validate_paycheck_plans(raw):
    """Validate the paycheck-plans list. Returns a cleaned list or raises."""
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError('paycheckPlans must be a list')
    if len(raw) > 50:
        raise ValueError('Too many paycheck plans (max 50)')
    return [_validate_plan_item(item) for item in raw]


def _validate_custom_categories(raw):
    """Validate custom bill categories: {key: {label, icon, color}}."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError('customCategories must be an object')
    if len(raw) > 100:
        raise ValueError('Too many custom categories (max 100)')
    clean = {}
    for key, cfg in raw.items():
        k = str(key).strip()[:40]
        if not k:
            raise ValueError('custom category key must not be empty')
        if not isinstance(cfg, dict):
            raise ValueError('each custom category must be an object')
        label = str(cfg.get('label', '')).strip()[:60]
        if not label:
            raise ValueError('custom category label must not be empty')
        clean[k] = {
            'label': label,
            'icon': str(cfg.get('icon', 'fa-tag'))[:40],
            'color': str(cfg.get('color', 'gray'))[:20],
        }
    return clean


@login_required
@require_http_methods(['GET', 'POST', 'DELETE'])
def api_sharing(request):
    """Manage who has access to the signed-in user's own BillFlow page."""
    if request.method == 'GET':
        members = [
            {
                'email': m.member.email,
                'role': m.role,
                'name': (m.member.get_full_name() or m.member.username or m.member.email),
            }
            for m in Membership.objects.filter(owner=request.user)
                                       .select_related('member').order_by('created_at')
        ]
        pending = [
            {'email': p.email, 'role': p.role}
            for p in PendingInvite.objects.filter(owner=request.user).order_by('created_at')
        ]
        return JsonResponse({'members': members, 'pending': pending})

    payload = _json_body(request)
    if payload is None:
        return HttpResponseBadRequest('Invalid JSON')
    email = str(payload.get('email', '')).strip().lower()
    role = str(payload.get('role', 'editor'))
    if role not in (Membership.ROLE_EDITOR, Membership.ROLE_VIEWER):
        return HttpResponseBadRequest('Role must be editor or viewer')

    if request.method == 'POST':
        try:
            validate_email(email)
        except Exception:
            return HttpResponseBadRequest('Enter a valid email address')
        if email == (request.user.email or '').strip().lower():
            return HttpResponseBadRequest("That's your own account")
        existing_user = get_user_model().objects.filter(email__iexact=email).first()
        if existing_user is not None:
            _, created = Membership.objects.update_or_create(
                owner=request.user, member=existing_user, defaults={'role': role})
            PendingInvite.objects.filter(owner=request.user, email__iexact=email).delete()
            return JsonResponse({'ok': True, 'status': 'added' if created else 'updated'})
        PendingInvite.objects.update_or_create(
            owner=request.user, email=email, defaults={'role': role})
        return JsonResponse({'ok': True, 'status': 'invited'})

    # DELETE - remove a member and/or a pending invite by email
    removed = 0
    member_user = get_user_model().objects.filter(email__iexact=email).first()
    if member_user is not None:
        removed += Membership.objects.filter(owner=request.user, member=member_user).delete()[0]
    removed += PendingInvite.objects.filter(owner=request.user, email__iexact=email).delete()[0]
    return JsonResponse({'ok': True, 'removed': removed > 0})


@login_required
@require_http_methods(['POST'])
def api_sharing_select(request):
    """Switch which page the signed-in user is viewing (form or JSON post)."""
    page = request.POST.get('page')
    if page is None:
        payload = _json_body(request)
        if payload is None:
            return HttpResponseBadRequest('Invalid JSON')
        page = str(payload.get('page', 'mine'))
    page = str(page)
    if page != 'mine':
        try:
            Membership.objects.get(member=request.user, owner__pk=int(page))
        except (Membership.DoesNotExist, TypeError, ValueError):
            return JsonResponse({'error': 'No access to that page'}, status=403)
        request.session['bf_active_page'] = str(int(page))
    else:
        request.session['bf_active_page'] = 'mine'
    return redirect('home')


MAX_CHAT_BODY = 2000
CHAT_PAGE_LIMIT = 200  # newest messages returned on a full load


def _display_name(user):
    """Best-effort display name, same rule the sidebar/sharing views use."""
    return (user.get_full_name() or '').strip() or user.email or user.username


def _chat_message_dict(m):
    return {
        'id': m.id,
        'sender': _display_name(m.sender),
        'email': m.sender.email,
        'body': m.body,
        'created_at': m.created_at,
    }


@login_required
@require_http_methods(['GET', 'POST', 'DELETE'])
def api_chat(request):
    """Chat thread of the page currently being viewed.

    The page owner and everyone with access share one conversation.
    Editors and the owner can post and delete their own messages; the
    page owner can delete anything on their page; viewers are read-only.
    GET ?after=<id> returns only messages newer than that id (polling).
    """
    owner, role = _resolve_active_page(request)

    if request.method == 'GET':
        qs = ChatMessage.objects.filter(page_owner=owner).select_related('sender')
        after_raw = (request.GET.get('after') or '').strip()
        try:
            if after_raw:
                after = int(after_raw)
                messages = list(qs.filter(id__gt=after).order_by('id')[:CHAT_PAGE_LIMIT])
            else:
                messages = list(qs.order_by('-id')[:CHAT_PAGE_LIMIT])[::-1]
        except ValueError:
            return HttpResponseBadRequest('Invalid after value')

        members = [
            {'name': _display_name(owner), 'email': owner.email, 'role': 'owner'}
        ] + [
            {'name': _display_name(m.member), 'email': m.member.email, 'role': m.role}
            for m in Membership.objects.filter(owner=owner)
                                       .select_related('member').order_by('created_at')
        ]
        return JsonResponse({
            'page_owner': owner.id,
            'can_post': role != 'viewer',
            'members': members,
            'messages': [_chat_message_dict(m) for m in messages],
        })

    if role == 'viewer':
        return JsonResponse({'error': 'This page is shared with you as view-only.'}, status=403)

    payload = _json_body(request)
    if payload is None:
        return HttpResponseBadRequest('Invalid JSON')

    if request.method == 'POST':
        body = str(payload.get('body', '')).strip()
        if not body:
            return HttpResponseBadRequest('Message cannot be empty')
        msg = ChatMessage.objects.create(
            page_owner=owner, sender=request.user, body=body[:MAX_CHAT_BODY])
        return JsonResponse({'ok': True, 'message': _chat_message_dict(msg)})

    # DELETE - senders may remove their own messages; the page owner may
    # moderate any message on their page.
    try:
        msg = ChatMessage.objects.get(page_owner=owner, pk=int(payload.get('id')))
    except (ChatMessage.DoesNotExist, TypeError, ValueError):
        return HttpResponseNotFound('No such message')
    if msg.sender != request.user and owner != request.user:
        return JsonResponse({'error': 'You can only delete your own messages.'}, status=403)
    msg.delete()
    return JsonResponse({'ok': True})
