import json
from pathlib import Path

import httpx
import pytest

import checkin
from utils.config import AccountConfig, AppConfig, load_accounts_config


def test_chuanapi_account_slot_keeps_existing_accounts(monkeypatch):
	for name in list(checkin.os.environ):
		if name == 'ANYROUTER_ACCOUNTS' or name.startswith('EXTRA_ACCOUNTS'):
			monkeypatch.delenv(name)
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', json.dumps([{'name': 'Existing', 'access_token': 'old'}]))
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_113',
		json.dumps([{'name': 'ChuanAPI-146', 'provider': 'chuanapi', 'api_user': '146', 'access_token': 'test-pat'}]),
	)
	accounts = load_accounts_config()
	assert [account.name for account in accounts] == ['Existing', 'ChuanAPI-146']
	assert (accounts[-1].provider, accounts[-1].api_user, accounts[-1].access_token) == (
		'chuanapi',
		'146',
		'test-pat',
	)
	workflow = Path('.github/workflows/checkin.yml').read_text()
	mapping = 'EXTRA_ACCOUNTS_113: ${{ secrets.EXTRA_ACCOUNTS_113 }}'
	assert mapping in workflow.split('\n  gorouter-status:', 1)[0]
	assert workflow.count(mapping) == 1


@pytest.mark.parametrize(
	('status_code', 'result', 'expected'),
	[
		(200, {'success': True}, True),
		(400, {'success': False, 'message': '今日已签到'}, True),
		(500, {'success': False, 'message': 'server error'}, False),
	],
)
async def test_chuanapi_uses_authenticated_http_checkin(monkeypatch, status_code, result, expected):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	provider = AppConfig.load_from_env().providers['chuanapi']
	account = AccountConfig(cookies=None, provider='chuanapi', access_token='test-pat', api_user='146')
	assert provider.use_proxy is False
	requests = []

	def handler(request):
		requests.append(request)
		assert request.url.host == 'chuanapi.com'
		assert request.headers['Authorization'] == 'Bearer test-pat'
		assert request.headers['New-Api-User'] == '146'
		if request.method == 'GET':
			assert request.url.path == '/api/user/self'
			return httpx.Response(200, json={'success': True, 'data': {'id': 146, 'quota': 2500000, 'used_quota': 0}})
		assert request.method == 'POST' and request.url.path == '/api/user/checkin'
		return httpx.Response(status_code, json=result)

	client = httpx.Client
	monkeypatch.setattr(
		checkin.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)
	success, before, after = await checkin._run_account_checkin(account, 'ChuanAPI-146', provider)
	assert success is expected and before['success'] and after['success']
	assert ('check_in_error' in after) is (not expected)
	assert [request.method for request in requests] == ['GET', 'POST', 'GET']
