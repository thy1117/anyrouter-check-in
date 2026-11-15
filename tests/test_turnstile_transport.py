import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import checkin
from utils.config import AccountConfig, AppConfig, ProviderConfig


def test_turnstile_header_defaults_to_query_transport():
	assert ProviderConfig(name='example', domain='https://example.test').turnstile_header == ''
	assert ProviderConfig.from_dict('example', {'domain': 'https://example.test'}).turnstile_header == ''


@pytest.mark.parametrize('configured_header', ['X-Turnstile-Token', ''])
def test_turnstile_header_inherits_provider_defaults(configured_header):
	defaults = ProviderConfig(name='example', domain='https://original.test', turnstile_header=configured_header)

	provider = ProviderConfig.from_dict('example', {'domain': 'https://override.test'}, defaults=defaults)

	assert provider.turnstile_header == configured_header


@pytest.mark.parametrize('override', ['X-Another-Turnstile', ''])
def test_turnstile_header_explicit_override_takes_precedence(override):
	defaults = ProviderConfig(name='example', domain='https://original.test', turnstile_header='X-Turnstile-Token')

	provider = ProviderConfig.from_dict(
		'example', {'domain': 'https://override.test', 'turnstile_header': override}, defaults=defaults
	)

	assert provider.turnstile_header == override


def test_91token_partial_provider_override_preserves_header_transport(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.setenv('EXTRA_PROVIDERS', '{"91token":{"domain":"https://91token.com"}}')

	assert AppConfig.load_from_env().providers['91token'].turnstile_header == 'X-Turnstile-Token'


async def test_other_turnstile_providers_keep_query_transport(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	provider = AppConfig.load_from_env().providers['aotera']
	assert provider.turnstile_header == ''
	account = AccountConfig(cookies=None, provider='aotera', access_token='test-pat', api_user='1791')
	page = SimpleNamespace(goto=AsyncMock())
	context = SimpleNamespace(
		new_page=AsyncMock(return_value=page), cookies=AsyncMock(return_value=[]), close=AsyncMock()
	)
	monkeypatch.setattr(checkin, 'load_browser_login_settings', lambda *a, **kw: SimpleNamespace(wait_timeout_ms=1000))
	monkeypatch.setattr(checkin, 'launch_login_context', AsyncMock(return_value=context))
	monkeypatch.setattr(checkin, 'prepare_browser_page', AsyncMock())
	monkeypatch.setattr(checkin, 'wait_for_waf_ready', AsyncMock())
	monkeypatch.setattr(checkin, 'solve_turnstile_in_page', AsyncMock(return_value=('query+token/with=symbols', None)))
	monkeypatch.setattr(checkin, 'reset_turnstile_in_page', AsyncMock())
	profile = (200, json.dumps({'success': True, 'data': {'id': 1791, 'quota': 500000}}))
	status = lambda value: (200, json.dumps({'success': True, 'data': {'stats': {'checked_in_today': value}}}))
	request = AsyncMock(side_effect=[profile, status(False), (200, '{"success":true}'), status(True), profile])
	monkeypatch.setattr(checkin, 'request_in_page', request)

	success, _before, after = await checkin._run_account_checkin(account, 'Aotera', provider)

	assert success and after['checked_in_today']
	posts = [call for call in request.await_args_list if call.kwargs.get('method') == 'POST']
	assert len(posts) == 1
	assert posts[0].args[1] == '/api/user/checkin?turnstile=query%2Btoken%2Fwith%3Dsymbols'
	assert all('X-Turnstile-Token' not in call.kwargs['headers'] for call in request.await_args_list)
	context.close.assert_awaited_once()
