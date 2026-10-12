import json

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

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
