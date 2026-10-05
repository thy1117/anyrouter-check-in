import json
from unittest.mock import AsyncMock

import pytest

import checkin
from utils.config import AccountConfig, AppConfig


@pytest.mark.asyncio
@pytest.mark.parametrize(
	'scenario', ['verified', 'already', 'challenge_failed', 'rejected', 'status_failed', 'retry_success']
)
async def test_twinkle_contract_and_no_replay(monkeypatch, scenario):
	provider = AppConfig.load_from_env().providers['twinkle']
	account = AccountConfig(
		cookies=None, name='Twinkle-test', provider='twinkle', email='test@example.test', password='test'
	)
	calls = []
	status_reads = 0

	async def evaluate(script, args):
		nonlocal status_reads
		calls.append(args.copy())
		path, method = args['path'], args['method']
		status = 200
		if path == provider.login_api_path:
			data = {'access_token': 'access'}
		elif path == provider.user_info_path:
			data = {'balance': 10}
		elif path == '/api/v1/settings/public':
			data = {'captcha_checkin_enabled': True, 'turnstile_enabled': True, 'turnstile_site_key': 'sitekey'}
		elif method == 'GET':
			status_reads += 1
			data = {'checked_today': scenario == 'already' or status_reads > 1, 'eligible': True}
			if scenario == 'status_failed':
				status = 503
		else:
			assert args['body'] == {'turnstile_token': 'challenge'}
			assert args['headers']['Authorization'] == 'Bearer access'
			data = {}
			if scenario == 'rejected':
				return {'status': 400, 'body': '{"reason":"TURNSTILE_VERIFICATION_FAILED"}'}
		return {'status': status, 'body': json.dumps({'code': 0, 'data': data})}

	page = AsyncMock()
	page.evaluate.side_effect = evaluate
	context = AsyncMock()
	context.new_page.return_value = page
	monkeypatch.setattr(checkin, 'launch_login_context', AsyncMock(return_value=context))
	monkeypatch.setattr(checkin, 'prepare_browser_page', AsyncMock())
	monkeypatch.setattr(checkin, 'wait_for_waf_ready', AsyncMock())
	solver = AsyncMock(
		return_value=(None, 'Turnstile challenge stalled after click')
		if scenario == 'challenge_failed'
		else ('challenge', None)
	)
	if scenario == 'retry_success':
		solver.side_effect = [(None, 'Turnstile challenge stalled after click'), ('challenge', None)]
	monkeypatch.setattr(checkin, 'solve_turnstile_in_page', solver)
	monkeypatch.setattr(checkin, 'reset_turnstile_in_page', AsyncMock())
	monkeypatch.setattr(checkin.asyncio, 'sleep', AsyncMock())
	success, _, after = await checkin._run_account_checkin(account, account.name, provider)
	assert success == (scenario in ('verified', 'already', 'retry_success'))
	posts = [c for c in calls if c['path'] == provider.sign_in_path and c['method'] == 'POST']
	assert len(posts) == (1 if scenario in ('verified', 'rejected', 'retry_success') else 0)
	assert all(c['path'] != provider.auth_refresh_path for c in calls)
	expected_attempts = (
		0
		if scenario in ('already', 'status_failed')
		else 3
		if scenario == 'challenge_failed'
		else 2
		if scenario == 'retry_success'
		else 1
	)
	assert solver.await_count == expected_attempts
	assert page.reload.await_count == max(0, expected_attempts - 1)
	context.close.assert_awaited_once()
	if not success:
		assert after['check_in_error']

	if scenario == 'challenge_failed':
		assert 'Turnstile challenge stalled after click' in after['check_in_error']
