import json
from pathlib import Path

import httpx
import pytest

from checkin import (
	SafeLineAuth,
	parse_sub2api_profile_response,
	run_bearer_check_in,
	solve_safeline_guard,
)
from utils.config import AccountConfig, AppConfig, _account_env_names, load_accounts_config


def lunora_provider(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	return AppConfig.load_from_env().providers['lunora']


def test_lunora_uses_checkin_api_and_existing_token_refresh(monkeypatch):
	provider = lunora_provider(monkeypatch)
	assert provider.domain == 'https://www.uselunora.com'
	assert provider.api_style == 'sub2api'
	assert provider.login_path == '/checkin'
	assert provider.sign_in_path == '/api/v1/checkin/claim'
	assert provider.check_in_status_path == '/api/v1/checkin/status'
	assert provider.user_info_path == '/api/v1/auth/me'
	assert provider.auth_refresh_path == '/api/v1/auth/refresh'
	assert provider.api_user_key == ''
	assert provider.use_proxy is False


def test_lunora_slot_appends_without_overwriting_existing_accounts(monkeypatch):
	for name in _account_env_names():
		monkeypatch.delenv(name, raising=False)
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', json.dumps([{'name': 'existing', 'access_token': 'existing-token'}]))
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_78',
		json.dumps(
			[
				{
					'name': 'Lunora-1125',
					'provider': 'lunora',
					'access_token': 'test-access',
					'refresh_token': 'test-refresh',
				}
			]
		),
	)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_79',
		json.dumps(
			[
				{
					'name': 'Lunora-5237',
					'provider': 'lunora',
					'access_token': 'second-access',
					'refresh_token': 'second-refresh',
				}
			]
		),
	)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_80',
		json.dumps(
			[
				{
					'name': 'Lunora-8746',
					'provider': 'lunora',
					'access_token': 'third-access',
					'refresh_token': 'third-refresh',
				}
			]
		),
	)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_81',
		json.dumps(
			[
				{
					'name': 'Lunora-9308',
					'provider': 'lunora',
					'access_token': 'fourth-access',
					'refresh_token': 'fourth-refresh',
				}
			]
		),
	)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_82',
		json.dumps(
			[
				{
					'name': 'Lunora-3069',
					'provider': 'lunora',
					'access_token': 'fifth-access',
					'refresh_token': 'fifth-refresh',
				}
			]
		),
	)
	accounts = load_accounts_config()
	assert accounts is not None
	assert [account.name for account in accounts] == [
		'existing',
		'Lunora-1125',
		'Lunora-5237',
		'Lunora-8746',
		'Lunora-9308',
		'Lunora-3069',
	]
	assert accounts[0].access_token == 'existing-token'
	assert accounts[1].provider == 'lunora'
	assert accounts[1].access_token == 'test-access'
	assert accounts[1].refresh_token == 'test-refresh'
	assert accounts[2].provider == 'lunora'
	assert accounts[2].access_token == 'second-access'
	assert accounts[2].refresh_token == 'second-refresh'
	assert accounts[3].provider == 'lunora'
	assert accounts[3].access_token == 'third-access'
	assert accounts[3].refresh_token == 'third-refresh'
	assert accounts[4].provider == 'lunora'
	assert accounts[4].access_token == 'fourth-access'
	assert accounts[4].refresh_token == 'fourth-refresh'
	assert accounts[5].provider == 'lunora'
	assert accounts[5].access_token == 'fifth-access'
	assert accounts[5].refresh_token == 'fifth-refresh'
	workflow = Path(__file__).resolve().parents[1] / '.github/workflows/checkin.yml'
	assert 'EXTRA_ACCOUNTS_78: ${{ secrets.EXTRA_ACCOUNTS_78 }}' in workflow.read_text()
	assert 'EXTRA_ACCOUNTS_79: ${{ secrets.EXTRA_ACCOUNTS_79 }}' in workflow.read_text()
	assert 'EXTRA_ACCOUNTS_80: ${{ secrets.EXTRA_ACCOUNTS_80 }}' in workflow.read_text()
	assert 'EXTRA_ACCOUNTS_81: ${{ secrets.EXTRA_ACCOUNTS_81 }}' in workflow.read_text()
	assert 'EXTRA_ACCOUNTS_82: ${{ secrets.EXTRA_ACCOUNTS_82 }}' in workflow.read_text()


@pytest.mark.parametrize(
	('checked', 'eligible', 'success', 'submissions'),
	[(False, True, True, 1), (True, True, True, 0), (False, False, False, 0)],
)
def test_lunora_checkin_respects_status(monkeypatch, checked, eligible, success, submissions):
	provider = lunora_provider(monkeypatch)
	account = AccountConfig(cookies=None, name='Lunora-1125', provider='lunora', access_token='test-access')
	requests = []

	def handler(request):
		requests.append(request)
		assert request.headers['Authorization'] == 'Bearer test-access'
		if request.url.path == '/api/v1/auth/me':
			assert request.method == 'GET'
			return httpx.Response(200, json={'code': 0, 'data': {'balance': 0.0828}})
		if request.url.path == '/api/v1/checkin/status':
			assert request.method == 'GET'
			return httpx.Response(200, json={'code': 0, 'data': {'checked_today': checked, 'eligible': eligible}})
		assert request.url.path == '/api/v1/checkin/claim' and request.method == 'POST'
		return httpx.Response(
			200, json={'code': 0, 'data': {'record': {'attendance_day': 1, 'reward_amount': 0.1}, 'created': True}}
		)

	client = httpx.Client
	monkeypatch.setattr(
		'checkin.httpx.Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)
	monkeypatch.setattr('checkin.create_xiaobai_token_state', lambda *args: None)
	result, before, after = run_bearer_check_in(account, 'Lunora-1125', provider)
	assert result is success
	assert before is not None and after is not None
	assert before['quota'] == after['quota'] == 0.0828
	assert len([request for request in requests if request.method == 'POST']) == submissions


def test_lunora_ineligible_reason_extracted(monkeypatch):
	provider = lunora_provider(monkeypatch)
	account = AccountConfig(cookies=None, name='Lunora-8746', provider='lunora', access_token='test-access')

	def handler(request):
		if request.url.path == '/api/v1/auth/me':
			return httpx.Response(200, json={'code': 0, 'data': {'balance': 1.36}})
		if request.url.path == '/api/v1/checkin/status':
			return httpx.Response(
				200,
				json={
					'code': 0,
					'data': {
						'checked_today': False,
						'eligible': False,
						'ineligible_reason': 'recent_usage_required',
					},
				},
			)
		return httpx.Response(404)

	client = httpx.Client
	monkeypatch.setattr(
		'checkin.httpx.Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)
	monkeypatch.setattr('checkin.create_xiaobai_token_state', lambda *args: None)
	result, before, after = run_bearer_check_in(account, 'Lunora-8746', provider)
	assert result is False
	assert '近 30 天内需有真实 API 使用记录' in after['check_in_error']


def test_solve_safeline_guard_valid_and_invalid():
	assert solve_safeline_guard('28cd9ec3O1Ac24') is not None
	assert solve_safeline_guard(None) is None
	assert solve_safeline_guard('') is None
	assert solve_safeline_guard('short') is None
	assert solve_safeline_guard('28cd9ec3O1Acnotdigits') is None


def test_parse_sub2api_profile_response_safeline_intercepted():
	safeline_html = '<script src="/_guard/auto.js"></script>'
	res = parse_sub2api_profile_response(200, safeline_html)
	assert res['success'] is False
	assert res['error'] == 'Failed to get user info: SafeLine WAF challenge intercepted'


def test_safeline_auth_flow():
	calls = []

	def handler(request: httpx.Request):
		calls.append(request)
		cookie_header = request.headers.get('cookie', '')
		if 'guard=' not in cookie_header or 'guardret=' not in cookie_header:
			return httpx.Response(
				200,
				text='<script src="/_guard/auto.js"></script>',
				headers={'Set-Cookie': 'guard=28cd9ec3O1Ac24; path=/'},
			)
		return httpx.Response(200, json={'code': 0, 'data': {'ok': True}})

	client = httpx.Client(transport=httpx.MockTransport(handler), auth=SafeLineAuth())
	resp1 = client.get('https://www.uselunora.com/api/v1/auth/me')
	assert resp1.status_code == 200
	assert resp1.json() == {'code': 0, 'data': {'ok': True}}
	# First request caused 2 transport calls (initial challenge + solved retry)
	assert len(calls) == 2

	# Second request should use cached cookies directly without triggering challenge
	resp2 = client.get('https://www.uselunora.com/api/v1/checkin/status')
	assert resp2.status_code == 200
	assert len(calls) == 3


def test_lunora_checkin_with_safeline_waf_challenge(monkeypatch):
	provider = lunora_provider(monkeypatch)
	account = AccountConfig(cookies=None, name='Lunora-1125', provider='lunora', access_token='test-access')
	solved_cookies = {}

	def handler(request: httpx.Request):
		cookie = request.headers.get('cookie', '')
		if 'guard=' not in cookie or 'guardret=' not in cookie:
			return httpx.Response(
				200,
				text='<script src="/_guard/auto.js"></script>',
				headers={'Set-Cookie': 'guard=701b9bdcgFN968; path=/'},
			)
		solved_cookies['guard'] = True
		if request.url.path == '/api/v1/auth/me':
			return httpx.Response(200, json={'code': 0, 'data': {'balance': 1.5}})
		if request.url.path == '/api/v1/checkin/status':
			return httpx.Response(200, json={'code': 0, 'data': {'checked_today': False, 'eligible': True}})
		if request.url.path == '/api/v1/checkin/claim':
			return httpx.Response(
				200, json={'code': 0, 'data': {'record': {'attendance_day': 1, 'reward_amount': 0.1}, 'created': True}}
			)
		return httpx.Response(404)

	client = httpx.Client
	monkeypatch.setattr(
		'checkin.httpx.Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)
	monkeypatch.setattr('checkin.create_xiaobai_token_state', lambda *args: None)
	result, before, after = run_bearer_check_in(account, 'Lunora-1125', provider)
	assert result is True
	assert solved_cookies.get('guard') is True
	assert before is not None and after is not None
	assert before['quota'] == 1.5
	assert after['quota'] == 1.5
