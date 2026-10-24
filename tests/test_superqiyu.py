import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import checkin
from utils.config import AccountConfig, AppConfig, load_accounts_config


def test_superqiyu_configuration_and_account_slot(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	provider = AppConfig.load_from_env().providers['superqiyu']
	assert provider.domain == 'https://www.superqiyu.org'
	assert provider.login_path == '/profile'
	assert provider.user_info_path == '/api/user/self'
	assert provider.sign_in_path == provider.check_in_status_path == '/api/user/checkin'
	assert provider.api_user_key == 'New-Api-User'
	assert provider.checkin_turnstile and provider.persist_profile
	assert provider.turnstile_site_key == '0x4AAAAAAFSF3WVv-F5gEfno'
	assert provider.use_proxy is False
	for name in list(checkin.os.environ):
		if name == 'ANYROUTER_ACCOUNTS' or name.startswith('EXTRA_ACCOUNTS'):
			monkeypatch.delenv(name)
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', json.dumps([{'name': 'Existing', 'access_token': 'old'}]))
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_110',
		json.dumps([{'name': 'SuperQiyu-554', 'provider': 'superqiyu', 'api_user': '554', 'access_token': 'test'}]),
	)
	accounts = load_accounts_config()
	assert [account.name for account in accounts] == ['Existing', 'SuperQiyu-554']
	assert accounts[-1].provider == 'superqiyu' and accounts[-1].api_user == '554'
	workflow = Path('.github/workflows/checkin.yml').read_text().split('\n  gorouter-status:', 1)[0]
	assert 'EXTRA_ACCOUNTS_110: ${{ secrets.EXTRA_ACCOUNTS_110 }}' in workflow


@pytest.mark.parametrize('checked', [False, True])
async def test_superqiyu_reuses_turnstile_flow_and_confirms_status(monkeypatch, checked):
	provider = AppConfig.load_from_env().providers['superqiyu']
	account = AccountConfig(cookies=None, provider='superqiyu', access_token='test-pat', api_user='554')
	page = SimpleNamespace(goto=AsyncMock())
	context = SimpleNamespace(
		new_page=AsyncMock(return_value=page), cookies=AsyncMock(return_value=[]), close=AsyncMock()
	)
	monkeypatch.setattr(checkin, 'load_browser_login_settings', lambda *a, **kw: SimpleNamespace(wait_timeout_ms=1000))
	monkeypatch.setattr(checkin, 'launch_login_context', AsyncMock(return_value=context))
	monkeypatch.setattr(checkin, 'prepare_browser_page', AsyncMock())
	monkeypatch.setattr(checkin, 'wait_for_waf_ready', AsyncMock())
	solve = AsyncMock(return_value=('test-challenge', None))
	monkeypatch.setattr(checkin, 'solve_turnstile_in_page', solve)
	monkeypatch.setattr(checkin, 'reset_turnstile_in_page', AsyncMock())
	profile = (200, json.dumps({'success': True, 'data': {'id': 554, 'quota': 2500000}}))
	status = lambda value: (200, json.dumps({'success': True, 'data': {'stats': {'checked_in_today': value}}}))
	responses = [profile, status(checked)]
	if not checked:
		responses.extend([(200, '{"success":true}'), status(True)])
	request = AsyncMock(side_effect=[*responses, profile])
	monkeypatch.setattr(checkin, 'request_in_page', request)
	success, before, after = await checkin._run_account_checkin(account, 'SuperQiyu-554', provider)
	assert success and before['success'] and after['checked_in_today']
	posts = [call for call in request.await_args_list if call.kwargs.get('method') == 'POST']
	assert len(posts) == int(not checked)
	assert solve.await_count == int(not checked)
	for call in request.await_args_list:
		assert call.kwargs['headers']['Authorization'] == 'Bearer test-pat'
		assert call.kwargs['headers']['New-Api-User'] == '554'
	context.close.assert_awaited_once()
