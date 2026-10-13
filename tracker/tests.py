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
        self.assertIn('Welcome back, Jason', body)  # personalized dashboard subtitle
        self.assertIn('name="viewport"', body)  # mobile layout depends on it
        self.assertIn('Sign out', body)

    def test_app_page_shows_backup_and_restore_ui(self):
        self.client.force_login(self.user)
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('Backup &amp; Restore', body)          # settings section title
        self.assertIn('Download Backup', body)
        self.assertIn('Restore Backup', body)
        self.assertIn('backupData()', body)
        self.assertIn('openRestoreModal()', body)
        self.assertIn('id="restoreModal"', body)
        self.assertIn('id="restoreFileInput"', body)
        self.assertIn('accept=".json,application/json"', body)

    def test_backup_document_roundtrips_through_state_api(self):
        """A backup file as built by backupData()/restore must survive the
        clear -> restore cycle through the validated state API."""
        self.client.force_login(self.user)
        full = {
            'appName': 'House Bills',
            'budgets': {'housing': 1500, 'debt': 400},
            'bills': [{'id': 1, 'name': 'Rent', 'amount': 1200, 'dueDate': '2026-11-01',
                       'category': 'housing', 'status': 'pending', 'recurring': True,
                       'isPayoff': True, 'totalOwed': 2400, 'paidAmount': 600,
                       'payments': [{'date': '2026-10-01', 'amount': 600, 'note': 'x', 'accountId': 1}],
                       'interestRate': 3.5, 'interestRateType': 'annual', 'notes': 'n',
                       'preferredAccountId': 1}],
            'accounts': [{'id': 1, 'name': 'Checking', 'type': 'checking', 'balance': 900, 'color': 'blue'}],
            'paycheckPlans': [{'id': 5, 'amount': 2000, 'date': '2026-10-15', 'nextDate': '2026-10-30',
                               'allocations': {'1': 1200}}],
            'customCategories': {'pets': {'label': 'Pets', 'icon': 'fa-paw', 'color': 'pink'}},
        }
        # Exactly the shape backupData() downloads: versioned wrapper + data doc
        backup = {'bfVersion': 1, 'app': 'BillFlow',
                  'exportedAt': '2026-10-13T12:00:00.000Z', 'data': dict(full)}
        r = self.client.put('/api/state/', data=json.dumps(full), content_type='application/json')
        self.assertEqual(r.status_code, 200)

        # Simulate "Clear Data", then restore from the backup file
        self.client.put('/api/state/', data=json.dumps(
            {'appName': 'BillFlow', 'budgets': {}, 'bills': [], 'accounts': []}),
            content_type='application/json')
        r2 = self.client.put('/api/state/', data=json.dumps(backup['data']), content_type='application/json')
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()['ok'])

        r3 = self.client.get('/api/state/')
        self.assertEqual(r3.status_code, 200)
        doc = r3.json()['data']
        self.assertEqual(doc['appName'], 'House Bills')
        self.assertEqual(doc['bills'][0]['name'], 'Rent')
        self.assertEqual(doc['bills'][0]['paidAmount'], 600)
        self.assertEqual(doc['accounts'][0]['balance'], 900)
        self.assertEqual(doc['paycheckPlans'][0]['allocations'], {'1': 1200})
        self.assertEqual(doc['customCategories']['pets']['label'], 'Pets')

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

    def test_member_lands_on_shared_page_after_login(self):
        # Owner shares with the member, whose session has no active page yet.
        self._save_owner_doc(app_name='OwenPage')
        self.client.force_login(self.owner)
        self.client.post('/api/sharing/', data=json.dumps({'email': 'member@example.com', 'role': 'editor'}),
                         content_type='application/json')
        # force_login fires user_logged_in with the client's request, so the
        # login signal should point the member's session at the shared page.
        self.client.force_login(self.member)
        r = self.client.get('/api/state/')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['exists'])
        self.assertEqual(r.json()['data']['appName'], 'OwenPage')
        body = self.client.get('/').content.decode()
        self.assertIn('Viewing', body)

    def test_banner_offers_shared_pages_when_on_own_page(self):
        self.client.force_login(self.owner)
        self.client.post('/api/sharing/', data=json.dumps({'email': 'member@example.com', 'role': 'editor'}),
                         content_type='application/json')
        self.client.force_login(self.member)   # auto-lands on the owner's page
        self.client.post('/api/sharing/select/', data={'page': 'mine'})
        body = self.client.get('/').content.decode()
        self.assertIn('you also have shared access to', body)
        self.assertIn('Open Owen', body)

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

    def test_mobile_menu_available_on_every_page(self):
        """Every page header must include the mobile hamburger, and the CSS
        hide rule must precede the 768px media query so the mobile
        'display:flex' override wins the cascade (otherwise phones can only
        ever see the dashboard)."""
        import re

        from django.conf import settings

        path = os.path.join(str(settings.BASE_DIR), 'templates', 'tracker', 'app.html')
        with open(path, encoding='utf-8') as fh:
            html = fh.read()
        sections = len(re.findall(r'class="page-section(?: active)?" id="page-', html))
        burgers = len(re.findall(r'class="btn btn-secondary mobile-menu-btn"', html))
        self.assertEqual(sections, 12, 'unexpected page-section count')
        self.assertGreaterEqual(burgers, sections,
                                'some page headers are missing the mobile menu button')
        idx_none = html.find('.mobile-menu-btn { display:none !important; }')
        idx_media = html.find('@media (max-width:768px)')
        self.assertNotEqual(idx_none, -1, 'mobile-menu hide rule missing')
        self.assertNotEqual(idx_media, -1, 'mobile media query missing')
        self.assertLess(idx_none, idx_media,
                        'hide rule must come before the media query it overrides')

    def test_mobile_layout_rules_exist(self):
        """Guard the phone refinements: iOS-zoom-proof inputs, the two-line
        bill row, and stat grids that beat their inline styles."""
        from django.conf import settings

        path = os.path.join(str(settings.BASE_DIR), 'templates', 'tracker', 'app.html')
        with open(path, encoding='utf-8') as fh:
            html = fh.read()
        for needle in ('===== MOBILE REFINEMENTS',
                       '.form-input, .form-select { font-size: 16px',
                       '.bill-row .bill-actions { grid-column: 1 / -1',
                       'grid-template-columns: 1fr 1fr !important',
                       '.modal { max-width: calc(100vw - 16px)'):
            self.assertIn(needle, html)

    def test_month_view_supports_scheduled_occurrences(self):
        """Future months of recurring bills must read 'scheduled' (derived
        per-occurrence status) instead of the global 'paid' status, and those
        occurrences must reach the paycheck planner."""
        from django.conf import settings

        path = os.path.join(str(settings.BASE_DIR), 'templates', 'tracker', 'app.html')
        with open(path, encoding='utf-8') as fh:
            html = fh.read()
        for needle in ('function billMonthStatus(', 'function planOccurrence(',
                       '.bill-status.scheduled', "filterAllBills('scheduled'",
                       'Scheduled</button>', 'dot blue',
                       'renderBillRow(b, view)', "planBillOwes(r.b, r.occ.st)"):
            self.assertIn(needle, html)

    def test_rich_notes_and_bill_tags_exist(self):
        """Rich notes editor (with sanitizer), expandable note panels, bill
        tags in chat, and scroll-reset on page switch."""
        from django.conf import settings

        path = os.path.join(str(settings.BASE_DIR), 'templates', 'tracker', 'app.html')
        with open(path, encoding='utf-8') as fh:
            html = fh.read()
        for needle in ('function bfSanitizeRich(', 'function loadBillNotesEditor(',
                       'function toggleNotesSource(', 'function toggleBillNotes(',
                       'function openBillTagModal(', 'function insertBillTag(',
                       'function openBillFromChat(', 'function chatLinkBills(',
                       'id="billNotesEditor"', 'contenteditable="true"',
                       'id="billTagModal"', 'data-bid="${bill.id}"',
                       'bill-notes-panel', 'chat-bill-chip',
                       'window.scrollTo(0, 0)'):
            self.assertIn(needle, html)

    def test_category_drill_down_exists(self):
        """Budget & Reports category rows open a drill-down listing the
        bills in that category (with edit + jump-to-All-Bills actions)."""
        from django.conf import settings

        path = os.path.join(str(settings.BASE_DIR), 'templates', 'tracker', 'app.html')
        with open(path, encoding='utf-8') as fh:
            html = fh.read()
        for needle in ('function openCategoryBills(', 'function closeCategoryBillsModal(',
                       'function renderCategoryBillList(', 'function viewCategoryInBills(',
                       'id="categoryBillsModal"', 'id="catBillsList"', 'id="catBillsSummary"',
                       'onclick="openCategoryBills(this.dataset.cat)"',
                       'data-cat="${bfEscapeHtml(cat)}"', 'catbill-row'):
            self.assertIn(needle, html)
        # All four category surfaces are clickable: budget allocation,
        # budget legend, report breakdown, report legend.
        self.assertEqual(html.count('onclick="openCategoryBills(this.dataset.cat)"'), 4)


class TemplateStructureTests(TestCase):
    """Parses the *rendered* app page with html.parser (script bodies are
    CDATA, so JS template literals cannot pollute the walk) and asserts the
    document tree is sound: every page-section is a direct child of <main>
    and no div is left open. Catches misplaced sections that would push
    content to the bottom of the page or under the sidebar."""

    def _rendered(self):
        from django.template.loader import get_template
        from types import SimpleNamespace
        return get_template('tracker/app.html').render({
            'state_json': 'null',
            'sharing_json': '{}',
            'sharing': {'viewing_shared': False, 'owner_name': '', 'role': 'owner',
                        'readOnly': False, 'owner_id': 1,
                        'my_pages': [{'id': 'mine', 'name': 'My BillFlow', 'active': True}]},
            'display_name': 'Test', 'user_initial': 'T', 'google_ready': False,
            'user': SimpleNamespace(email='', is_authenticated=True),
        })

    def test_page_sections_are_siblings_and_tree_is_balanced(self):
        from html.parser import HTMLParser

        html = self._rendered()
        sections, stack, extra = [], [], []

        class Walk(HTMLParser):
            def handle_starttag(self, tag, attrs):
                if tag != 'div':
                    return
                d = dict(attrs)
                stack.append(d.get('id') or ('.' + (d.get('class') or '').split()[0]
                                             if d.get('class') else '<div>'))
                if 'page-section' in (d.get('class') or ''):
                    sections.append((d.get('id'), list(stack[:-1])))

            def handle_endtag(self, tag):
                if tag == 'div':
                    (stack.pop() if stack else extra.append('near %s' % str(self.getpos())))

        w = Walk(convert_charrefs=True)
        w.feed(html)
        w.close()
        self.assertEqual(extra, [], 'more </div> than <div> somewhere')
        self.assertEqual(stack, [], 'unclosed <div> elements remain: %r' % stack)
        ids = [s for s, _ in sections]
        self.assertEqual(len(ids), len(set(ids)), 'duplicate page-section ids')
        self.assertIn('page-chat', ids)
        for sid, ancestors in sections:
            self.assertEqual(ancestors, ['.app-container'],
                             'page-section %s has unexpected ancestors %r — every '
                             'section must sit directly inside .app-container > <main>'
                             % (sid, ancestors))


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


class ChatApiTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner', email='owner@example.com',
                                              first_name='Owen', password='pw123456789')
        self.member = User.objects.create_user(username='member', email='member@example.com',
                                               first_name='Mia', password='pw123456789')
        self.other = User.objects.create_user(username='other', email='other@example.com',
                                              first_name='Olive', password='pw123456789')

    def _grant(self, role='editor'):
        self.client.force_login(self.owner)
        r = self.client.post('/api/sharing/', data=json.dumps({'email': 'member@example.com', 'role': role}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 200)

    def _select_owner_page(self):
        self.client.force_login(self.member)
        r = self.client.post('/api/sharing/select/', data={'page': str(self.owner.pk)})
        self.assertEqual(r.status_code, 302)

    def test_chat_requires_auth(self):
        r = self.client.get('/api/chat/')
        self.assertEqual(r.status_code, 302)

    def test_list_returns_team_conversation(self):
        self.client.force_login(self.owner)
        r = self.client.get('/api/chat/')
        data = r.json()
        self.assertTrue(data['can_post'])
        self.assertEqual(data['page_owner'], self.owner.pk)
        self.assertEqual([c['kind'] for c in data['conversations']], ['page'])
        self.assertEqual(data['conversations'][0]['unread'], 0)
        self.assertTrue(any(m['email'] == 'owner@example.com' for m in data['members']))

    def test_owner_posts_to_team_chat_and_member_sees_unread(self):
        self._grant('editor')
        self.client.force_login(self.owner)
        conv_id = self.client.get('/api/chat/').json()['conversations'][0]['id']
        r = self.client.post('/api/chat/', data=json.dumps({'conversation': conv_id, 'body': '  hello team  '}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 200)
        msg = r.json()['message']
        self.assertEqual(msg['body'], 'hello team')
        self.assertEqual(msg['conversation'], conv_id)

        # The member sees unread=1 in their list; opening it clears it.
        self._select_owner_page()
        ml = self.client.get('/api/chat/').json()
        self.assertEqual(ml['conversations'][0]['unread'], 1)
        mr = self.client.get('/api/chat/?conversation=%d' % conv_id).json()
        self.assertEqual([m['body'] for m in mr['messages']], ['hello team'])
        self.assertEqual(mr['conversation']['unread'], 0)
        mr2 = self.client.get('/api/chat/?conversation=%d' % conv_id).json()
        self.assertEqual(mr2['conversation']['unread'], 0)

    def test_viewer_cannot_post_but_can_read(self):
        self._grant('viewer')
        self._select_owner_page()
        lst = self.client.get('/api/chat/').json()
        self.assertFalse(lst['can_post'])
        conv_id = lst['conversations'][0]['id']
        r = self.client.post('/api/chat/', data=json.dumps({'conversation': conv_id, 'body': 'hi'}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 403)

    def test_direct_chat_flow(self):
        self._grant('editor')
        # Owner starts a DM with the member.
        self.client.force_login(self.owner)
        r = self.client.post('/api/chat/', data=json.dumps({'direct': 'member@example.com'}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 200)
        conv_id = r.json()['conversation']['id']
        self.assertEqual(r.json()['conversation']['kind'], 'direct')
        self.assertEqual(r.json()['conversation']['name'], 'Mia')

        # A message into the DM.
        r2 = self.client.post('/api/chat/', data=json.dumps({'conversation': conv_id, 'body': 'psst'}),
                              content_type='application/json')
        self.assertEqual(r2.status_code, 200)

        # The member's list shows team + the DM (unread), named from their
        # own perspective.
        self._select_owner_page()
        ml = self.client.get('/api/chat/').json()
        convs = {c['kind']: c for c in ml['conversations']}
        self.assertIn('page', convs)
        self.assertIn('direct', convs)
        self.assertEqual(convs['direct']['unread'], 1)
        self.assertEqual(convs['direct']['name'], 'Owen')

        # Starting the same DM again reuses the conversation.
        r3 = self.client.post('/api/chat/', data=json.dumps({'direct': 'owner@example.com'}),
                              content_type='application/json')
        self.assertEqual(r3.status_code, 200)
        self.assertEqual(r3.json()['conversation']['id'], conv_id)

        # A second member cannot see or open someone else's DM.
        self.client.force_login(self.owner)
        self.client.post('/api/sharing/', data=json.dumps({'email': 'other@example.com', 'role': 'editor'}),
                         content_type='application/json')
        self.client.force_login(self.other)
        self.client.post('/api/sharing/select/', data={'page': str(self.owner.pk)})
        ol = self.client.get('/api/chat/').json()
        self.assertNotIn('direct', [c['kind'] for c in ol['conversations']])
        hidden = self.client.get('/api/chat/?conversation=%d' % conv_id)
        self.assertEqual(hidden.status_code, 404)

    def test_direct_requires_page_access_and_not_self(self):
        self.client.force_login(self.owner)
        r = self.client.post('/api/chat/', data=json.dumps({'direct': 'nobody@example.com'}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)
        r2 = self.client.post('/api/chat/', data=json.dumps({'direct': 'owner@example.com'}),
                              content_type='application/json')
        self.assertEqual(r2.status_code, 400)

    def test_after_returns_only_newer_messages(self):
        self.client.force_login(self.owner)
        conv_id = self.client.get('/api/chat/').json()['conversations'][0]['id']
        ids = []
        for i in range(3):
            r = self.client.post('/api/chat/', data=json.dumps({'conversation': conv_id, 'body': f'm{i}'}),
                                 content_type='application/json')
            ids.append(r.json()['message']['id'])
        r2 = self.client.get('/api/chat/?conversation=%d&after=%d' % (conv_id, ids[0]))
        self.assertEqual(r2.status_code, 200)
        self.assertEqual([m['id'] for m in r2.json()['messages']], ids[1:])
        r3 = self.client.get('/api/chat/?conversation=%d&after=abc' % conv_id)
        self.assertEqual(r3.status_code, 400)

    def test_empty_body_rejected_and_long_body_truncated(self):
        self.client.force_login(self.owner)
        conv_id = self.client.get('/api/chat/').json()['conversations'][0]['id']
        r = self.client.post('/api/chat/', data=json.dumps({'conversation': conv_id, 'body': '   '}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)
        r2 = self.client.post('/api/chat/', data=json.dumps({'conversation': conv_id, 'body': 'x' * 3000}),
                              content_type='application/json')
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(len(r2.json()['message']['body']), 2000)
        r3 = self.client.post('/api/chat/', data='not json', content_type='application/json')
        self.assertEqual(r3.status_code, 400)
        r4 = self.client.post('/api/chat/', data=json.dumps({'body': 'no conversation'}),
                              content_type='application/json')
        self.assertEqual(r4.status_code, 400)

    def test_delete_rules(self):
        # Owner posts; an editor may not delete someone else's message.
        self._grant('editor')
        self.client.force_login(self.owner)
        conv_id = self.client.get('/api/chat/').json()['conversations'][0]['id']
        r = self.client.post('/api/chat/', data=json.dumps({'conversation': conv_id, 'body': 'from owner'}),
                             content_type='application/json')
        mid = r.json()['message']['id']
        self._select_owner_page()
        r2 = self.client.delete('/api/chat/', data=json.dumps({'id': mid}),
                                content_type='application/json')
        self.assertEqual(r2.status_code, 403)

        # Editor deletes their own message: allowed.
        r3 = self.client.post('/api/chat/', data=json.dumps({'conversation': conv_id, 'body': 'from member'}),
                              content_type='application/json')
        mid2 = r3.json()['message']['id']
        r4 = self.client.delete('/api/chat/', data=json.dumps({'id': mid2}),
                                content_type='application/json')
        self.assertEqual(r4.status_code, 200)

        # The page owner moderates the team chat.
        self.client.force_login(self.owner)
        r5 = self.client.delete('/api/chat/', data=json.dumps({'id': mid}),
                                content_type='application/json')
        self.assertEqual(r5.status_code, 200)
        g = self.client.get('/api/chat/?conversation=%d' % conv_id)
        self.assertEqual(g.json()['messages'], [])

    def test_delete_missing_message_is_404(self):
        self.client.force_login(self.owner)
        r = self.client.delete('/api/chat/', data=json.dumps({'id': 999999}),
                               content_type='application/json')
        self.assertEqual(r.status_code, 404)
