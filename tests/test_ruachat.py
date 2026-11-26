import json
from collections import deque

import httpx
import pytest

import checkin
import checkin_october
from utils.config import AccountConfig, AppConfig

API = 'https://api.rua.chat'
AUTH = 'https://auth.rua.chat'
FLOW = 'rua-chat-authentication-flow'
QUERY = 'next=%2Fapplication%2Fo%2Fauthorize%2F&state=test-oidc-state'
EXECUTOR = f'{AUTH}/api/v3/flows/executor/{FLOW}/'
CHECKIN = f'{API}/api/user/checkin'
PROFILE = f'{API}/api/user/self'
EXCHANGE = f'{API}/api/sso/exchange'
LOGOUT = f'{API}/api/user/auth/logout'
POINTS = f'{API}/api/rewards/self/points'
LOGIN = {'uid_field': 'reader@example.test', 'password': 'test-password', 'component': 'ak-stage-identification'}
TODAY = '2026-11-26'
EARNINGS = {'points': '2.50', 'grant_quota': 500000, 'recharge_quota': 0, 'cards': 1}


def _ok(data=None, **kwargs):
	return httpx.Response(200, json={'success': True, 'data': {} if data is None else data}, **kwargs)


def _redirect(location, **headers):
	return httpx.Response(302, headers={'Location': location, **headers})


def _settings(**changes):
	return (
		'GET',
		f'{API}/api/status',
		_ok({'auth_mode': 'sso_only', 'checkin_enabled': True, 'turnstile_check': False, **changes}),
	)


def _login(*, user_id=42, challenge=None, split_password=False):
	authorize = '/application/o/authorize/?state=test-oidc-state'
	csrf_page = httpx.Response(200, headers={'Set-Cookie': 'authentik_csrf=unit-csrf; Path=/; Secure'})
	password_steps = []
	password_form = LOGIN
	if split_password:
		password_form = {'component': 'ak-stage-password', 'password': LOGIN['password']}
		identification_form = {key: value for key, value in LOGIN.items() if key != 'password'}
		password_steps.append(
			('POST', EXECUTOR, httpx.Response(200, json={'component': 'ak-stage-password'}), identification_form)
		)
	return [
		_settings(),
		('GET', f'{API}/api/sso/start', _redirect(AUTH + authorize, **{'Set-Cookie': 'sso=unit-sso; Path=/; Secure'})),
		('GET', AUTH + authorize, _redirect(f'/account/?flow={FLOW}&{QUERY}')),
		(
			'GET',
			f'{AUTH}/account/',
			httpx.Response(200) if split_password else csrf_page,
		),
		*([('GET', f'{AUTH}/if/flow/{FLOW}/?{QUERY}&ak_native=1', csrf_page)] if split_password else []),
		(
			'GET',
			EXECUTOR,
			httpx.Response(
				200,
				json={
					'component': 'ak-stage-identification',
					'user_fields': ['email'],
					'password_fields': not split_password,
					'captcha_stage': None,
				},
			),
		),
		*password_steps,
		(
			'POST',
			EXECUTOR,
			challenge or httpx.Response(200, json={'component': 'xak-flow-redirect', 'to': authorize}),
			password_form,
		),
		('GET', AUTH + authorize, _redirect(f'{API}/api/sso/callback?code=unit-code&state=test-oidc-state')),
		(
			'GET',
			f'{API}/api/sso/callback',
			_redirect('/auth/sso/complete', **{'Set-Cookie': 'exchange=unit-exchange; Path=/; Secure'}),
		),
		('GET', f'{API}/auth/sso/complete', httpx.Response(200)),
		(
			'POST',
			EXCHANGE,
			_ok(
				{
					'access_token': 'unit-dashboard-token',
					'access_expires_at': 4102444800,
					'session': {'sid': 'unit-session-id'},
					'user': {'id': user_id, 'username': 'reader', 'role': 1},
				},
				headers={'Set-Cookie': 'dashboard=unit-dashboard; Path=/; Secure; HttpOnly'},
			),
		),
	]


def _profile(quota=500000):
	return ('GET', PROFILE, _ok({'id': 42, 'quota': quota, 'used_quota': 0}))


def _status(checked, *, allowed=True, **changes):
	return (
		'GET',
		CHECKIN,
		_ok(
			{
				'business_date': TODAY,
				'checked_today': checked,
				'access': {'allowed': allowed, 'reason': 'outstanding_debt' if not allowed else ''},
				'records': [{'checkin_date': TODAY, 'earnings': EARNINGS}] if checked else [],
				'today': {'base_bundle': {'items': [{'points': {'amount': '99'}, 'quantity': 1}]}},
				**changes,
			}
		),
	)


@pytest.fixture
def run_ruachat(monkeypatch):
	client = httpx.AsyncClient
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	monkeypatch.setattr(checkin, 'format_check_in_time', lambda: f'{TODAY} 10:00:00')
	provider = AppConfig.load_from_env().providers['ruachat']

	def forbid_legacy(*args, **kwargs):
		pytest.fail('rua.chat fell back to the obsolete browser login')

	monkeypatch.setattr(checkin_october, '_october_dispatch', forbid_legacy)
	monkeypatch.setattr(checkin, 'login_with_credentials', forbid_legacy)

	async def run(*expected, sso=True, entrypoint='runner', user_id='42'):
		pending = deque(expected)
		account = AccountConfig(
			cookies=None,
			provider='ruachat',
			name='test-ruachat',
			api_user=user_id,
			username=LOGIN['uid_field'] if sso else None,
			password=LOGIN['password'] if sso else None,
			access_token=None if sso else 'unit-pat',
		)

		def handle(request):
			assert pending, f'Unexpected {request.method} {request.url}'
			method, url, response, *form = pending.popleft()
			assert request.method == method
			expected_url = httpx.URL(url)
			assert (request.url if expected_url.query else request.url.copy_with(query=None)) == expected_url
			if url == f'{API}/api/sso/start':
				assert request.url.params['redirect'] == '/rewards/checkin'
			if url == EXECUTOR:
				assert request.url.params['query'] == QUERY
				assert 'Authorization' not in request.headers
				assert 'unit-sso' not in request.headers.get('Cookie', '')
				if method == 'POST':
					assert json.loads(request.content) == form[0]
					assert request.headers['X-authentik-CSRF'] == 'unit-csrf'
			if url == EXCHANGE:
				assert 'exchange=unit-exchange' in request.headers['Cookie']
				assert 'unit-csrf' not in request.headers['Cookie']
			if url in {PROFILE, CHECKIN, POINTS, LOGOUT}:
				token = 'unit-dashboard-token' if sso else 'unit-pat'
				assert request.headers['Authorization'] == f'Bearer {token}'
				if url != LOGOUT:
					assert request.headers['New-Api-User'] == '42'
				if sso:
					assert request.headers['X-Auth-Session'] == 'unit-session-id'
					assert 'dashboard=unit-dashboard' in request.headers['Cookie']
			if url == CHECKIN and method == 'GET':
				assert request.url.params['month'] == TODAY[:7]
			if url in {CHECKIN, LOGOUT} and method == 'POST':
				assert request.content == b''
				assert request.headers['Origin'] == API
			if url != EXECUTOR:
				assert LOGIN['password'].encode() not in request.content
			if isinstance(response, Exception):
				raise response
			return response

		monkeypatch.setattr(
			httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs)
		)
		if entrypoint == 'production':
			result = await checkin_october.dispatch_account(account, account.name, provider)
		elif entrypoint == 'current':
			result = await checkin.check_in_account(account, 0, AppConfig.load_from_env())
		else:
			result = await checkin.run_ruachat_check_in(account)
		assert not pending
		return result

	return run


@pytest.mark.parametrize(('entrypoint', 'already'), [('production', False), ('current', True)])
async def test_daily_reward_confirms_points_without_duplicate_submission(run_ruachat, entrypoint, already):
	requests = [*_login(split_password=already), _profile(), _status(already)]
	if not already:
		requests += [('POST', CHECKIN, _ok()), _status(True)]
	success, before, after = await run_ruachat(
		*requests,
		('GET', POINTS, _ok({'balance': '12.50'})),
		_profile(1000000),
		('POST', LOGOUT, _ok()),
		entrypoint=entrypoint,
	)
	assert success is True and before['quota'] == 1 and after['quota'] == 2
	assert after['points_balance'] == '12.50'
	assert after['ruachat_checkin'] == {
		'business_date': TODAY,
		'points_awarded': '2.50',
		'grant_quota': 500000,
		'recharge_quota': 0,
		'cards': 1,
		'already_checked_in': already,
	}


@pytest.mark.parametrize('user_id', [43, None], ids=['wrong-account', 'invalid-bundle'])
async def test_unverified_identity_logs_out_without_checking_in(run_ruachat, user_id):
	success, _, after = await run_ruachat(*_login(user_id=user_id), ('POST', LOGOUT, _ok()))
	assert success is False and after['check_in_error']


async def test_login_redirect_must_stay_on_official_hosts(run_ruachat, capsys):
	challenge = httpx.Response(
		200, json={'component': 'xak-flow-redirect', 'to': 'https://other.example/test-password'}
	)
	success, _, after = await run_ruachat(*_login(challenge=challenge)[:6])
	assert success is False
	assert LOGIN['password'] not in capsys.readouterr().out + str(after)


async def test_interactive_verification_required_stops_before_login(run_ruachat):
	success, _, after = await run_ruachat(_settings(turnstile_check=True))
	assert success is False and after['check_in_error']


async def test_ineligible_account_does_not_submit_checkin(run_ruachat):
	success, _, after = await run_ruachat(_settings(), _profile(), _status(False, allowed=False), sso=False)
	assert success is False and 'outstanding_debt' in after['check_in_error']


@pytest.mark.parametrize('confirmed', [False, True])
async def test_uncertain_checkin_is_verified_without_resubmitting(run_ruachat, confirmed):
	requests = [
		_settings(),
		_profile(),
		_status(False),
		('POST', CHECKIN, httpx.ReadTimeout('test-private-body')),
		_status(confirmed),
	]
	if confirmed:
		requests += [('GET', POINTS, _ok({'balance': '12.50'})), _profile(1000000)]
	success, _, after = await run_ruachat(*requests, sso=False)
	assert success is confirmed
	assert 'test-private-body' not in str(after)


async def test_reward_preview_is_not_evidence_of_points_received(run_ruachat):
	success, _, after = await run_ruachat(_settings(), _profile(), _status(True, stats=None, records=None), sso=False)
	assert success is False and 'daily points receipt is missing' in after['check_in_error']
	assert 'ruachat_checkin' not in after


@pytest.mark.parametrize('points', [None, '0'], ids=['missing', 'zero'])
async def test_zero_points_is_a_valid_receipt(run_ruachat, points):
	earnings = {key: value for key, value in EARNINGS.items() if key != 'points'}
	requests = [_settings(), _profile()]
	if points is not None:
		earnings['points'] = points
	requests.append(_status(True, records=[{'checkin_date': TODAY, 'earnings': earnings}]))
	if points is not None:
		requests += [('GET', POINTS, _ok({'balance': '12.50'})), _profile()]
	success, _, after = await run_ruachat(*requests, sso=False)
	if points is None:
		assert success is False and 'points amount is missing' in after['check_in_error']
	else:
		assert success is True and after['ruachat_checkin']['points_awarded'] == '0'
