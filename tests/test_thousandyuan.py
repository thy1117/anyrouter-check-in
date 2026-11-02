import json
from pathlib import Path

import httpx
import pytest

import checkin
from utils.config import AccountConfig, AppConfig, load_accounts_config


def test_thousandyuan_provider_and_workflow_slot(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	provider = AppConfig.load_from_env().providers['thousandyuan']

	assert provider.domain == 'https://api.thousandyuan.dpdns.org'
	assert provider.login_path == '/sign-in'
	assert provider.login_api_path == '/api/user/login?turnstile='
	assert provider.sign_in_path == provider.check_in_status_path == '/api/user/checkin'
	assert provider.user_info_path == '/api/user/self'
	assert provider.api_user_key == ''
	assert provider.use_proxy is False

	workflow = Path('.github/workflows/checkin.yml').read_text()
	assert 'EXTRA_ACCOUNTS_124: ' not in workflow
	assert (
		"CHECKIN_TARGET_ACCOUNTS: ${{ github.event_name == 'schedule' && 'vsllm' || inputs.target_accounts }}"
		in workflow
	)


def test_thousandyuan_accounts_load_from_one_slot(monkeypatch):
	for name in list(checkin.os.environ):
		if name == 'ANYROUTER_ACCOUNTS' or name.startswith('EXTRA_ACCOUNTS'):
			monkeypatch.delenv(name)
	accounts = [
		{
			'name': f'ThousandYuan-thy{number}',
			'provider': 'thousandyuan',
			'username': f'thy{number}',
			'password': 'test-password',
		}
		for number in range(1117, 1124)
	]
	monkeypatch.setenv('EXTRA_ACCOUNTS_124', json.dumps(accounts))

	loaded = load_accounts_config()
	assert loaded is not None
	assert [account.username for account in loaded] == [f'thy{number}' for number in range(1117, 1124)]
	assert all(account.provider == 'thousandyuan' and account.password == 'test-password' for account in loaded)


@pytest.mark.parametrize('already_checked', [False, True])
async def test_thousandyuan_password_login_checkin_and_logout(monkeypatch, already_checked):
	provider = AppConfig.load_from_env().providers['thousandyuan']
	account = AccountConfig(
		cookies=None,
		name='ThousandYuan-thy1117',
		provider='thousandyuan',
		username='thy1117',
		password='test-password',
	)
	requests = []

	def handler(request):
		requests.append(request)
		if request.method == 'POST' and request.url.path == '/api/user/login':
			assert request.url.query == b'turnstile='
			assert json.loads(request.read()) == {'username': 'thy1117', 'password': 'test-password'}
			return httpx.Response(
				200,
				json={
					'success': True,
					'data': {
						'access_token': 'access-token',
						'user': {'id': 1117, 'username': 'thy1117'},
						'session': {'sid': 'session-id'},
					},
				},
			)
		if request.method == 'GET' and request.url.path == '/api/user/self':
			assert request.headers['Authorization'] == 'Bearer access-token'
			assert 'New-Api-User' not in request.headers
			return httpx.Response(200, json={'success': True, 'data': {'id': 1117, 'quota': 2000000, 'used_quota': 0}})
		if request.method == 'POST' and request.url.path == '/api/user/checkin':
			assert request.headers['Authorization'] == 'Bearer access-token'
			assert request.content == b''
			if already_checked:
				return httpx.Response(400, json={'success': False, 'message': 'Already checked in today'})
			return httpx.Response(200, json={'success': True, 'data': {'quota_awarded': 500000}})
		if request.method == 'POST' and request.url.path == '/api/user/auth/logout':
			assert request.headers['Authorization'] == 'Bearer access-token'
			assert request.headers['X-Auth-Session'] == 'session-id'
			return httpx.Response(200, json={'success': True})
		raise AssertionError(f'unexpected request: {request.method} {request.url}')

	client_type = httpx.Client
	monkeypatch.setattr(
		checkin.httpx,
		'Client',
		lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs),
	)

	success, before, after = await checkin._run_account_checkin(account, account.name, provider)
	assert success
	assert before and before['success']
	assert after and after['success']
	assert [request.url.path for request in requests] == [
		'/api/user/login',
		'/api/user/self',
		'/api/user/checkin',
		'/api/user/self',
		'/api/user/auth/logout',
	]
