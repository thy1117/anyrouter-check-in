import json
from pathlib import Path

import httpx
import pytest

import checkin
from utils.config import AccountConfig, AppConfig, _account_env_names, load_accounts_config


def test_newcodex_slots_append_accounts_and_map_only_regular_job(monkeypatch):
	for name in _account_env_names():
		monkeypatch.delenv(name, raising=False)
	monkeypatch.delenv('CHECKIN_DISABLED_PROVIDERS', raising=False)
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', json.dumps([{'name': 'Existing', 'access_token': 'old-fake-token'}]))
	for slot, name, api_user in (
		(132, 'NewCodex-1117', '19'),
		(133, 'NewCodex-1118', '430'),
		(134, 'NewCodex-1119', '479'),
		(135, 'NewCodex-1120', '480'),
		(136, 'NewCodex-1121', '481'),
		(137, 'NewCodex-1122', '482'),
	):
		monkeypatch.setenv(
			f'EXTRA_ACCOUNTS_{slot}',
			json.dumps(
				[{'name': name, 'provider': 'newcodex', 'api_user': api_user, 'access_token': f'fake-pat-{slot}'}]
			),
		)
	accounts = load_accounts_config()
	assert [account.name for account in accounts] == [
		'Existing',
		'NewCodex-1117',
		'NewCodex-1118',
		'NewCodex-1119',
		'NewCodex-1120',
		'NewCodex-1121',
		'NewCodex-1122',
	]
	assert [(account.provider, account.api_user, account.access_token) for account in accounts[1:]] == [
		('newcodex', '19', 'fake-pat-132'),
		('newcodex', '430', 'fake-pat-133'),
		('newcodex', '479', 'fake-pat-134'),
		('newcodex', '480', 'fake-pat-135'),
		('newcodex', '481', 'fake-pat-136'),
		('newcodex', '482', 'fake-pat-137'),
	]
	workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/checkin.yml').read_text()
	regular_job = workflow.split('\n  checkin:\n', 1)[1].split('\n  gorouter-status:', 1)[0]
	for slot in (132, 133, 134, 135, 136, 137):
		mapping = f'EXTRA_ACCOUNTS_{slot}: ${{{{ secrets.EXTRA_ACCOUNTS_{slot} }}}}'
		assert mapping in regular_job
		assert workflow.count(mapping) == 1


@pytest.mark.parametrize('api_user', ['19', '430', '479', '480', '481', '482'])
@pytest.mark.parametrize('already_checked_in', [False, True])
async def test_newcodex_uses_authenticated_http_checkin(monkeypatch, api_user, already_checked_in):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	provider = AppConfig.load_from_env().providers['newcodex']
	assert provider.login_path == '/profile'
	assert provider.use_proxy is False and provider.checkin_turnstile is False
	account = AccountConfig(cookies=None, provider='newcodex', access_token='fake-newcodex-pat', api_user=api_user)
	requests = []

	def handler(request):
		requests.append(request)
		assert request.url.host == 'newcodex.cn'
		assert request.headers['Authorization'] == 'Bearer fake-newcodex-pat'
		assert request.headers['New-Api-User'] == api_user
		if request.method == 'GET':
			assert request.url.path == '/api/user/self'
			return httpx.Response(200, json={'success': True, 'data': {'id': int(api_user), 'quota': 2500000}})
		assert request.method == 'POST' and request.url.path == '/api/user/checkin'
		if already_checked_in:
			return httpx.Response(400, json={'success': False, 'message': '今日已签到'})
		return httpx.Response(200, json={'success': True})

	client = httpx.Client
	monkeypatch.setattr(
		checkin.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)
	success, before, after = await checkin._run_account_checkin(account, 'NewCodex-test', provider)
	assert success is True and before['success'] and after['success']
	assert [request.method for request in requests] == ['GET', 'POST', 'GET']
