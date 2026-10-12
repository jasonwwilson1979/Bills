import json
import os

from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in
from django.core.management import call_command
from django.test import TestCase, override_settings
from unittest import mock

FAKE_GOOGLE = {
    'google': {
        'APP': {'client_id': 'fake-id', 'secret': 'fake-secret', 'key': ''},
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
    }
}


class AuthFlowTests(TestCase):
    def test_home_redirects_anonymous_to_login(self):
        r = self.client.get('/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/accounts/login/', r['Location'])

    def test_login_page_renders_for_anonymous(self):
        r = self.client.get('/accounts/login/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('Welcome to BillFlow', r.content.decode())

    @override_settings(SOCIALACCOUNT_PROVIDERS={'google': {'APP': {'client_id': '', 'secret': ''}}})
    def test_login_page_shows_setup_warning_when_unconfigured(self):
        r = self.client.get('/accounts/login/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('GOOGLE_CLIENT_ID', r.content.decode())

    @override_settings(SOCIALACCOUNT_PROVIDERS=FAKE_GOOGLE)
    def test_login_page_shows_google_button_when_configured(self):
        r = self.client.get('/accounts/login/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('/accounts/google/login/', r.content.decode())

    @override_settings(SOCIALACCOUNT_PROVIDERS=FAKE_GOOGLE)
    def test_google_login_get_redirects_straight_to_google(self):
        # SOCIALACCOUNT_LOGIN_ON_GET = True: no intermediate confirmation page
        r = self.client.get('/accounts/google/login/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('accounts.google.com', r['Location'])

    @override_settings(
        SOCIALACCOUNT_PROVIDERS=FAKE_GOOGLE, SOCIALACCOUNT_LOGIN_ON_GET=False
    )
    def test_intermediate_page_is_styled_and_still_works(self):
        # Fallback path (setting off): our own styled template must render,
        # not allauth's bare default (which shows an odd "Menu:" block).
        r = self.client.get('/accounts/google/login/')
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('Continue with Google', body)
        self.assertNotIn('<strong>Menu:</strong>', body)
        # And the form must still complete the flow when posted
        r2 = self.client.post('/accounts/google/login/')
        self.assertEqual(r2.status_code, 302)
        self.assertIn('accounts.google.com', r2['Location'])

    def test_logout_then_home_requires_login_again(self):
        user = User.objects.create_user(username='jason', password='pw123456789')
        self.client.force_login(user)
        r = self.client.post('/accounts/logout/')
        self.assertEqual(r.status_code, 302)
        r2 = self.client.get('/')
        self.assertEqual(r2.status_code, 302)
        self.assertIn('/accounts/login/', r2['Location'])


class AppPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='jason', email='jason@example.com',
            first_name='Jason', password='pw123456789',
        )

    def test_app_page_renders_with_empty_state(self):
        self.client.force_login(self.user)
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('BF_BOOTSTRAP', body)
        self.assertIn('state: null', body)   # no saved document yet
        self.assertIn('Jason', body)         # sidebar shows the display name
        self.assertIn('Sign out', body)

    def test_api_state_requires_auth(self):
        r = self.client.put('/api/state/', data='{}', content_type='application/json')
        self.assertEqual(r.status_code, 302)

    def test_api_state_rejects_invalid_payload(self):
        self.client.force_login(self.user)
        r = self.client.put('/api/state/', data='not json', content_type='application/json')
        self.assertEqual(r.status_code, 400)
        r2 = self.client.put('/api/state/', data=json.dumps({'appName': 'x'}), content_type='application/json')
        self.assertEqual(r2.status_code, 400)

    def test_api_state_put_then_bootstrap_roundtrip(self):
        self.client.force_login(self.user)
        doc = {
            'appName': 'MyBills',
            'budgets': {'housing': 1},
            'bills': [{
                'id': 1, 'name': 'Rent', 'amount': 1200, 'dueDate': '2026-10-01',
                'category': 'housing', 'status': 'pending', 'recurring': True,
                'isPayoff': False, 'totalOwed': 0, 'paidAmount': 0, 'payments': [],
                'interestRate': 0, 'interestRateType': 'annual', 'notes': '',
                'preferredAccountId': None,
            }],
            'accounts': [],
        }
        r = self.client.put('/api/state/', data=json.dumps(doc), content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['ok'])

        # GET endpoint returns the saved document
        r2 = self.client.get('/api/state/')
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()['exists'])
        self.assertEqual(r2.json()['data']['appName'], 'MyBills')

        # The app page bootstraps with the saved state
        r3 = self.client.get('/')
        body = r3.content.decode()
        self.assertIn('MyBills', body)
        self.assertIn('Rent', body)
        self.assertNotIn('state: null', body)


class AuthDependencyTests(TestCase):
    def test_allauth_lazy_dependencies_installed(self):
        # allauth imports 'jwt' (and cryptography for key verification) lazily
        # inside its Google id_token handling. If either module is missing,
        # the OAuth callback dies with ModuleNotFoundError -> HTTP 500 that
        # allauth's own try/except cannot catch. These must be in
        # requirements.txt so clean Docker builds include them.
        import importlib
        for module_name in ('jwt', 'cryptography'):
            importlib.import_module(module_name)


class SharingTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner', email='owner@example.com',
                                              first_name='Owen', password='pw123456789')
        self.member = User.objects.create_user(username='member', email='member@example.com',
                                               first_name='Mia', password='pw123456789')

    def _save_owner_doc(self, app_name='HomeBills'):
        self.client.force_login(self.owner)
        doc = {'appName': app_name, 'budgets': {}, 'bills': [{'id': 1, 'name': 'Rent'}], 'accounts': []}
        r = self.client.put('/api/state/', data=json.dumps(doc), content_type='application/json')
        self.assertEqual(r.status_code, 200)

    def test_invite_existing_user_editor_can_read_and_write(self):
        self._save_owner_doc()
        self.client.force_login(self.owner)
        r = self.client.post('/api/sharing/', data=json.dumps({'email': 'member@example.com', 'role': 'editor'}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['status'], 'added')

        # The member switches to the owner's page and can read + write it
        self.client.force_login(self.member)
        r = self.client.post('/api/sharing/select/', data={'page': str(self.owner.pk)})
        self.assertEqual(r.status_code, 302)
        r2 = self.client.get('/api/state/')
        self.assertTrue(r2.json()['exists'])
        self.assertEqual(r2.json()['data']['appName'], 'HomeBills')
        doc2 = {'appName': 'HomeBills', 'budgets': {}, 'bills': [{'id': 2, 'name': 'Power'}], 'accounts': []}
        r3 = self.client.put('/api/state/', data=json.dumps(doc2), content_type='application/json')
        self.assertEqual(r3.status_code, 200)

        # The owner sees the member's change on their own page
        self.client.force_login(self.owner)
        r4 = self.client.get('/api/state/')
        self.assertEqual(r4.json()['data']['bills'][0]['name'], 'Power')

    def test_viewer_cannot_write_shared_page(self):
        self._save_owner_doc()
        self.client.force_login(self.owner)
        self.client.post('/api/sharing/', data=json.dumps({'email': 'member@example.com', 'role': 'viewer'}),
                         content_type='application/json')
        self.client.force_login(self.member)
        self.client.post('/api/sharing/select/', data={'page': str(self.owner.pk)})
        r = self.client.put('/api/state/', data=json.dumps({'appName': 'x', 'budgets': {}, 'bills': [], 'accounts': []}),
                            content_type='application/json')
        self.assertEqual(r.status_code, 403)
        # Reads still work
        r2 = self.client.get('/api/state/')
        self.assertTrue(r2.json()['exists'])

    def test_home_shows_shared_banner_and_page_switcher(self):
        self._save_owner_doc()
        self.client.force_login(self.owner)
        self.client.post('/api/sharing/', data=json.dumps({'email': 'member@example.com', 'role': 'viewer'}),
                         content_type='application/json')
        self.client.force_login(self.member)
        self.client.post('/api/sharing/select/', data={'page': str(self.owner.pk)})
        r = self.client.get('/')
        body = r.content.decode()
        self.assertIn('Viewing', body)
        self.assertIn('Owen', body)
        self.assertIn('read-only', body)
        self.assertIn('My BillFlow', body)

    def test_pending_invite_activates_on_login(self):
        self._save_owner_doc()
        self.client.force_login(self.owner)
        r = self.client.post('/api/sharing/', data=json.dumps({'email': 'newguy@example.com', 'role': 'editor'}),
                             content_type='application/json')
        self.assertEqual(r.json()['status'], 'invited')

        # The invited person signs in with Google -> django's login signal fires
        newbie = User.objects.create_user(username='newguy', email='newguy@example.com', password='pw123456789')
        user_logged_in.send(sender=User, request=None, user=newbie)

        self.client.force_login(newbie)
        r2 = self.client.post('/api/sharing/select/', data={'page': str(self.owner.pk)})
        self.assertEqual(r2.status_code, 302)
        r3 = self.client.get('/api/state/')
        self.assertTrue(r3.json()['exists'])
        self.assertEqual(r3.json()['data']['appName'], 'HomeBills')

        # The invite has been consumed
        self.client.force_login(self.owner)
        r4 = self.client.get('/api/sharing/')
        self.assertEqual(r4.json()['pending'], [])
        self.assertEqual(len(r4.json()['members']), 1)

    def test_remove_member_revokes_access(self):
        self.client.force_login(self.owner)
        self.client.post('/api/sharing/', data=json.dumps({'email': 'member@example.com', 'role': 'editor'}),
                         content_type='application/json')
        r = self.client.delete('/api/sharing/', data=json.dumps({'email': 'member@example.com'}),
                               content_type='application/json')
        self.assertTrue(r.json()['removed'])

        self.client.force_login(self.member)
        r2 = self.client.post('/api/sharing/select/', data={'page': str(self.owner.pk)})
        self.assertEqual(r2.status_code, 403)
        # Their own page still works
        r3 = self.client.get('/api/state/')
        self.assertFalse(r3.json()['exists'])

    def test_cannot_invite_self_invalid_email_or_role(self):
        self.client.force_login(self.owner)
        r = self.client.post('/api/sharing/', data=json.dumps({'email': 'owner@example.com', 'role': 'editor'}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)
        r2 = self.client.post('/api/sharing/', data=json.dumps({'email': 'not-an-email', 'role': 'editor'}),
                              content_type='application/json')
        self.assertEqual(r2.status_code, 400)
        r3 = self.client.post('/api/sharing/', data=json.dumps({'email': 'x@example.com', 'role': 'admin'}),
                              content_type='application/json')
        self.assertEqual(r3.status_code, 400)

    def test_select_without_access_rejected(self):
        self.client.force_login(self.member)
        r = self.client.post('/api/sharing/select/', data={'page': '999999'})
        self.assertEqual(r.status_code, 403)


class EnsureAdminTests(TestCase):
    @mock.patch.dict(os.environ, {'BILLFLOW_ADMIN_EMAIL': 'owner@example.com'})
    def test_promotes_matching_email(self):
        u = User.objects.create_user(username='o', email='Owner@Example.com', password='pw123456789')
        call_command('ensure_admin', verbosity=0)
        u.refresh_from_db()
        self.assertTrue(u.is_staff)
        self.assertTrue(u.is_superuser)

    @mock.patch.dict(os.environ, {'BILLFLOW_ADMIN_EMAIL': 'future@example.com'})
    def test_noop_when_user_not_yet_created(self):
        call_command('ensure_admin', verbosity=0)
        self.assertFalse(User.objects.filter(email__iexact='future@example.com').exists())

    def test_noop_when_env_unset(self):
        call_command('ensure_admin', verbosity=0)


class BrandTests(TestCase):
    def test_favicon_static_file_exists(self):
        from django.contrib.staticfiles import finders
        self.assertIsNotNone(finders.find('tracker/favicon.svg'))

    def test_login_page_shows_dollar_logo_and_favicon(self):
        r = self.client.get('/accounts/login/')
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('fa-dollar-sign', body)
        self.assertIn('favicon.svg', body)
        self.assertNotIn('fa-bolt', body)

    def test_app_page_shows_dollar_logo_and_favicon(self):
        u = User.objects.create_user(username='brand', email='brand@example.com', password='pw123456789')
        self.client.force_login(u)
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('fa-dollar-sign', body)
        self.assertIn('favicon.svg', body)

    @override_settings(DEBUG=True)
    def test_favicon_is_served(self):
        r = self.client.get('/static/tracker/favicon.svg')
        self.assertEqual(r.status_code, 200)
        body = b''.join(r.streaming_content)
        self.assertIn(b'<svg', body)


class PaycheckPlanTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='planner', email='planner@example.com', password='pw123456789')
        self.client.force_login(self.user)

    def _put(self, doc):
        return self.client.put('/api/state/', data=json.dumps(doc), content_type='application/json')

    def test_paycheck_plans_roundtrip(self):
        doc = {'appName': 'B', 'budgets': {}, 'bills': [], 'accounts': [],
               'paycheckPlans': [
                   {'id': 42, 'amount': 2300, 'date': '2026-10-15', 'nextDate': '2026-10-30', 'allocations': {'1': 150}},
                   {'amount': 2400, 'date': '2026-10-30', 'allocations': {}},
               ]}
        r = self._put(doc)
        self.assertEqual(r.status_code, 200)
        g = self.client.get('/api/state/')
        plans = g.json()['data']['paycheckPlans']
        self.assertEqual(len(plans), 2)
        self.assertEqual(plans[0]['id'], 42)
        self.assertEqual(plans[0]['amount'], 2300)
        self.assertEqual(plans[0]['nextDate'], '2026-10-30')
        self.assertEqual(plans[0]['allocations'], {'1': 150.0})
        self.assertEqual(plans[1]['date'], '2026-10-30')

    def test_old_single_plan_migrates_into_list(self):
        doc = {'appName': 'B', 'budgets': {}, 'bills': [], 'accounts': [],
               'paycheckPlan': {'amount': 500, 'date': '2026-10-01', 'allocations': {}}}
        r = self._put(doc)
        self.assertEqual(r.status_code, 200)
        g = self.client.get('/api/state/')
        plans = g.json()['data']['paycheckPlans']
        self.assertEqual(plans, [{'amount': 500.0, 'date': '2026-10-01', 'nextDate': '', 'allocations': {}}])

    def test_paycheck_plans_validation_rejects_bad_payloads(self):
        base = {'appName': 'B', 'budgets': {}, 'bills': [], 'accounts': []}
        for bad in ('nope', 12, {'amount': 'rich'}, [{'amount': True}], [{'amount': -5}],
                    [{'date': 'soon'}], [{'nextDate': 'soon'}], [{'allocations': {'1': 'x'}}],
                    [{'allocations': []}], [{'allocations': 5}]):
            doc = dict(base, paycheckPlans=bad)
            r = self._put(doc)
            self.assertEqual(r.status_code, 400, msg=f'payload {bad!r} should be rejected')

    def test_paycheck_plans_preserved_when_omitted(self):
        doc = {'appName': 'B', 'budgets': {}, 'bills': [], 'accounts': [],
               'paycheckPlans': [{'amount': 2300, 'date': '2026-10-15', 'allocations': {}}]}
        self._put(doc)
        r = self._put({'appName': 'B2', 'budgets': {}, 'bills': [], 'accounts': []})
        self.assertEqual(r.status_code, 200)
        g = self.client.get('/api/state/')
        self.assertEqual(g.json()['data']['paycheckPlans'][0]['amount'], 2300)

    def test_paycheck_plans_can_be_cleared(self):
        doc = {'appName': 'B', 'budgets': {}, 'bills': [], 'accounts': [],
               'paycheckPlans': [{'amount': 100, 'date': '2026-10-15', 'allocations': {}}]}
        self._put(doc)
        self._put({'appName': 'B', 'budgets': {}, 'bills': [], 'accounts': [], 'paycheckPlans': None})
        g = self.client.get('/api/state/')
        self.assertIsNone(g.json()['data']['paycheckPlans'])


class CustomCategoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='cats', email='cats@example.com', password='pw123456789')
        self.client.force_login(self.user)

    def _put(self, doc):
        return self.client.put('/api/state/', data=json.dumps(doc), content_type='application/json')

    def test_custom_categories_roundtrip(self):
        doc = {'appName': 'B', 'budgets': {}, 'bills': [], 'accounts': [],
               'customCategories': {'c_pets': {'label': 'Pets', 'icon': 'fa-paw', 'color': 'pink'}}}
        r = self._put(doc)
        self.assertEqual(r.status_code, 200)
        g = self.client.get('/api/state/')
        cc = g.json()['data']['customCategories']
        self.assertEqual(cc['c_pets'], {'label': 'Pets', 'icon': 'fa-paw', 'color': 'pink'})

    def test_custom_categories_validation_rejects_bad_payloads(self):
        base = {'appName': 'B', 'budgets': {}, 'bills': [], 'accounts': []}
        for bad in ('nope', [{'label': 'x'}], {'c_x': {'label': ''}}, {'c_x': 'Pets'}):
            doc = dict(base, customCategories=bad)
            self.assertEqual(self._put(doc).status_code, 400, msg=f'payload {bad!r} should be rejected')

    def test_custom_categories_preserved_when_omitted(self):
        doc = {'appName': 'B', 'budgets': {}, 'bills': [], 'accounts': [],
               'customCategories': {'c_pets': {'label': 'Pets', 'icon': 'fa-paw', 'color': 'pink'}}}
        self._put(doc)
        r = self._put({'appName': 'B2', 'budgets': {}, 'bills': [], 'accounts': []})
        self.assertEqual(r.status_code, 200)
        g = self.client.get('/api/state/')
        self.assertEqual(g.json()['data']['customCategories']['c_pets']['label'], 'Pets')

    def test_bill_match_key_pass_through(self):
        doc = {'appName': 'B', 'budgets': {}, 'bills': [
            {'id': 1, 'name': 'Netflix', 'amount': 15.49, 'dueDate': '2026-10-05', 'category': 'subscriptions',
             'status': 'pending', 'recurring': True, 'isPayoff': False, 'totalOwed': 0, 'paidAmount': 0,
             'payments': [], 'notes': '', 'matchKey': 'netflix'},
        ], 'accounts': []}
        r = self.client.put('/api/state/', data=json.dumps(doc), content_type='application/json')
        self.assertEqual(r.status_code, 200)
        g = self.client.get('/api/state/')
        self.assertEqual(g.json()['data']['bills'][0]['matchKey'], 'netflix')

    def test_custom_categories_can_be_cleared(self):
        doc = {'appName': 'B', 'budgets': {}, 'bills': [], 'accounts': [],
               'customCategories': {'c_pets': {'label': 'Pets', 'icon': 'fa-paw', 'color': 'pink'}}}
        self._put(doc)
        self._put({'appName': 'B', 'budgets': {}, 'bills': [], 'accounts': [], 'customCategories': None})
        g = self.client.get('/api/state/')
        self.assertIsNone(g.json()['data']['customCategories'])


class TemplateScriptTests(TestCase):
    def test_every_inline_handler_is_defined_in_template(self):
        """Catches onclick/onchange/oninput handlers pointing at functions that
        were never defined (which would silently break clicks at runtime)."""
        import re

        from django.conf import settings

        path = os.path.join(str(settings.BASE_DIR), 'templates', 'tracker', 'app.html')
        with open(path, encoding='utf-8') as fh:
            html = fh.read()
        handlers = set()
        for attr in re.findall(r'on(?:click|change|input)="([^"]+)"', html):
            for call in re.findall(r'(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(', attr):
                handlers.add(call)
        defined = set(re.findall(r'function\s+([A-Za-z_$][\w$]*)\s*\(', html))
        builtins = {'confirm', 'parseFloat', 'parseInt', 'String', 'stopPropagation', 'preventDefault'}
        missing = sorted(h for h in handlers if h not in defined and h not in builtins)
        self.assertEqual(missing, [], f'inline handlers not defined in app.html: {missing}')


class AccountTransactionsTests(TestCase):
    def test_account_deposits_and_debits_pass_through(self):
        user = User.objects.create_user(username='acct', email='acct@example.com', password='pw123456789')
        self.client.force_login(user)
        doc = {'appName': 'B', 'budgets': {}, 'bills': [], 'accounts': [
            {'id': 1, 'name': 'Checking', 'type': 'checking', 'balance': 1000, 'color': 'blue',
             'deposits': [{'date': '2026-10-01', 'amount': 500, 'note': 'Paycheck'}],
             'debits': [{'date': '2026-10-02', 'amount': 20, 'note': 'Coffee shop'}]},
        ]}
        r = self.client.put('/api/state/', data=json.dumps(doc), content_type='application/json')
        self.assertEqual(r.status_code, 200)
        g = self.client.get('/api/state/')
        acc = g.json()['data']['accounts'][0]
        self.assertEqual(acc['deposits'], [{'date': '2026-10-01', 'amount': 500, 'note': 'Paycheck'}])
        self.assertEqual(acc['debits'], [{'date': '2026-10-02', 'amount': 20, 'note': 'Coffee shop'}])
