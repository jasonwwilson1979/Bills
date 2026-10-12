import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from .models import UserData

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
    ud = _user_data(request.user)
    state = ud.data if ud else None
    if state:
        # Escape "<" so a crafted bill name could never close the script tag.
        state_json = json.dumps(state, separators=(',', ':')).replace('<', '\\u003c')
    else:
        state_json = 'null'

    display = (request.user.get_full_name() or '').strip()
    if not display:
        display = request.user.email.split('@')[0] if request.user.email else request.user.username

    return render(request, 'tracker/app.html', {
        'state_json': state_json,
        'display_name': display,
        'user_initial': (display[:1] or 'U').upper(),
        'google_ready': _google_ready(),
    })


@login_required
@require_http_methods(['GET', 'PUT'])
def api_state(request):
    """Read / replace the signed-in user's whole BillFlow state document."""
    ud = _user_data(request.user)

    if request.method == 'GET':
        return JsonResponse({'exists': ud is not None, 'data': ud.data if ud else None})

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

    doc = {
        'appName': str(payload.get('appName') or 'BillFlow')[:60],
        'budgets': payload.get('budgets') if isinstance(payload.get('budgets'), dict) else {},
        'bills': bills,
        'accounts': accounts,
    }
    obj, _created = UserData.objects.get_or_create(user=request.user, defaults={'data': doc})
    if obj.data != doc:
        obj.data = doc
        obj.save(update_fields=['data', 'updated_at'])
    return JsonResponse({'ok': True})


def login_view(request):
    """Styled sign-in page offering Google authentication."""
    if request.user.is_authenticated:
        return redirect('home')
    return render(request, 'account/login.html', {'google_ready': _google_ready()})
