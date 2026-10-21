import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import checkin
from utils.config import AccountConfig, AppConfig


@pytest.fixture
def gorouter(monkeypatch):
	page = SimpleNamespace(goto=AsyncMock(), reload=AsyncMock())
	context = SimpleNamespace(
		new_page=AsyncMock(return_value=page), cookies=AsyncMock(return_value=[]), close=AsyncMock()
	)
	monkeypatch.setattr(
		checkin, 'load_browser_login_settings', lambda *a, **kw: SimpleNamespace(wait_timeout_ms=120000)
	)
	monkeypatch.setattr(checkin, 'launch_login_context', AsyncMock(return_value=context))
	monkeypatch.setattr(checkin, 'prepare_browser_page', AsyncMock())
	monkeypatch.setattr(checkin, 'wait_for_waf_ready', AsyncMock())
	monkeypatch.setattr(checkin, 'reset_turnstile_in_page', AsyncMock())
	monkeypatch.setattr(checkin.asyncio, 'sleep', AsyncMock())
	monkeypatch.setattr(checkin, 'solve_turnstile_in_page', AsyncMock(return_value=('dummy-challenge', None)))
	account = AccountConfig(
		cookies=None, provider='aotera', name='Aotera-thy1117', access_token='test-pat', api_user='1136'
	)
	return account, AppConfig.load_from_env().providers['aotera'], context


def profile():
	return 200, json.dumps({'success': True, 'data': {'id': 1136, 'username': 'thy1117', 'quota': 500000}})


def signed(value):
	return 200, json.dumps({'success': True, 'data': {'stats': {'checked_in_today': value}}})


async def test_successful_post_unconfirmed_status_is_not_replayed(monkeypatch, gorouter):
	account, provider, context = gorouter
	request = AsyncMock(
		side_effect=[profile(), signed(False), (200, '{"success":true}'), signed(False), signed(False), signed(False)]
	)
	monkeypatch.setattr(checkin, 'request_in_page', request)
	success, before, after = await checkin.run_gorouter_check_in_in_page(account, account.name, provider)
	posts = [call for call in request.await_args_list if call.kwargs.get('method') == 'POST']
	assert len(posts) == 1
	assert success is False
	assert before['success'] is True
	assert after['check_in_phase'] == 'submitted_unconfirmed'
	assert after['check_in_submitted'] is True
	context.close.assert_awaited_once()


async def test_already_checked_balance_read_failure_preserves_checkin_success(monkeypatch, gorouter):
	account, provider, _ = gorouter
	request = AsyncMock(side_effect=[profile(), signed(True), TimeoutError('read failed')])
	monkeypatch.setattr(checkin, 'request_in_page', request)

	success, before, after = await checkin.run_gorouter_check_in_in_page(account, account.name, provider)

	assert success is True
	assert before['success'] is True
	assert after is not None
	assert after['success'] is False
	assert after['checked_in_today'] is True
	assert after['balance_read_error'] == 'TimeoutError'
	assert not any(call.kwargs.get('method') == 'POST' for call in request.await_args_list)


async def test_delayed_status_confirms_one_post_without_new_challenge(monkeypatch, gorouter):
	account, provider, _ = gorouter
	request = AsyncMock(
		side_effect=[profile(), signed(False), (200, '{"success":true}'), signed(False), signed(True), profile()]
	)
	monkeypatch.setattr(checkin, 'request_in_page', request)
	success, _, after = await checkin.run_gorouter_check_in_in_page(account, account.name, provider)
	assert success is True
	assert len([call for call in request.await_args_list if call.kwargs.get('method') == 'POST']) == 1
	assert checkin.solve_turnstile_in_page.await_count == 1
	assert after['checked_in_today'] is True


@pytest.mark.parametrize('confirmed', [False, True])
async def test_ambiguous_post_timeout_checks_status_without_replaying_or_logging_token(
	monkeypatch, gorouter, capsys, confirmed
):
	account, provider, _ = gorouter
	request = AsyncMock(
		side_effect=[
			profile(),
			signed(False),
			TimeoutError('URL contains dummy-challenge'),
			signed(confirmed),
			signed(confirmed),
			signed(confirmed),
		]
	)
	monkeypatch.setattr(checkin, 'request_in_page', request)
	success, before, after = await checkin.run_gorouter_check_in_in_page(account, account.name, provider)
	assert success is confirmed
	assert before['success'] is True
	assert len([call for call in request.await_args_list if call.kwargs.get('method') == 'POST']) == 1
	assert request.await_count >= 4
	assert 'dummy-challenge' not in capsys.readouterr().out
	if not confirmed:
		assert after['check_in_phase'] == 'submitted_unconfirmed'
		assert after['check_in_submitted'] is True


async def test_stalled_challenge_is_reported_as_not_submitted(monkeypatch, gorouter):
	account, provider, _ = gorouter
	request = AsyncMock(side_effect=[profile(), signed(False)])
	monkeypatch.setattr(checkin, 'request_in_page', request)
	monkeypatch.setattr(
		checkin, 'solve_turnstile_in_page', AsyncMock(return_value=(None, 'Turnstile challenge stalled after click'))
	)
	success, _, after = await checkin.run_gorouter_check_in_in_page(account, account.name, provider)
	assert success is False
	assert request.await_count == 2
	assert after['check_in_phase'] == 'not_submitted'
	assert after['check_in_submitted'] is False
	assert '未提交' in after['check_in_error']


async def test_confirmed_checkin_is_preserved_if_followup_balance_read_fails(monkeypatch, gorouter):
	account, provider, _ = gorouter
	request = AsyncMock(
		side_effect=[profile(), signed(False), (200, '{"success":true}'), signed(True), TimeoutError('read failed')]
	)
	monkeypatch.setattr(checkin, 'request_in_page', request)
	success, before, after = await checkin.run_gorouter_check_in_in_page(account, account.name, provider)
	assert success is True
	assert before['success'] is True
	assert after['checked_in_today'] is True
	assert after['success'] is False
	assert after['balance_read_error'] == 'TimeoutError'


@pytest.mark.parametrize('status', [401, 403, 429])
async def test_auth_or_rate_rejection_is_never_retried(monkeypatch, gorouter, status):
	account, provider, _ = gorouter
	request = AsyncMock(side_effect=[profile(), signed(False), (status, '{"success":false,"message":"rejected"}')])
	monkeypatch.setattr(checkin, 'request_in_page', request)
	success, _, after = await checkin.run_gorouter_check_in_in_page(account, account.name, provider)
	assert success is False
	assert request.await_count == 3
	assert after['check_in_phase'] == 'submission_rejected'
	assert checkin.solve_turnstile_in_page.await_count == 1


async def test_server_turnstile_rejection_is_not_used_to_mint_more_tokens(monkeypatch, gorouter):
	account, provider, _ = gorouter
	request = AsyncMock(
		side_effect=[profile(), signed(False), (200, '{"success":false,"message":"Turnstile 校验失败，请刷新重试！"}')]
	)
	monkeypatch.setattr(checkin, 'request_in_page', request)
	success, _, after = await checkin.run_gorouter_check_in_in_page(account, account.name, provider)
	assert success is False
	assert request.await_count == 3
	assert after['check_in_phase'] == 'submission_rejected'
	assert checkin.solve_turnstile_in_page.await_count == 1


@pytest.mark.parametrize('message', ['签到名额暂未轮到本账号；登录、充值和模型调用不受影响', '签到活动暂未开放'])
async def test_explicit_business_rejection_is_not_reported_as_ambiguous(monkeypatch, gorouter, message):
	account, provider, context = gorouter
	request = AsyncMock(
		side_effect=[profile(), signed(False), (200, json.dumps({'success': False, 'message': message}))]
	)
	monkeypatch.setattr(checkin, 'request_in_page', request)
	success, before, after = await checkin.run_gorouter_check_in_in_page(account, account.name, provider)
	assert success is False
	assert before['success'] is True
	assert after['check_in_phase'] == 'submission_rejected'
	assert after['check_in_submitted'] is True
	assert message in after['check_in_error']
	assert '结果未确认' not in after['check_in_error']
	assert request.await_count == 3
	assert checkin.solve_turnstile_in_page.await_count == 1
	context.close.assert_awaited_once()


async def test_already_checked_business_response_still_confirms_by_status(monkeypatch, gorouter):
	account, provider, _ = gorouter
	request = AsyncMock(
		side_effect=[
			profile(),
			signed(False),
			(200, json.dumps({'success': False, 'message': '今日已签到'})),
			signed(True),
			profile(),
		]
	)
	monkeypatch.setattr(checkin, 'request_in_page', request)
	success, _, after = await checkin.run_gorouter_check_in_in_page(account, account.name, provider)
	assert success is True
	assert after['check_in_phase'] == 'confirmed'
	assert after['checked_in_today'] is True
	assert len([call for call in request.await_args_list if call.kwargs.get('method') == 'POST']) == 1


@pytest.mark.parametrize(
	'post_reply', [(200, '{}'), (200, 'null'), (200, '{"success":null}'), (500, '{"success":false}')]
)
async def test_missing_or_server_error_result_remains_ambiguous(monkeypatch, gorouter, post_reply):
	account, provider, _ = gorouter
	request = AsyncMock(side_effect=[profile(), signed(False), post_reply, signed(False), signed(False), signed(False)])
	monkeypatch.setattr(checkin, 'request_in_page', request)
	success, _, after = await checkin.run_gorouter_check_in_in_page(account, account.name, provider)
	assert success is False
	assert after['check_in_phase'] == 'submitted_unconfirmed'
	assert len([call for call in request.await_args_list if call.kwargs.get('method') == 'POST']) == 1


async def test_bad_gateway_post_can_be_confirmed_by_status_without_post_replay(monkeypatch, gorouter):
	account, provider, _ = gorouter
	request = AsyncMock(side_effect=[profile(), signed(False), (503, '<html>gateway</html>'), signed(True), profile()])
	monkeypatch.setattr(checkin, 'request_in_page', request)
	success, _, after = await checkin.run_gorouter_check_in_in_page(account, account.name, provider)
	assert success is True
	assert after['checked_in_today'] is True
	assert len([call for call in request.await_args_list if call.kwargs.get('method') == 'POST']) == 1
