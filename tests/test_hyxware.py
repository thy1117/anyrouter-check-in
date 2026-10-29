import json
from pathlib import Path

import httpx
import pytest

import checkin
from utils.config import AppConfig, load_accounts_config


@pytest.mark.parametrize(
	('status_code', 'result', 'expected'),
	[
		(200, {'success': True}, True),
		(400, {'success': False, 'message': 'Already checked in today'}, True),
		(500, {'success': False, 'message': 'server error'}, False),
	],
)
async def test_hyxware_account_uses_existing_http_checkin(monkeypatch, status_code, result, expected):
	for name in list(checkin.os.environ):
		if name in ('PROVIDERS', 'EXTRA_PROVIDERS', 'ANYROUTER_ACCOUNTS') or name.startswith('EXTRA_ACCOUNTS'):
			monkeypatch.delenv(name)
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', json.dumps([{'name': 'Existing', 'access_token': 'old'}]))
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_114',
		json.dumps([{'name': 'HyxAPI-thy1117', 'provider': 'hyxware', 'api_user': '2817', 'access_token': 'test-pat'}]),
	)
	accounts = load_accounts_config()
	assert [account.name for account in accounts] == ['Existing', 'HyxAPI-thy1117']
	provider = AppConfig.load_from_env().providers['hyxware']
	assert provider.use_proxy is False
	requests = []

	def handler(request):
		requests.append(request)
		assert request.url.host == 'hyxware.com'
		assert request.headers['Authorization'] == 'Bearer test-pat'
		assert request.headers['New-Api-User'] == '2817'
		if request.method == 'GET':
			assert request.url.path == '/api/user/self'
			return httpx.Response(200, json={'success': True, 'data': {'id': 2817, 'quota': 2500000, 'used_quota': 0}})
		assert request.method == 'POST' and request.url.path == '/api/user/checkin'
		return httpx.Response(status_code, json=result)

	client = httpx.Client
	monkeypatch.setattr(
		checkin.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)
	success, before, after = await checkin._run_account_checkin(accounts[-1], 'HyxAPI-thy1117', provider)
	assert success is expected and before['success'] and after['success']
	assert ('check_in_error' in after) is (not expected)
	assert [request.method for request in requests] == ['GET', 'POST', 'GET']
	workflow = Path('.github/workflows/checkin.yml').read_text()
	mapping = 'EXTRA_ACCOUNTS_114: ${{ secrets.EXTRA_ACCOUNTS_114 }}'
	assert mapping in workflow.split('\n  gorouter-status:', 1)[0]
	assert workflow.count(mapping) == 1
