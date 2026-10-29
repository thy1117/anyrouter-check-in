import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import yaml
from playwright.async_api import Error as PlaywrightError

import checkin
from utils.config import AccountConfig, AppConfig, load_accounts_config
from utils.proxy import ProxyNodeSwitchError

HEADERS = {'Authorization': 'Bearer token', 'New-Api-User': '42'}
CHECKIN_PATH = '/api/user/checkin'
STATUS_PATH = '/api/gwent/status'
DRAW_PATH = '/api/gwent/draw'
CHECKIN_MONTH = re.compile(r'^/api/user/checkin\?month=\d{4}-\d{2}$')
PROVIDER = SimpleNamespace(
	name='vsllm',
	domain='https://vsllm.cc',
	sign_in_path=CHECKIN_PATH,
	check_in_status_path=CHECKIN_PATH,
)


def _body(data):
	return 200, json.dumps({'success': True, 'data': data})


def _checkin(checked):
	return _body({'stats': {'checked_in_today': checked}})


def _gwent(enabled=True, charges=3, extra=0):
	return _body({'enabled': enabled, 'charges_current': charges, 'extra_draws_left': extra, 'charges_max': 3})


def _prize(quota):
	return _body({'prize': {'name': 'gift', 'quota': quota}})


def _install(monkeypatch, handler):
	mock = AsyncMock(side_effect=handler)
	monkeypatch.setattr(checkin, 'request_in_page', mock)
	return mock


def test_vsllm_provider_and_workflow_slot(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	provider = AppConfig.load_from_env().providers['vsllm']
	assert provider.domain == 'https://vsllm.cc'
	assert provider.request_in_page is True
	assert provider.use_proxy is True
	assert provider.persist_profile is True
	assert provider.login_api_path is None
	assert provider.api_user_key == 'New-Api-User'

	workflow = yaml.safe_load((Path(__file__).parents[1] / '.github/workflows/checkin.yml').read_text())
	jobs = workflow['jobs']
	assert 'EXTRA_ACCOUNTS_115' in yaml.safe_dump(jobs['checkin'])
	assert 'EXTRA_ACCOUNTS_116' in yaml.safe_dump(jobs['checkin'])
	assert 'EXTRA_ACCOUNTS_117' in yaml.safe_dump(jobs['checkin'])
	assert all(
		all(
			slot not in yaml.safe_dump(job)
			for slot in ('EXTRA_ACCOUNTS_115', 'EXTRA_ACCOUNTS_116', 'EXTRA_ACCOUNTS_117')
		)
		for name, job in jobs.items()
		if name != 'checkin'
	)

	monkeypatch.delenv('ANYROUTER_ACCOUNTS', raising=False)
	monkeypatch.delenv('EXTRA_ACCOUNTS', raising=False)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_117',
		json.dumps([{'name': 'Vsllm', 'provider': 'vsllm', 'cookies': {'session': 'test'}, 'api_user': '42'}]),
	)
	accounts = load_accounts_config()
	assert accounts is not None
	assert [(account.name, account.provider) for account in accounts] == [('Vsllm', 'vsllm')]


def test_vsllm_accounts_require_distinct_proxy_ips(monkeypatch, tmp_path):
	monkeypatch.setenv('PROXY_ASSIGNMENTS_FILE', str(tmp_path / 'assignments.json'))
	monkeypatch.setattr(checkin, '_PROXY_ALLOCATOR', None)
	monkeypatch.setattr(checkin, 'get_proxy_server', lambda **_: 'http://127.0.0.1:7890')
	monkeypatch.setattr(checkin, 'get_mihomo_nodes', lambda: ['node-a', 'node-b', 'node-c'])
	monkeypatch.setattr(
		checkin,
		'probe_proxy_ip',
		lambda node, _domain: {'node-a': '203.0.113.1', 'node-b': '203.0.113.2', 'node-c': '203.0.113.3'}[node],
	)
	provider = AppConfig.load_from_env().providers['vsllm']
	first = AccountConfig(cookies=None, name='vsllm-github', provider='vsllm', access_token='a', api_user='3380')
	second = AccountConfig(cookies=None, name='vsllm-tthx', provider='vsllm', access_token='b', api_user='23647')
	third = AccountConfig(cookies=None, name='vsllm-tthx1', provider='vsllm', access_token='c', api_user='23653')

	assert checkin.resolve_account_proxy_node(first, provider, 0) == 'node-a'
	assert checkin.resolve_account_proxy_node(second, provider, 1) == 'node-b'
	assert checkin.resolve_account_proxy_node(third, provider, 2) == 'node-c'


def test_vsllm_fails_closed_without_proxy(monkeypatch):
	monkeypatch.setattr(checkin, 'get_proxy_server', lambda **_: None)
	provider = AppConfig.load_from_env().providers['vsllm']
	account = AccountConfig(cookies=None, name='vsllm-github', provider='vsllm', access_token='a', api_user='3380')

	with pytest.raises(ProxyNodeSwitchError, match='requires a proxy'):
		checkin.resolve_account_proxy_node(account, provider, 0)


async def test_vsllm_waits_for_cloudflare_clearance(monkeypatch):
	responses = iter(
		[
			['Just a moment...', 'Checking your browser'],
			['VSLLM', '控制台'],
		]
	)
	page = SimpleNamespace(evaluate=AsyncMock(side_effect=lambda _: next(responses)), frames=[])
	sleep = AsyncMock()
	monkeypatch.setattr(checkin.asyncio, 'sleep', sleep)
	monkeypatch.setattr(checkin, 'turnstile_widget_box', AsyncMock(return_value=None))
	click = AsyncMock()
	monkeypatch.setattr(checkin, 'click_turnstile_checkbox', click)

	await checkin.wait_for_vsllm_clearance(page, 60_000)

	assert page.evaluate.await_count == 2
	sleep.assert_awaited_once_with(2)
	click.assert_not_awaited()


async def test_vsllm_clearance_clicks_visible_challenge_once(monkeypatch):
	frame = SimpleNamespace(url='https://challenges.cloudflare.com/cdn-cgi/challenge-platform/flow')
	responses = iter(
		[
			['Just a moment...', 'Checking your browser'],
			['Just a moment...', 'Checking your browser'],
			['VSLLM', '控制台'],
		]
	)
	page = SimpleNamespace(evaluate=AsyncMock(side_effect=lambda _: next(responses)), frames=[frame])
	box = {'x': 20, 'y': 30, 'width': 300, 'height': 70}

	async def visible_unless_clicked(target, clicked_urls):
		assert target is page
		return None if frame.url in clicked_urls else box

	widget = AsyncMock(side_effect=visible_unless_clicked)
	click = AsyncMock()
	monkeypatch.setattr(checkin, 'turnstile_widget_box', widget)
	monkeypatch.setattr(checkin, 'click_turnstile_checkbox', click)
	monkeypatch.setattr(checkin.asyncio, 'sleep', AsyncMock())

	await checkin.wait_for_vsllm_clearance(page, 60_000)

	click.assert_awaited_once_with(page, box)
	assert widget.await_count >= 1


async def test_vsllm_clearance_retries_navigation_destroyed_error(monkeypatch):
	page = SimpleNamespace(
		evaluate=AsyncMock(
			side_effect=[
				PlaywrightError('Execution context was destroyed, most likely because of a navigation'),
				['VSLLM', '控制台'],
			]
		),
		frames=[],
	)
	sleep = AsyncMock()
	monkeypatch.setattr(checkin.asyncio, 'sleep', sleep)
	monkeypatch.setattr(checkin, 'turnstile_widget_box', AsyncMock(return_value=None))

	await checkin.wait_for_vsllm_clearance(page, 60_000)

	assert page.evaluate.await_count == 2
	sleep.assert_awaited_once()


async def test_vsllm_clearance_does_not_swallow_unexpected_errors(monkeypatch):
	page = SimpleNamespace(evaluate=AsyncMock(side_effect=ValueError('unexpected')), frames=[])
	monkeypatch.setattr(checkin, 'turnstile_widget_box', AsyncMock(return_value=None))

	with pytest.raises(ValueError, match='unexpected'):
		await checkin.wait_for_vsllm_clearance(page, 60_000)


async def test_vsllm_persistent_context_warmup_precedes_identity(monkeypatch, tmp_path):
	provider = AppConfig.load_from_env().providers['vsllm']
	account = AccountConfig(
		cookies=None,
		name='Vsllm-test',
		provider='vsllm',
		access_token='test-token',
		api_user='42',
	)
	settings = SimpleNamespace(
		headless=False,
		humanize=True,
		wait_timeout_ms=10_000,
		profile_dir=tmp_path / 'profile',
		cloakbrowser_binary_path=None,
		persist_profile=True,
	)
	events = []
	page = SimpleNamespace(
		url='about:blank',
		context=SimpleNamespace(add_cookies=AsyncMock()),
	)

	async def goto(url, **_kwargs):
		page.url = url
		events.append(('goto', url))

	page.goto = goto
	context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
	load_settings = Mock(return_value=settings)
	launch_context = AsyncMock(return_value=context)

	async def clearance(target, _timeout_ms):
		assert target is page
		events.append(('clearance', target.url))

	async def request(_target, path, **_kwargs):
		events.append(('request', path))
		return 200, json.dumps({'success': True, 'data': {'id': 42, 'quota': 1000, 'used_quota': 0}})

	async def rewards(*_args, **_kwargs):
		events.append(('rewards',))
		return True, None

	async def forbidden_launch(**_kwargs):
		raise AssertionError('VSLLM must use launch_login_context')

	monkeypatch.setattr(checkin, 'load_browser_login_settings', load_settings)
	monkeypatch.setattr(checkin, 'launch_login_context', launch_context)
	monkeypatch.setattr(checkin, 'launch_async', forbidden_launch)
	monkeypatch.setattr(checkin, 'prepare_browser_page', AsyncMock())
	monkeypatch.setattr(checkin, 'wait_for_waf_ready', AsyncMock())
	monkeypatch.setattr(checkin, 'wait_for_vsllm_clearance', clearance)
	monkeypatch.setattr(checkin, 'wait_for_cookies', AsyncMock())
	monkeypatch.setattr(checkin, 'request_in_page', request)
	monkeypatch.setattr(checkin, 'run_vsllm_rewards_in_page', rewards)

	success, _, _ = await checkin.run_check_in_in_page(account, account.name, provider)

	assert success is True
	assert events[:4] == [
		('goto', 'https://vsllm.cc/'),
		('clearance', 'https://vsllm.cc/'),
		('goto', 'https://vsllm.cc/console/personal'),
		('clearance', 'https://vsllm.cc/console/personal'),
	]
	assert events[4:] == [('request', '/api/user/self'), ('rewards',), ('request', '/api/user/self')]
	load_settings.assert_called_once_with(account.name, 'vsllm', persist_profile=True)
	launch_context.assert_awaited_once_with(settings, use_proxy=True)
	context.close.assert_awaited_once()


async def test_vsllm_clearance_timeout_submits_no_rewards(monkeypatch, tmp_path):
	provider = AppConfig.load_from_env().providers['vsllm']
	account = AccountConfig(
		cookies=None,
		name='Vsllm-timeout',
		provider='vsllm',
		access_token='test-token',
		api_user='42',
	)
	settings = SimpleNamespace(
		headless=False,
		humanize=True,
		wait_timeout_ms=20,
		profile_dir=tmp_path / 'profile',
		cloakbrowser_binary_path=None,
		persist_profile=True,
	)
	page = SimpleNamespace(
		goto=AsyncMock(),
		evaluate=AsyncMock(return_value=['Just a moment...', 'Checking your browser']),
		frames=[],
		context=SimpleNamespace(add_cookies=AsyncMock()),
	)
	context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
	real_sleep = checkin.asyncio.sleep

	async def forbidden_launch(**_kwargs):
		raise AssertionError('VSLLM must use launch_login_context')

	async def brief_sleep(_seconds):
		await real_sleep(0.005)

	request = AsyncMock()
	rewards = AsyncMock()
	monkeypatch.setattr(checkin.asyncio, 'sleep', brief_sleep)
	monkeypatch.setattr(checkin, 'load_browser_login_settings', lambda *_args, **_kwargs: settings)
	monkeypatch.setattr(checkin, 'launch_login_context', AsyncMock(return_value=context))
	monkeypatch.setattr(checkin, 'launch_async', forbidden_launch)
	monkeypatch.setattr(checkin, 'prepare_browser_page', AsyncMock())
	monkeypatch.setattr(checkin, 'wait_for_waf_ready', AsyncMock())
	monkeypatch.setattr(checkin, 'turnstile_widget_box', AsyncMock(return_value=None))
	monkeypatch.setattr(checkin, 'click_turnstile_checkbox', AsyncMock())
	monkeypatch.setattr(checkin, 'request_in_page', request)
	monkeypatch.setattr(checkin, 'run_vsllm_rewards_in_page', rewards)

	success, before, after = await checkin.run_check_in_in_page(account, account.name, provider)

	assert success is False
	assert before is None
	assert 'Cloudflare challenge did not clear' in after['check_in_error']
	assert page.evaluate.await_count > 0
	request.assert_not_awaited()
	rewards.assert_not_awaited()
	context.close.assert_awaited_once()


@pytest.mark.parametrize('status', [_gwent(charges=0), _gwent(enabled=False), _gwent(charges=0, extra=0)])
async def test_no_available_draws_is_success(monkeypatch, status):
	async def handler(_page, path, *, method='GET', headers=None):
		return _checkin(True) if path.startswith(CHECKIN_PATH) else status

	request = _install(monkeypatch, handler)
	assert await checkin.run_vsllm_rewards_in_page(SimpleNamespace(), 'Vsllm', PROVIDER, HEADERS) == (True, None)
	assert not any(call.kwargs.get('method') == 'POST' for call in request.await_args_list)


@pytest.mark.parametrize(('already_checked', 'refills'), [(False, False), (False, True), (True, False)])
async def test_checkin_then_three_draws_include_zero_and_respect_hard_cap(monkeypatch, already_checked, refills):
	checkin_gets = 0
	draws = 0
	status_reads = 0

	async def handler(_page, path, *, method='GET', headers=None):
		nonlocal checkin_gets, draws, status_reads
		assert headers == HEADERS
		if path.startswith(CHECKIN_PATH):
			if method == 'POST':
				assert path == CHECKIN_PATH
				return 200, json.dumps({'success': True})
			assert CHECKIN_MONTH.fullmatch(path)
			checkin_gets += 1
			return _checkin(already_checked or checkin_gets > 1)
		if path == STATUS_PATH:
			status_reads += 1
			if refills:
				return _gwent(charges=3)
			return _gwent(charges=2, extra=1) if draws == 0 else _gwent(charges=max(3 - draws, 0))
		assert path == DRAW_PATH and method == 'POST'
		draws += 1
		return _prize(0 if draws == 2 else 0.5)

	request = _install(monkeypatch, handler)
	assert await checkin.run_vsllm_rewards_in_page(SimpleNamespace(), 'Vsllm', PROVIDER, HEADERS) == (True, None)
	assert checkin_gets == (1 if already_checked else 2)
	assert draws == 3
	assert status_reads == 4
	checkin_posts = sum(
		call.args[1] == CHECKIN_PATH and call.kwargs['method'] == 'POST' for call in request.await_args_list
	)
	assert checkin_posts == (0 if already_checked else 1)


@pytest.mark.parametrize(('initial', 'checkin_posts'), [('invalid', 0), (False, 1)])
async def test_unwritable_or_unconfirmed_checkin_does_not_draw(monkeypatch, initial, checkin_posts):
	checkin_gets = 0

	async def handler(_page, path, *, method='GET', headers=None):
		nonlocal checkin_gets
		if path.startswith(CHECKIN_PATH):
			if method == 'POST':
				assert initial is False
				return 200, json.dumps({'success': True})
			checkin_gets += 1
			return _body({'stats': {'checked_in_today': 'yes'}}) if initial == 'invalid' else _checkin(False)
		return _gwent(charges=3) if path == STATUS_PATH else _prize(1)

	request = _install(monkeypatch, handler)
	success, error = await checkin.run_vsllm_rewards_in_page(SimpleNamespace(), 'Vsllm', PROVIDER, HEADERS)
	assert success is False and error
	assert sum(call.kwargs.get('method') == 'POST' for call in request.await_args_list) == checkin_posts
	assert not any(call.args[1] == DRAW_PATH for call in request.await_args_list)


@pytest.mark.parametrize(
	'failure',
	[
		(500, 'boom'),
		(200, 'not-json'),
		(200, json.dumps({'success': False, 'message': 'rejected'})),
		_prize(True),
		_prize(-1),
		_prize(None),
		TimeoutError('draw timed out'),
	],
)
async def test_draw_failure_or_invalid_prize_stops_after_one_post(monkeypatch, failure):
	draws = 0

	async def handler(_page, path, *, method='GET', headers=None):
		nonlocal draws
		if path.startswith(CHECKIN_PATH):
			return _checkin(True)
		if path == STATUS_PATH:
			return _gwent(charges=3)
		draws += 1
		if isinstance(failure, Exception):
			raise failure
		return failure

	_install(monkeypatch, handler)
	success, error = await checkin.run_vsllm_rewards_in_page(SimpleNamespace(), 'Vsllm', PROVIDER, HEADERS)
	assert success is False and error
	assert draws == 1


@pytest.mark.parametrize(
	('reward_result', 'expected_success', 'expected_error'),
	[((False, 'draw failed'), False, 'draw failed'), ((True, None), True, None)],
)
async def test_final_self_failure_preserves_operation_result(
	monkeypatch, reward_result, expected_success, expected_error
):
	self_reads = 0

	async def handler(_page, path, *, method='GET', headers=None):
		nonlocal self_reads
		assert path == '/api/user/self'
		self_reads += 1
		if self_reads == 1:
			return 200, json.dumps({'success': True, 'data': {'id': 42, 'quota': 100, 'used_quota': 0}})
		return 500, 'unavailable'

	_install(monkeypatch, handler)
	rewards = AsyncMock(return_value=reward_result)
	monkeypatch.setattr(checkin, 'run_vsllm_rewards_in_page', rewards)
	provider = SimpleNamespace(
		name='vsllm',
		user_info_path='/api/user/self',
		sign_in_path=CHECKIN_PATH,
		api_user_key='New-Api-User',
		needs_manual_check_in=lambda: True,
		use_proxy=False,
	)
	account = AccountConfig(name='Vsllm', cookies={'session': 'test'}, provider='vsllm', api_user='42')
	success, _, after = await checkin.run_check_in_in_page(account, 'Vsllm', provider, page=SimpleNamespace())
	assert success is expected_success
	assert after.get('check_in_error') == expected_error
	assert rewards.await_count == 1
	assert self_reads == 2
