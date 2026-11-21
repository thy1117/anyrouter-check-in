import json
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import checkin
from utils.config import AccountConfig, AppConfig, load_accounts_config


def test_91token_accounts_keep_their_ids_and_are_wired_into_workflow(monkeypatch):
	for name in list(checkin.os.environ):
		if name in ('ANYROUTER_ACCOUNTS', 'EXTRA_ACCOUNTS', 'CHECKIN_DISABLED_PROVIDERS') or name.startswith(
			'EXTRA_ACCOUNTS_'
		):
			monkeypatch.delenv(name)
	for slot, name, uid in (
		(139, '1117', '1791'),
		(140, '1118', '2098'),
		(141, '1119', '2108'),
		(142, '1120', '2110'),
		(143, '1121', '2113'),
		(144, '1122', '2115'),
		(145, '1123', '2117'),
		(146, '1125', '2120'),
	):
		monkeypatch.setenv(
			f'EXTRA_ACCOUNTS_{slot}',
			json.dumps(
				[{'name': f'91Token-{name}', 'provider': '91token', 'api_user': uid, 'access_token': 'test-pat'}]
			),
		)
		workflow = Path('.github/workflows/checkin.yml').read_text(encoding='utf-8')
		assert f'EXTRA_ACCOUNTS_{slot}: ${{{{ secrets.EXTRA_ACCOUNTS_{slot} }}}}' in workflow
	accounts = load_accounts_config()
	assert [(account.name, account.api_user) for account in accounts] == [
		('91Token-1117', '1791'),
		('91Token-1118', '2098'),
		('91Token-1119', '2108'),
		('91Token-1120', '2110'),
		('91Token-1121', '2113'),
		('91Token-1122', '2115'),
		('91Token-1123', '2117'),
		('91Token-1125', '2120'),
	]
	assert checkin.filter_accounts_by_target(accounts, '91token') == accounts


HTTP_ONLY_ACCOUNT = {
	'name': '91Token-1126',
	'provider': '91token',
	'api_user': '90001',
	'access_token': 'test-http-only-pat',
	'http_only': True,
}
PROFILE_PATH = '/api/user/self'
CHECKIN_PATH = '/api/user/checkin'
CHECKIN_STATUS_PATH = f'{CHECKIN_PATH}?month=2026-11'


@pytest.mark.parametrize(('slot', 'suffix'), [(147, '1126'), (148, '1127')])
def test_91token_http_only_account_is_wired_into_workflow(monkeypatch, slot, suffix):
	for name in tuple(checkin.os.environ):
		if name in ('ANYROUTER_ACCOUNTS', 'EXTRA_ACCOUNTS', 'CHECKIN_DISABLED_PROVIDERS') or name.startswith(
			'EXTRA_ACCOUNTS_'
		):
			monkeypatch.delenv(name)
	account = {**HTTP_ONLY_ACCOUNT, 'name': f'91Token-{suffix}'}
	monkeypatch.setenv(f'EXTRA_ACCOUNTS_{slot}', json.dumps([account]))

	workflow = Path('.github/workflows/checkin.yml').read_text(encoding='utf-8')
	assert f'EXTRA_ACCOUNTS_{slot}: ${{{{ secrets.CHECKIN_ACCOUNT_{suffix} }}}}' in workflow
	accounts = load_accounts_config()
	assert accounts is not None
	assert len(accounts) == 1
	assert accounts[0].name == account['name']
	assert accounts[0].provider == '91token'
	assert accounts[0].api_user == HTTP_ONLY_ACCOUNT['api_user']
	assert accounts[0].http_only is True


@pytest.mark.parametrize('value', ['true', 'false', 1])
def test_91token_http_only_requires_a_boolean(value):
	with pytest.raises(ValueError, match='http_only'):
		AccountConfig.from_dict({**HTTP_ONLY_ACCOUNT, 'http_only': value}, 0)


@pytest.fixture
def run_91token_http_only(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	app_config = AppConfig.load_from_env()
	account = AccountConfig.from_dict(HTTP_ONLY_ACCOUNT, 0)
	assert account.http_only is True

	class FixedDatetime(datetime):
		@classmethod
		def now(cls, tz=None):
			return cls(2026, 10, 31, 16, 15, tzinfo=UTC).astimezone(tz)

	monkeypatch.setattr(checkin, 'datetime', FixedDatetime)

	def forbid_browser_or_proxy(*args, **kwargs):
		pytest.fail('http_only unexpectedly initiated browser verification or proxy switching')

	for name in (
		'get_waf_cookies_with_browser',
		'launch_login_context',
		'resolve_account_proxy_node',
		'run_gorouter_check_in_in_page',
		'solve_turnstile_in_page',
	):
		monkeypatch.setattr(checkin, name, forbid_browser_or_proxy)

	client = httpx.AsyncClient

	async def run(*expected_requests, use_account_entrypoint=False):
		pending = deque(expected_requests)
		requests = []

		def handle(request):
			if not pending:
				pytest.fail(f'Unexpected request: {request.method} {request.url}')
			requests.append(request)
			return pending.popleft()[2]

		monkeypatch.setattr(
			httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs)
		)
		if use_account_entrypoint:
			result = await checkin.check_in_account(account, 0, app_config)
		else:
			provider = app_config.providers['91token']
			result = await checkin._run_account_checkin(account, account.get_display_name(0), provider)
		assert not pending
		for request, (method, path, _) in zip(requests, expected_requests, strict=True):
			assert request.method == method
			assert str(request.url) == f'https://91token.com{path}'
			assert request.headers['Authorization'] == 'Bearer test-http-only-pat'
			assert request.headers['New-Api-User'] == HTTP_ONLY_ACCOUNT['api_user']
			assert 'X-Turnstile-Token' not in request.headers
		return result

	return run


def _profile_response(user_id=90001):
	return httpx.Response(200, json={'success': True, 'data': {'id': user_id, 'quota': 500000}})


def _status_response(checked):
	return httpx.Response(200, json={'success': True, 'data': {'stats': {'checked_in_today': checked}}})


@pytest.mark.parametrize('user_id', [None, 90002], ids=['missing', 'different'])
async def test_91token_http_only_requires_matching_profile_id(run_91token_http_only, user_id):
	success, _before, after = await run_91token_http_only(('GET', PROFILE_PATH, _profile_response(user_id)))

	assert success is False
	assert after['check_in_error']
	assert after['check_in_submitted'] is False


async def test_91token_http_only_does_not_submit_when_status_is_forbidden(run_91token_http_only):
	success, before, after = await run_91token_http_only(
		('GET', PROFILE_PATH, _profile_response()),
		('GET', CHECKIN_STATUS_PATH, httpx.Response(403, text='<html>Forbidden</html>')),
		use_account_entrypoint=True,
	)

	assert success is False
	assert before['success'] is True
	assert after['check_in_error']
	assert after['check_in_submitted'] is False


async def test_91token_http_only_skips_an_already_checked_in_account(run_91token_http_only):
	success, before, after = await run_91token_http_only(
		('GET', PROFILE_PATH, _profile_response()),
		('GET', CHECKIN_STATUS_PATH, _status_response(True)),
	)

	assert success is True
	assert before['success'] is True
	assert after['checked_in_today'] is True


async def test_91token_http_only_submits_once(run_91token_http_only):
	success, before, after = await run_91token_http_only(
		('GET', PROFILE_PATH, _profile_response()),
		('GET', CHECKIN_STATUS_PATH, _status_response(False)),
		('POST', CHECKIN_PATH, httpx.Response(200, json={'success': True})),
	)

	assert success is True
	assert before['success'] is True
	assert after['checked_in_today'] is True


@pytest.mark.parametrize('failure', ['turnstile', 'verification_page', 'redirect'])
async def test_91token_http_only_stops_after_a_rejected_submission(run_91token_http_only, failure):
	if failure == 'turnstile':
		response = httpx.Response(200, json={'success': False, 'message': 'Turnstile token is empty'})
	elif failure == 'verification_page':
		response = httpx.Response(403, text='<html>Website verification required</html>')
	else:
		response = httpx.Response(307, headers={'Location': '/profile'})

	success, before, after = await run_91token_http_only(
		('GET', PROFILE_PATH, _profile_response()),
		('GET', CHECKIN_STATUS_PATH, _status_response(False)),
		('POST', CHECKIN_PATH, response),
	)

	assert success is False
	assert before['success'] is True
	assert after['check_in_error']
	assert after['check_in_submitted'] is True


@pytest.mark.parametrize('checked', [False, True])
async def test_91token_uses_turnstile_only_when_needed_and_confirms_status(monkeypatch, checked):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	provider = AppConfig.load_from_env().providers['91token']
	assert provider.domain == 'https://91token.com'
	assert provider.login_path == '/profile'
	assert provider.sign_in_path == provider.check_in_status_path == '/api/user/checkin'
	assert provider.checkin_turnstile and provider.persist_profile and provider.turnstile_site_key
	assert provider.turnstile_header == 'X-Turnstile-Token'
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
	if posts:
		post = posts[0]
		assert post.args[1] == '/api/user/checkin'
		assert 'test-challenge' not in post.args[1]
		assert 'turnstile=' not in post.args[1]
		assert post.kwargs['headers']['X-Turnstile-Token'] == 'test-challenge'
	for call in request.await_args_list:
		assert call.kwargs['headers']['Authorization'] == 'Bearer test-pat'
		assert call.kwargs['headers']['New-Api-User'] == '1791'
		if call.kwargs.get('method') != 'POST':
			assert 'X-Turnstile-Token' not in call.kwargs['headers']
	context.close.assert_awaited_once()


async def test_91token_empty_turnstile_never_submits(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	provider = AppConfig.load_from_env().providers['91token']
	account = AccountConfig(cookies=None, provider='91token', access_token='test-pat', api_user='1791')
	page = SimpleNamespace(goto=AsyncMock())
	context = SimpleNamespace(
		new_page=AsyncMock(return_value=page), cookies=AsyncMock(return_value=[]), close=AsyncMock()
	)
	monkeypatch.setattr(checkin, 'load_browser_login_settings', lambda *a, **kw: SimpleNamespace(wait_timeout_ms=1000))
	monkeypatch.setattr(checkin, 'launch_login_context', AsyncMock(return_value=context))
	monkeypatch.setattr(checkin, 'prepare_browser_page', AsyncMock())
	monkeypatch.setattr(checkin, 'wait_for_waf_ready', AsyncMock())
	monkeypatch.setattr(
		checkin, 'solve_turnstile_in_page', AsyncMock(return_value=(None, 'Turnstile token was not obtained'))
	)
	monkeypatch.setattr(checkin, 'reset_turnstile_in_page', AsyncMock())
	monkeypatch.setattr(checkin, 'GOROUTER_TURNSTILE_ATTEMPTS', 1)
	profile = (200, json.dumps({'success': True, 'data': {'id': 1791, 'quota': 500000}}))
	status = (200, json.dumps({'success': True, 'data': {'stats': {'checked_in_today': False}}}))
	request = AsyncMock(side_effect=[profile, status])
	monkeypatch.setattr(checkin, 'request_in_page', request)

	success, _before, after = await checkin._run_account_checkin(account, '91Token-1117', provider)

	assert success is False
	assert after['check_in_phase'] == 'not_submitted'
	assert after['check_in_submitted'] is False
	assert not [call for call in request.await_args_list if call.kwargs.get('method') == 'POST']
	context.close.assert_awaited_once()


@pytest.mark.parametrize('submission', ['gateway_error', 'transport_error', 'rejected'])
async def test_91token_does_not_replay_a_submitted_challenge(monkeypatch, submission, capsys):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	provider = AppConfig.load_from_env().providers['91token']
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
	if submission == 'gateway_error':
		responses = [(502, 'Bad Gateway'), status(True), profile]
	elif submission == 'transport_error':
		responses = [RuntimeError('submission URL contained test-challenge'), status(True), profile]
	else:
		responses = [(200, '{"success":false,"message":"Turnstile token is empty"}')]
	request = AsyncMock(side_effect=[profile, status(False), *responses])
	monkeypatch.setattr(checkin, 'request_in_page', request)

	success, _before, after = await checkin._run_account_checkin(account, '91Token-1117', provider)

	assert success is (submission != 'rejected')
	assert after['check_in_submitted'] is True
	assert after['check_in_phase'] == ('submission_rejected' if submission == 'rejected' else 'confirmed')
	posts = [call for call in request.await_args_list if call.kwargs.get('method') == 'POST']
	assert len(posts) == solve.await_count == 1
	assert posts[0].args[1] == '/api/user/checkin'
	assert posts[0].kwargs['headers']['X-Turnstile-Token'] == 'test-challenge'
	for call in request.await_args_list:
		if call.kwargs.get('method') != 'POST':
			assert 'X-Turnstile-Token' not in call.kwargs['headers']
	assert 'test-challenge' not in capsys.readouterr().out
	context.close.assert_awaited_once()
