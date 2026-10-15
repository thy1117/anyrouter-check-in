import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import checkin
from utils.config import AccountConfig, AppConfig


@pytest.mark.parametrize('checked_today', [False, True])
async def test_gorouter_status_only_reads_identity_and_status_without_challenge_or_write(monkeypatch, checked_today):
	page = SimpleNamespace(goto=AsyncMock())
	context = SimpleNamespace(
		new_page=AsyncMock(return_value=page), cookies=AsyncMock(return_value=[]), close=AsyncMock()
	)
	request = AsyncMock(
		side_effect=[
			(200, json.dumps({'success': True, 'data': {'id': 1136, 'username': 'thy1117', 'quota': 500000}})),
			(200, json.dumps({'success': True, 'data': {'stats': {'checked_in_today': checked_today}}})),
		]
	)
	monkeypatch.setattr(
		checkin, 'load_browser_login_settings', lambda *a, **kw: SimpleNamespace(wait_timeout_ms=120000)
	)
	monkeypatch.setattr(checkin, 'launch_login_context', AsyncMock(return_value=context))
	monkeypatch.setattr(checkin, 'prepare_browser_page', AsyncMock())
	monkeypatch.setattr(checkin, 'wait_for_waf_ready', AsyncMock())
	monkeypatch.setattr(checkin, 'request_in_page', request)

	async def forbidden(*args, **kwargs):
		pytest.fail('Read-only status must not render a challenge, refresh credentials, or submit a check-in')

	monkeypatch.setattr(checkin, 'solve_turnstile_in_page', forbidden)
	account = AccountConfig(
		cookies=None, provider='aotera', name='Aotera-thy1117', access_token='test-private-pat', api_user='1136'
	)
	provider = AppConfig.load_from_env().providers['aotera']
	success, before, after = await checkin.run_gorouter_check_in_in_page(
		account, account.name, provider, status_only=True
	)

	assert success is True
	assert before['success'] is True
	assert after['status_only'] is True
	assert after['checked_in_today'] is checked_today
	assert request.await_count == 2
	assert all(call.kwargs.get('method', 'GET') == 'GET' for call in request.await_args_list)
	assert request.await_args_list[0].args[1] == provider.user_info_path
	assert 'month=' in request.await_args_list[1].args[1]
	context.close.assert_awaited_once()


async def test_gorouter_status_only_without_pat_refuses_before_browser_or_refresh(monkeypatch):
	launch = AsyncMock()
	monkeypatch.setattr(checkin, 'launch_login_context', launch)
	monkeypatch.setattr(
		checkin,
		'load_browser_login_settings',
		lambda *a, **kw: pytest.fail('status-only must check PAT before loading browser settings'),
	)
	account = AccountConfig(
		cookies='new_api_refresh=test-refresh-cookie',
		provider='aotera',
		name='Aotera-thy1117',
		access_token=None,
		api_user='1136',
	)
	provider = AppConfig.load_from_env().providers['aotera']

	success, before, after = await checkin.run_gorouter_check_in_in_page(
		account, 'Aotera-thy1117', provider, status_only=True
	)

	assert success is False
	assert before is None
	assert after is not None
	assert after['success'] is False
	assert 'access_token' in after['check_in_error']
	launch.assert_not_awaited()
