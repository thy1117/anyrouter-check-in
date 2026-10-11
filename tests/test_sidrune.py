import json
from pathlib import Path

import httpx
import pytest

from checkin import exchange_sidrune_welfare_balance, run_bearer_check_in
from utils.config import AccountConfig, AppConfig, _account_env_names, load_accounts_config


def sidrune_provider(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	return AppConfig.load_from_env().providers['sidrune']


def test_sidrune_uses_welfare_api_and_existing_token_refresh(monkeypatch):
	provider = sidrune_provider(monkeypatch)
	assert provider.domain == 'https://sidrune.ai'
	assert provider.api_style == 'sub2api'
	assert provider.login_path == '/welfare'
	assert provider.sign_in_path == '/api/v1/welfare/checkin'
	assert provider.check_in_status_path == '/api/v1/welfare/profile'
	assert provider.user_info_path == '/api/v1/auth/me'
	assert provider.auth_refresh_path == '/api/v1/auth/refresh'
	assert provider.api_user_key == ''
	assert provider.use_proxy is False


def test_sidrune_slot_appends_without_overwriting_existing_accounts(monkeypatch):
	for name in _account_env_names():
		monkeypatch.delenv(name, raising=False)
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', json.dumps([{'name': 'existing', 'access_token': 'existing-token'}]))
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_67',
		json.dumps(
			[
				{
					'name': 'Sidrune-dodo',
					'provider': 'sidrune',
					'access_token': 'test-access',
					'refresh_token': 'test-refresh',
				}
			]
		),
	)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_68',
		json.dumps(
			[
				{
					'name': 'Sidrune-tthxyc',
					'provider': 'sidrune',
					'access_token': 'second-access',
					'refresh_token': 'second-refresh',
				}
			]
		),
	)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_69',
		json.dumps(
			[
				{
					'name': 'Sidrune-5237',
					'provider': 'sidrune',
					'access_token': 'third-access',
					'refresh_token': 'third-refresh',
				}
			]
		),
	)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_70',
		json.dumps(
			[
				{
					'name': 'Sidrune-8746',
					'provider': 'sidrune',
					'access_token': 'fourth-access',
					'refresh_token': 'fourth-refresh',
				}
			]
		),
	)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_71',
		json.dumps(
			[
				{
					'name': 'Sidrune-3069',
					'provider': 'sidrune',
					'access_token': 'fifth-access',
					'refresh_token': 'fifth-refresh',
				}
			]
		),
	)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_72',
		json.dumps(
			[
				{
					'name': 'Sidrune-9308',
					'provider': 'sidrune',
					'access_token': 'sixth-access',
					'refresh_token': 'sixth-refresh',
				}
			]
		),
	)
	accounts = load_accounts_config()
	assert accounts is not None
	assert [account.name for account in accounts] == [
		'existing',
		'Sidrune-dodo',
		'Sidrune-tthxyc',
		'Sidrune-5237',
		'Sidrune-8746',
		'Sidrune-3069',
		'Sidrune-9308',
	]
	assert accounts[0].access_token == 'existing-token'
	assert accounts[1].provider == 'sidrune'
	assert accounts[1].access_token == 'test-access'
	assert accounts[1].refresh_token == 'test-refresh'
	assert accounts[2].provider == 'sidrune'
	assert accounts[2].access_token == 'second-access'
	assert accounts[2].refresh_token == 'second-refresh'
	assert accounts[3].provider == 'sidrune'
	assert accounts[3].access_token == 'third-access'
	assert accounts[3].refresh_token == 'third-refresh'
	assert accounts[4].provider == 'sidrune'
	assert accounts[4].access_token == 'fourth-access'
	assert accounts[4].refresh_token == 'fourth-refresh'
	assert accounts[5].provider == 'sidrune'
	assert accounts[5].access_token == 'fifth-access'
	assert accounts[5].refresh_token == 'fifth-refresh'
	assert accounts[6].provider == 'sidrune'
	assert accounts[6].access_token == 'sixth-access'
	assert accounts[6].refresh_token == 'sixth-refresh'
	workflow = Path(__file__).resolve().parents[1] / '.github/workflows/checkin.yml'
	assert 'EXTRA_ACCOUNTS_67: ${{ secrets.EXTRA_ACCOUNTS_67 }}' in workflow.read_text()
	assert 'EXTRA_ACCOUNTS_68: ${{ secrets.EXTRA_ACCOUNTS_68 }}' in workflow.read_text()
	assert 'EXTRA_ACCOUNTS_69: ${{ secrets.EXTRA_ACCOUNTS_69 }}' in workflow.read_text()
	assert 'EXTRA_ACCOUNTS_70: ${{ secrets.EXTRA_ACCOUNTS_70 }}' in workflow.read_text()
	assert 'EXTRA_ACCOUNTS_71: ${{ secrets.EXTRA_ACCOUNTS_71 }}' in workflow.read_text()
	assert 'EXTRA_ACCOUNTS_72: ${{ secrets.EXTRA_ACCOUNTS_72 }}' in workflow.read_text()


@pytest.mark.parametrize(
	('checked', 'eligible', 'success', 'submissions'),
	[(False, True, True, 1), (True, True, True, 0), (False, False, False, 0)],
)
def test_sidrune_welfare_checkin_respects_status(monkeypatch, checked, eligible, success, submissions):
	provider = sidrune_provider(monkeypatch)
	account = AccountConfig(cookies=None, name='Sidrune-dodo', provider='sidrune', access_token='test-access')
	requests = []
	checked_today = checked

	def handler(request):
		nonlocal checked_today
		requests.append(request)
		assert request.headers['Authorization'] == 'Bearer test-access'
		if request.url.path == '/api/v1/auth/me':
			assert request.method == 'GET'
			return httpx.Response(200, json={'code': 0, 'data': {'balance': 2.5}})
		if request.url.path == '/api/v1/welfare/profile':
			assert request.method == 'GET'
			return httpx.Response(
				200, json={'code': 0, 'data': {'checked_in_today': checked_today, 'eligible': eligible, 'balance': 0}}
			)
		assert request.url.path == '/api/v1/welfare/checkin' and request.method == 'POST'
		checked_today = True
		return httpx.Response(200, json={'code': 0, 'data': {'streak_day': 1, 'reward_amount': 0.1}})

	client = httpx.Client
	monkeypatch.setattr(
		'checkin.httpx.Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)
	monkeypatch.setattr('checkin.create_xiaobai_token_state', lambda *args: None)
	result, before, after = run_bearer_check_in(account, 'Sidrune-dodo', provider)
	assert result is success
	assert before is not None and after is not None
	assert before['quota'] == after['quota'] == 2.5
	assert len([request for request in requests if request.method == 'POST']) == submissions


@pytest.fixture
def welfare_session(monkeypatch):
	provider = sidrune_provider(monkeypatch)
	client = httpx.Client
	monkeypatch.setattr('checkin.create_xiaobai_token_state', lambda *args: None)

	def run(
		*, checked=True, profile=None, exchange_response=None, checkin_status=200, confirmed=True, name='Sidrune-dodo'
	):
		account = AccountConfig(cookies=None, name=name, provider='sidrune', access_token=f'test-{name}')
		welfare = {
			'checked_in_today': checked,
			'eligible': True,
			'balance': 0 if not checked else 1.09272526,
			'exchange_unlocked': True,
			'exchange_spending_threshold': 0,
			**(profile or {}),
		}
		requests = []
		wallet = 2.5

		def handler(request):
			nonlocal wallet
			requests.append(request)
			assert request.url.host == 'sidrune.ai'
			assert request.headers['Authorization'] == f'Bearer test-{name}'
			if request.url.path == '/api/v1/auth/me':
				assert request.method == 'GET'
				return httpx.Response(200, json={'code': 0, 'data': {'balance': wallet}})
			if request.url.path == '/api/v1/welfare/profile':
				assert request.method == 'GET'
				return httpx.Response(200, json={'code': 0, 'data': welfare})
			if request.url.path == '/api/v1/welfare/checkin':
				assert request.method == 'POST'
				if checkin_status == 200:
					welfare['checked_in_today'] = confirmed
					welfare['balance'] = 1.09272526
				return httpx.Response(checkin_status, json={'code': 0, 'data': {'reward_amount': 1.09272526}})
			assert request.url.path == '/api/v1/welfare/exchange/balance' and request.method == 'POST'
			if isinstance(exchange_response, Exception):
				raise exchange_response
			response = exchange_response or httpx.Response(200, json={'code': 0, 'data': {}})
			if response.status_code == 200 and response.headers.get('content-type') == 'application/json':
				payload = response.json()
				if isinstance(payload, dict) and payload.get('code') == 0 and payload.get('success') is not False:
					wallet += json.loads(request.content)['amount']
					welfare['balance'] = 0
			return response

		monkeypatch.setattr(
			'checkin.httpx.Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
		)
		return run_bearer_check_in(account, name, provider), requests

	return run


@pytest.mark.parametrize('checked', [False, True])
def test_sidrune_exchanges_fresh_full_welfare_balance_after_checkin(welfare_session, checked):
	(success, before, after), requests = welfare_session(checked=checked)
	assert success is True
	assert before['quota'] == 2.5 and after['quota'] == 3.5927
	posts = [request for request in requests if request.method == 'POST']
	assert len(posts) == (1 if checked else 2)
	assert json.loads(posts[-1].content) == {'amount': 1.09272526}
	assert requests[-3].url.path == '/api/v1/welfare/profile'
	assert requests[-2].url.path == '/api/v1/welfare/exchange/balance'
	assert requests[-1].url.path == '/api/v1/auth/me'


@pytest.mark.parametrize(
	'profile',
	[
		{'balance': 0},
		{'exchange_unlocked': False},
		{'exchange_unlocked': None},
		{'exchange_spending_threshold': 5, 'exchange_spending_met': False},
		{'exchange_spending_threshold': 5},
	],
)
def test_sidrune_skips_exchange_until_balance_and_gates_allow_it(welfare_session, profile):
	(success, before, after), requests = welfare_session(profile=profile)
	assert success is True and before['quota'] == after['quota'] == 2.5
	assert all(request.method == 'GET' for request in requests)


def test_sidrune_exchanges_when_spending_gate_is_met(welfare_session):
	(success, _, _), requests = welfare_session(
		profile={'exchange_spending_threshold': 5, 'exchange_spending_met': True}
	)
	assert success is True
	assert len([request for request in requests if request.method == 'POST']) == 1


@pytest.mark.parametrize('amount', [None, '1.11', True, -1, float('nan'), float('inf')])
def test_sidrune_invalid_welfare_balance_never_submits(monkeypatch, amount):
	provider = sidrune_provider(monkeypatch)
	requests = []

	def handler(request):
		requests.append(request)
		return httpx.Response(200, json={'code': 0})

	with httpx.Client(transport=httpx.MockTransport(handler)) as client:
		error = exchange_sidrune_welfare_balance(
			client, 'Sidrune-dodo', provider, {}, {'balance': amount, 'exchange_unlocked': True}
		)
	assert error is not None and 'invalid' in error
	assert requests == []


@pytest.mark.parametrize(
	'response',
	[
		httpx.Response(401, json={'message': 'private-token'}),
		httpx.Response(502, text='<html>private-token</html>'),
		httpx.Response(200, text='private-token'),
		httpx.Response(200, json={'code': 'private-token', 'message': 'private-token'}),
		httpx.Response(200, json={'success': True}),
		httpx.Response(200, json={'code': 0, 'success': False}),
		httpx.Response(200, json=[]),
		httpx.ReadTimeout('private-token'),
		ValueError('private-token'),
	],
)
def test_sidrune_exchange_failure_is_reported_without_replay_or_secrets(welfare_session, capsys, response):
	(success, before, after), requests = welfare_session(exchange_response=response)
	assert success is False and before['quota'] == after['quota'] == 2.5
	assert 'exchange' in after['check_in_error'] and 'no retry' in after['check_in_error']
	assert len([request for request in requests if request.method == 'POST']) == 1
	assert requests[-1].url.path == '/api/v1/auth/me'
	assert 'private-token' not in after['check_in_error'] + capsys.readouterr().out


@pytest.mark.parametrize(('checkin_status', 'confirmed'), [(502, True), (200, False)])
def test_sidrune_failed_or_unconfirmed_checkin_does_not_exchange(welfare_session, checkin_status, confirmed):
	(success, _, after), requests = welfare_session(checked=False, checkin_status=checkin_status, confirmed=confirmed)
	assert success is False
	assert after['check_in_error']
	assert [request.url.path for request in requests if request.method == 'POST'] == ['/api/v1/welfare/checkin']


def test_sidrune_all_six_accounts_use_their_own_session_and_balance(welfare_session):
	for index, suffix in enumerate(['dodo', 'tthxyc', '5237', '8746', '3069', '9308']):
		amount = (index + 1) / 10
		(success, before, after), requests = welfare_session(name=f'Sidrune-{suffix}', profile={'balance': amount})
		assert success is True and before['quota'] == 2.5 and after['quota'] == round(2.5 + amount, 4)
		posts = [request for request in requests if request.method == 'POST']
		assert len(posts) == 1 and json.loads(posts[0].content) == {'amount': amount}
