import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import checkin
from utils.config import AccountConfig, AppConfig, load_accounts_config


def test_91token_accounts_keep_their_ids_and_are_wired_into_workflow(monkeypatch):
	for name in list(checkin.os.environ):
		if name in ('ANYROUTER_ACCOUNTS', 'EXTRA_ACCOUNTS', 'CHECKIN_DISABLED_PROVIDERS') or name.startswith(
			'EXTRA_ACCOUNTS_'
		):
			monkeypatch.delenv(name)
	for slot, name, uid in ((139, '1117', '1791'), (140, '1118', '2098'), (141, '1119', '2108')):
		monkeypatch.setenv(
			f'EXTRA_ACCOUNTS_{slot}',
			json.dumps(
				[{'name': f'91Token-{name}', 'provider': '91token', 'api_user': uid, 'access_token': 'test-pat'}]
			),
		)
		workflow = Path('.github/workflows/checkin.yml').read_text()
		assert f'EXTRA_ACCOUNTS_{slot}: ${{{{ secrets.EXTRA_ACCOUNTS_{slot} }}}}' in workflow
	accounts = load_accounts_config()
	assert [(account.name, account.api_user) for account in accounts] == [
		('91Token-1117', '1791'),
		('91Token-1118', '2098'),
		('91Token-1119', '2108'),
	]
	assert checkin.filter_accounts_by_target(accounts, '91token') == accounts


@pytest.mark.parametrize('checked', [False, True])
async def test_91token_uses_turnstile_only_when_needed_and_confirms_status(monkeypatch, checked):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	provider = AppConfig.load_from_env().providers['91token']
	assert provider.domain == 'https://91token.com'
	assert provider.login_path == '/profile'
	assert provider.sign_in_path == provider.check_in_status_path == '/api/user/checkin'
	assert provider.checkin_turnstile and provider.persist_profile and provider.turnstile_site_key
	assert provider.use_proxy is False
	account = AccountConfig(cookies=None, provider='91token', access_token='test-pat', api_user='1791')
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
	profile = (200, json.dumps({'success': True, 'data': {'id': 1791, 'quota': 500000}}))
	status = lambda value: (200, json.dumps({'success': True, 'data': {'stats': {'checked_in_today': value}}}))
	responses = [profile, status(checked)]
	if not checked:
		responses.extend([(200, '{"success":true}'), status(True)])
	request = AsyncMock(side_effect=[*responses, profile])
	monkeypatch.setattr(checkin, 'request_in_page', request)
	success, before, after = await checkin._run_account_checkin(account, '91Token-1117', provider)
	assert success and before['success'] and after['checked_in_today']
	posts = [call for call in request.await_args_list if call.kwargs.get('method') == 'POST']
	assert len(posts) == solve.await_count == int(not checked)
	for call in request.await_args_list:
		assert call.kwargs['headers']['Authorization'] == 'Bearer test-pat'
		assert call.kwargs['headers']['New-Api-User'] == '1791'
	context.close.assert_awaited_once()
