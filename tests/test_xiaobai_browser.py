"""Offline checks for Xiaobai's account-bound Turnstile and one-write contract."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import checkin
from scripts.run_checkin_no_challenges import no_challenges
from utils.config import AccountConfig, AppConfig


def response(data, *, status=200, **fields):
	return status, json.dumps({'ok': True, 'data': data, **fields})


def profile(balance=10, user_id=7):
	return 200, json.dumps({'code': 0, 'data': {'id': user_id, 'balance': balance, 'used_balance': 2}})


def daily(signed=False, **fields):
	return response(
		{
			'signedToday': signed,
			'config': {'enabled': True},
			'verification': {
				'configured': True,
				'siteKey': 'account-sitekey',
				'action': 'checkin',
				'cData': 'fresh-data',
			},
			**fields,
		}
	)


@pytest.fixture
def browser(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	monkeypatch.setenv('GITHUB_ACTIONS', 'false')
	page = SimpleNamespace(goto=AsyncMock(), reload=AsyncMock())
	context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
	settings = SimpleNamespace(wait_timeout_ms=1000)
	load_settings = lambda *args, **kwargs: settings
	monkeypatch.setattr(checkin, 'load_browser_login_settings', load_settings)
	launch = AsyncMock(return_value=context)
	monkeypatch.setattr(checkin, 'launch_login_context', launch)
	monkeypatch.setattr(checkin, 'prepare_browser_page', AsyncMock())
	monkeypatch.setattr(checkin, 'wait_for_waf_ready', AsyncMock())
	monkeypatch.setattr(checkin, 'reset_turnstile_in_page', AsyncMock())
	monkeypatch.setattr(checkin.asyncio, 'sleep', AsyncMock())
	solver = AsyncMock(return_value=('private-challenge', None))
	monkeypatch.setattr(checkin, 'solve_turnstile_in_page', solver)
	request = AsyncMock()
	monkeypatch.setattr(checkin, 'request_in_page', request)
	provider = AppConfig.load_from_env().providers['xiaobai']

	async def run(responses):
		request.side_effect = responses
		return await checkin._run_xiaobai_check_in_in_page(
			'xiaobai-test', provider, 'private-access', expected_user_id='7'
		)

	return SimpleNamespace(
		run=run,
		page=page,
		context=context,
		request=request,
		solver=solver,
		provider=provider,
		launch=launch,
		settings=settings,
	)


def posts(browser):
	return [call for call in browser.request.await_args_list if call.kwargs.get('method') == 'POST']


async def test_uses_dynamic_context_and_camelcase_json_in_one_browser(browser, capsys):
	success, before, after = await browser.run(
		[profile(), daily(), response({'record': {'reward_amount': 0.25}}), daily(True), profile(10.25)]
	)
	assert success and before['quota'] == 10 and after['quota'] == 10.25
	assert after['signedToday'] is True and after['check_in_phase'] == 'confirmed'
	assert after['check_in_submitted'] is True
	browser.solver.assert_awaited_once_with(browser.page, 'account-sitekey', action='checkin', c_data='fresh-data')
	assert len(posts(browser)) == 1
	post = posts(browser)[0]
	assert post.args == (browser.page, '/checkin/api/checkin')
	assert post.kwargs['json_body'] == {'turnstileToken': 'private-challenge'}
	assert all(call.args[0] is browser.page for call in browser.request.await_args_list)
	assert all(
		call.kwargs['headers']['Authorization'] == 'Bearer private-access' for call in browser.request.await_args_list
	)
	assert all('turnstile' not in call.args[1].lower() for call in browser.request.await_args_list)
	browser.launch.assert_awaited_once_with(browser.settings, use_proxy=True)
	browser.context.close.assert_awaited_once()
	output = capsys.readouterr().out
	assert all(secret not in output for secret in ('private-access', 'private-challenge', 'fresh-data'))


async def test_account_checked_elsewhere_skips_challenge_and_submission(browser):
	success, before, after = await browser.run([profile(), daily(True)])
	assert success and before['quota'] == after['quota']
	assert after['check_in_phase'] == 'already_confirmed'
	assert after['check_in_submitted'] is False
	browser.solver.assert_not_awaited()
	assert not posts(browser)


@pytest.mark.parametrize(
	'verification',
	[
		None,
		{},
		{'configured': False, 'siteKey': 'key', 'cData': 'data'},
		{'configured': True, 'siteKey': 'key'},
		{'configured': True, 'siteKey': '', 'cData': 'data'},
		{'configured': True, 'siteKey': 'key', 'cData': ' '},
		{'configured': True, 'siteKey': 'key', 'cData': 123},
		{'configured': True, 'siteKey': 'key', 'cData': 'data', 'action': {}},
	],
)
async def test_missing_or_invalid_verification_never_falls_back_to_empty_post(browser, verification):
	success, _, after = await browser.run([profile(), daily(verification=verification)])
	assert success is False
	assert after['check_in_submitted'] is False and after['check_in_phase'] == 'not_submitted'
	browser.solver.assert_not_awaited()
	assert not posts(browser)
	browser.context.close.assert_awaited_once()


@pytest.mark.parametrize('state', [daily(config={'enabled': False}), daily(signed=None), (503, '{}'), (200, '<html>')])
async def test_disabled_or_unavailable_status_does_not_submit(browser, state):
	success, _, after = await browser.run([profile(), state])
	assert success is False and after['check_in_submitted'] is False
	browser.solver.assert_not_awaited()
	assert not posts(browser)


@pytest.mark.parametrize('bad_profile', [profile(user_id=8), profile(user_id=None), (401, '{}')])
async def test_browser_authentication_must_match_the_persisted_session(browser, bad_profile):
	success, _, after = await browser.run([bad_profile])
	assert success is False and after['check_in_phase'] == 'not_submitted'
	assert browser.request.await_count == 1
	browser.solver.assert_not_awaited()


async def test_stalled_challenge_refreshes_status_and_context_before_retry(browser):
	browser.solver.side_effect = [(None, 'Turnstile challenge stalled after click'), ('private-challenge', None)]
	new_verification = {'configured': True, 'siteKey': 'new-key', 'action': 'new-action', 'cData': 'new-cdata'}
	success, _, _ = await browser.run(
		[profile(), daily(), daily(verification=new_verification), response({}), daily(True), profile(10.25)]
	)
	assert success is True
	assert len(posts(browser)) == 1
	assert browser.solver.await_count == 2
	assert browser.solver.await_args.args == (browser.page, 'new-key')
	assert browser.solver.await_args.kwargs == {'action': 'new-action', 'c_data': 'new-cdata'}
	browser.page.reload.assert_awaited_once()


async def test_repeated_stalls_stop_without_submitting(browser):
	browser.solver.return_value = (None, 'Turnstile challenge stalled after click')
	success, _, after = await browser.run([profile(), daily(), daily(), daily()])
	assert success is False and after['check_in_phase'] == 'not_submitted'
	assert browser.solver.await_count == 3 and browser.page.reload.await_count == 2
	assert not posts(browser)
	assert 'stalled after click' in after['check_in_error']


async def test_non_stall_verification_error_is_not_retried_or_logged_raw(browser, capsys):
	browser.solver.return_value = (None, 'Turnstile render failed - private-access fresh-data')
	success, _, after = await browser.run([profile(), daily()])
	assert success is False and after['check_in_submitted'] is False
	browser.solver.assert_awaited_once()
	assert not posts(browser)
	assert 'private-access' not in capsys.readouterr().out
	assert 'fresh-data' not in after['check_in_error']


@pytest.mark.parametrize('status', [400, 401, 403, 429, 200])
async def test_explicit_rejection_stops_after_one_submission_without_echoing_body(browser, status, capsys):
	success, _, after = await browser.run(
		[
			profile(),
			daily(),
			response({}, status=status, ok=False, message='private-access private-challenge fresh-data'),
		]
	)
	assert success is False and after['check_in_phase'] == 'submission_rejected'
	assert after['check_in_submitted'] is True and len(posts(browser)) == 1
	assert browser.request.await_count == 3
	browser.solver.assert_awaited_once()
	output = capsys.readouterr().out
	assert all(secret not in output for secret in ('private-access', 'private-challenge', 'fresh-data'))


@pytest.mark.parametrize(
	'post_response', [TimeoutError('private-challenge'), (502, '<html>bad gateway</html>'), response({'record': {}})]
)
@pytest.mark.parametrize('confirmed', [False, True])
async def test_ambiguous_submission_or_success_response_requires_status_and_never_replays(
	browser, post_response, confirmed, capsys
):
	confirmations = [daily(False), daily(True), profile(10.25)] if confirmed else [daily(False)] * 3
	success, before, after = await browser.run([profile(), daily(), post_response, *confirmations])
	assert success is confirmed and before['quota'] == 10
	assert len(posts(browser)) == 1 and browser.solver.await_count == 1
	assert after['check_in_phase'] == ('confirmed' if confirmed else 'submitted_unconfirmed')
	assert after['check_in_submitted'] is True
	assert 'private-challenge' not in capsys.readouterr().out


@pytest.mark.parametrize('confirmation', [(401, '{}'), (403, '{}'), (429, '{}')])
async def test_auth_or_rate_failure_during_confirmation_stops_reads(browser, confirmation):
	success, _, after = await browser.run([profile(), daily(), response({}), confirmation])
	assert success is False and after['check_in_phase'] == 'submitted_unconfirmed'
	assert browser.request.await_count == 4 and len(posts(browser)) == 1


@pytest.mark.parametrize(
	'bad_balance', [profile(None), profile('NaN'), profile(user_id=8), (401, '{}'), TimeoutError('private-access')]
)
async def test_balance_failure_preserves_confirmed_success_without_rotation_or_replay(browser, bad_balance, capsys):
	success, before, after = await browser.run([profile(), daily(), response({}), daily(True), bad_balance])
	assert success is True and before['quota'] == 10
	assert after['signedToday'] is True and after['check_in_phase'] == 'confirmed'
	assert 'quota' not in after
	assert len(posts(browser)) == 1
	assert all(call.args[1] != browser.provider.auth_refresh_path for call in browser.request.await_args_list)
	assert 'private-access' not in capsys.readouterr().out


async def test_cleanup_failure_cannot_erase_a_confirmed_submission(browser, monkeypatch):
	browser.context.close.side_effect = RuntimeError('private-access')
	monkeypatch.setattr(checkin, 'reset_turnstile_in_page', AsyncMock(side_effect=RuntimeError('private-challenge')))
	success, _, after = await browser.run([profile(), daily(), response({}), daily(True), profile(10.25)])
	assert success is True and after['check_in_phase'] == 'confirmed'
	assert len(posts(browser)) == 1


async def test_verification_script_that_never_loads_has_a_deadline(browser, monkeypatch):
	async def stalled(*args, **kwargs):
		await asyncio.Event().wait()

	browser.solver.side_effect = stalled
	monkeypatch.setattr(checkin, 'XIAOBAI_VERIFICATION_TIMEOUT_SECONDS', 0.01)
	success, _, after = await browser.run([profile(), daily()])
	assert success is False and after['check_in_phase'] == 'not_submitted'
	assert not posts(browser)
	assert '超时' in after['check_in_error']
	browser.context.close.assert_awaited_once()


@pytest.mark.parametrize('stage', ['profile', 'status', 'post', 'confirmation'])
async def test_hanging_browser_request_has_a_deadline_without_replaying(browser, monkeypatch, stage):
	monkeypatch.setattr(checkin, 'XIAOBAI_BROWSER_REQUEST_TIMEOUT_SECONDS', 0.01)
	status_reads = 0

	async def request(page, path, *, method='GET', **kwargs):
		nonlocal status_reads
		if path == browser.provider.user_info_path:
			if stage == 'profile':
				await asyncio.Event().wait()
			return profile()
		if method == 'POST':
			if stage == 'post':
				await asyncio.Event().wait()
			return response({})
		status_reads += 1
		if stage == 'status' or (stage == 'confirmation' and status_reads > 1):
			await asyncio.Event().wait()
		return daily(status_reads > 1)

	browser.request.side_effect = request
	result = await checkin._run_xiaobai_check_in_in_page(
		'xiaobai-test', browser.provider, 'private-access', expected_user_id='7'
	)
	success, _, after = result
	assert success is (stage == 'post')
	assert len(posts(browser)) == int(stage in ('post', 'confirmation'))
	assert (
		after['check_in_phase']
		== {
			'profile': 'not_submitted',
			'status': 'not_submitted',
			'post': 'confirmed',
			'confirmation': 'submitted_unconfirmed',
		}[stage]
	)
	browser.context.close.assert_awaited_once()


async def test_no_challenges_guard_survives_worker_thread_and_blocks_before_browser(monkeypatch):
	provider = replace(AppConfig.load_from_env().providers['xiaobai'], use_proxy=False)
	account = AccountConfig(cookies=None, provider='xiaobai', name='test', access_token='private-access')
	monkeypatch.setattr(checkin, 'create_xiaobai_token_state', lambda *args: None)
	requests = []

	def handler(request):
		requests.append(request)
		assert request.method == 'GET'
		code, body = profile() if request.url.path == provider.user_info_path else daily()
		return httpx.Response(code, text=body, headers={'content-type': 'application/json'})

	monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', httpx.MockTransport(handler).handle_request)
	with no_challenges(checkin):
		success, _, after = await checkin._run_account_checkin(account, account.name, provider)
	assert success is False
	assert after['challenge_blocked'] is True
	assert after['check_in_phase'] == 'not_submitted' and after['check_in_submitted'] is False
	assert [request.url.path for request in requests] == [provider.user_info_path, provider.check_in_status_path]
