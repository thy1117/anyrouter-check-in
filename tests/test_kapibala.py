import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest

import checkin
from utils.browser import request_in_page
from utils.config import AccountConfig, AppConfig


def response(data):
	return 200, json.dumps({'success': True, 'data': data})


def profile(uid=7, quota=500000):
	return response({'id': uid, 'username': 'alice', 'quota': quota})


def daily(checked):
	return response({'stats': {'checked_in_today': checked}})


@pytest.fixture
def flow(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	provider = AppConfig.load_from_env().providers['kapibala']
	account = AccountConfig(cookies=None, provider='kapibala', username='alice', password='private-secret')
	page = SimpleNamespace(goto=AsyncMock(), reload=AsyncMock())
	context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
	solve = AsyncMock(side_effect=[('login+/token', None), ('checkin+/token', None)])
	monkeypatch.setattr(checkin, 'launch_login_context', AsyncMock(return_value=context))
	monkeypatch.setattr(checkin, 'load_browser_login_settings', lambda *a, **kw: SimpleNamespace(wait_timeout_ms=1000))
	monkeypatch.setattr(checkin, 'prepare_browser_page', AsyncMock())
	monkeypatch.setattr(checkin, 'wait_for_waf_ready', AsyncMock())
	monkeypatch.setattr(checkin, 'solve_turnstile_in_page', solve)
	monkeypatch.setattr(checkin, 'reset_turnstile_in_page', AsyncMock())
	monkeypatch.setattr(checkin.asyncio, 'sleep', AsyncMock())
	config = {'turnstile_check': True, 'turnstile_site_key': 'public-site-key', 'checkin_enabled': True}
	login = {
		'access_token': 'private-access',
		'session': {'sid': 'private-session'},
		'user': {'id': 7, 'username': 'alice'},
	}
	return SimpleNamespace(
		account=account, provider=provider, page=page, context=context, solve=solve, config=config, login=login
	)


def install_requests(monkeypatch, responses):
	request = AsyncMock(side_effect=responses)
	monkeypatch.setattr(checkin, 'request_in_page', request)
	return request


def posts(request, path):
	return [c for c in request.await_args_list if c.kwargs.get('method') == 'POST' and urlsplit(c.args[1]).path == path]


@pytest.mark.parametrize('checked,verification', [(False, True), (True, True), (False, False)])
async def test_browser_login_and_checkin_use_separate_challenges_and_one_session(
	monkeypatch, flow, checked, verification
):
	flow.config['turnstile_check'] = verification
	responses = [response(flow.config), response(flow.login), profile(), daily(checked)]
	if not checked:
		responses += [response({'quota_awarded': 100000}), daily(True)]
	responses += [profile(quota=600000), response({})]
	request = install_requests(monkeypatch, responses)
	result = await checkin._run_account_checkin(flow.account, 'Kapibala-test', flow.provider)
	assert result[0] and result[2]['checked_in_today']
	assert flow.provider.login_in_page and flow.provider.login_path == '/sign-in'
	login_posts = posts(request, '/api/user/login')
	checkin_posts = posts(request, '/api/user/checkin')
	assert len(login_posts) == 1
	assert len(checkin_posts) == int(not checked)
	assert login_posts[0].kwargs['json_body'] == {'username': 'alice', 'password': 'private-secret'}
	if verification:
		assert parse_qs(urlsplit(login_posts[0].args[1]).query)['turnstile'] == ['login+/token']
		if not checked:
			assert parse_qs(urlsplit(checkin_posts[0].args[1]).query)['turnstile'] == ['checkin+/token']
	else:
		assert not urlsplit(login_posts[0].args[1]).query
		assert not urlsplit(checkin_posts[0].args[1]).query
	assert flow.solve.await_count == (1 + int(not checked)) * int(verification)
	for call in request.await_args_list[2:]:
		assert call.args[0] is flow.page
		assert call.kwargs['headers']['Authorization'] == 'Bearer private-access'
		assert call.kwargs['headers']['X-Auth-Session'] == 'private-session'
	assert len(posts(request, '/api/user/auth/logout')) == 1
	flow.context.close.assert_awaited_once()


@pytest.mark.parametrize('failure', ['empty_token', 'missing_key', 'invalid_settings', 'disabled', 'encrypted'])
async def test_invalid_login_preconditions_never_submit_credentials(monkeypatch, flow, failure):
	if failure == 'empty_token':
		flow.solve.side_effect = None
		flow.solve.return_value = (None, 'verification incomplete')
	elif failure == 'missing_key':
		flow.config['turnstile_site_key'] = ''
	elif failure == 'invalid_settings':
		flow.config.pop('turnstile_check')
	elif failure == 'disabled':
		flow.config['checkin_enabled'] = False
	else:
		flow.config['password_login_encryption_enabled'] = True
	request = install_requests(monkeypatch, [response(flow.config)])
	assert not (await checkin._run_account_checkin(flow.account, 'Kapibala-test', flow.provider))[0]
	assert not [c for c in request.await_args_list if c.kwargs.get('method') == 'POST']
	flow.context.close.assert_awaited_once()


@pytest.mark.parametrize(
	'failure', ['rejected', 'gateway', 'transport', 'bad_json', 'username', 'user_id', 'profile_id']
)
async def test_failed_login_or_identity_never_submits_checkin(monkeypatch, flow, capsys, failure):
	login_response = response(flow.login)
	if failure == 'rejected':
		login_response = (200, '{"success":false,"message":"private-secret"}')
	elif failure == 'gateway':
		login_response = (502, 'gateway')
	elif failure == 'transport':
		login_response = RuntimeError('private-secret login+/token')
	elif failure == 'bad_json':
		login_response = (200, 'private-secret')
	elif failure == 'username':
		flow.login['user']['username'] = 'another-account'
		login_response = response(flow.login)
	elif failure == 'user_id':
		flow.account.api_user = '8'
	responses = [response(flow.config), login_response]
	if failure == 'profile_id':
		responses.append(profile(uid=8))
	if failure in ('username', 'user_id', 'profile_id'):
		responses.append(response({}))
	request = install_requests(monkeypatch, responses)
	assert not (await checkin._run_account_checkin(flow.account, 'Kapibala-test', flow.provider))[0]
	assert len(posts(request, '/api/user/login')) == 1
	assert not posts(request, '/api/user/checkin')
	output = capsys.readouterr().out
	assert 'private-secret' not in output and 'login+/token' not in output
	flow.context.close.assert_awaited_once()


@pytest.mark.parametrize(
	'submission',
	[(200, '{"success":false,"message":"Turnstile rejected"}'), (502, 'gateway'), RuntimeError('checkin+/token')],
)
async def test_checkin_failure_does_not_repeat_login_or_checkin(monkeypatch, flow, submission, capsys):
	responses = [response(flow.config), response(flow.login), profile(), daily(False), submission]
	if not isinstance(submission, tuple) or submission[0] == 502:
		responses += [daily(False)] * 3
	responses.append(response({}))
	request = install_requests(monkeypatch, responses)
	assert not (await checkin._run_account_checkin(flow.account, 'Kapibala-test', flow.provider))[0]
	assert len(posts(request, '/api/user/login')) == len(posts(request, '/api/user/checkin')) == 1
	assert len(posts(request, '/api/user/auth/logout')) == 1
	assert 'checkin+/token' not in capsys.readouterr().out


async def test_json_login_body_is_passed_to_fetch_without_retry():
	page = SimpleNamespace(evaluate=AsyncMock(return_value={'status': 502, 'body': 'gateway'}))
	body = {'username': 'alice', 'password': 'private-secret'}
	assert await request_in_page(page, '/api/user/login', method='POST', json_body=body) == (502, 'gateway')
	page.evaluate.assert_awaited_once()
	assert page.evaluate.await_args.args[1]['body'] == body
	assert 'JSON.stringify(body)' in page.evaluate.await_args.args[0]


def test_kapibala_partial_config_override_retains_browser_login(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.setenv('EXTRA_PROVIDERS', '{"kapibala":{"domain":"https://kapibala.asia"}}')
	assert AppConfig.load_from_env().providers['kapibala'].login_in_page
