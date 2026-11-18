import json
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import checkin
from checkin import run_xiaobai_check_in
from utils.config import AccountConfig, AppConfig
from utils.xiaobai_token_state import TokenStateError


class FakeResponse:
	def __init__(self, status_code, payload, *, content_type='application/json'):
		self.status_code = status_code
		self._payload = payload
		self.headers = {'content-type': content_type}
		self.text = json.dumps(payload)

	def json(self):
		if self.headers['content-type'] == 'text/html':
			raise json.JSONDecodeError('HTML, not JSON', '<html>', 0)
		return self._payload


class FakeState:
	def __init__(self, access='access-secret', refresh='', *, fail_pending=False, fail_saved=False):
		self.access, self.refresh = access, refresh
		self.fail_pending = fail_pending
		self.fail_saved = fail_saved
		self.saved = []
		self.closed = False

	def load(self):
		return self.access, self.refresh

	def save(self, access, refresh, *, pending=False):
		self.saved.append((access, refresh, pending))
		if self.fail_pending and pending or self.fail_saved and not pending:
			raise TokenStateError('Encrypted state write failed')
		self.access, self.refresh = access, refresh

	def close(self):
		self.closed = True


class FakeClient:
	def __init__(self, *, profiles=None, statuses=(), refreshes=(), on_refresh=None):
		self.profiles = iter(profiles) if profiles is not None else None
		self.statuses = iter(statuses)
		self.refreshes = iter(refreshes)
		self.calls = []
		self.on_refresh = on_refresh

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return False

	def get(self, url, *, headers, timeout):
		self.calls.append({'method': 'GET', 'url': url, 'headers': headers.copy()})
		if url.endswith('/auth/me'):
			response = (
				next(self.profiles) if self.profiles is not None else FakeResponse(200, {'code': 0, 'data': {'id': 1}})
			)
		else:
			response = next(self.statuses)
		if isinstance(response, Exception):
			raise response
		return response

	def post(self, url, *, headers=None, json=None, timeout):
		self.calls.append({'method': 'POST', 'url': url, 'headers': (headers or {}).copy(), 'json': json})
		if url.endswith('/auth/refresh'):
			if self.on_refresh:
				self.on_refresh()
			response = next(self.refreshes)
			if isinstance(response, Exception):
				raise response
			return response
		pytest.fail('HTTP must not submit Xiaobai check-in without its browser verification')


def account(access='access-secret', refresh=''):
	return AccountConfig(cookies=None, name='小白Code', provider='xiaobai', access_token=access, refresh_token=refresh)


def profile_ok(**fields):
	return FakeResponse(200, {'code': 0, 'data': {'id': 1, **fields}})


def expired():
	return FakeResponse(401, {'code': 'TOKEN_EXPIRED', 'message': 'Token has expired'})


def signed(value=True):
	return FakeResponse(200, {'ok': True, 'data': {'signedToday': value, 'config': {'enabled': True}}})


def refreshed():
	return FakeResponse(200, {'code': 0, 'data': {'access_token': 'new-access', 'refresh_token': 'new-refresh'}})


def install(monkeypatch, client, state=None):
	kwargs_seen = []
	monkeypatch.setattr('checkin.httpx.Client', lambda **kwargs: kwargs_seen.append(kwargs) or client)
	monkeypatch.setattr('checkin.create_xiaobai_token_state', lambda *args: state)
	monkeypatch.setattr('checkin.time.sleep', lambda *_: None)
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	monkeypatch.setattr(
		checkin,
		'_run_xiaobai_check_in_in_page',
		AsyncMock(side_effect=AssertionError('Browser handoff was not expected')),
	)
	return kwargs_seen


def run(acct=None, **kwargs):
	return run_xiaobai_check_in(acct or account(), '小白Code', AppConfig.load_from_env().providers['xiaobai'], **kwargs)


def test_signed_today_skips_duplicate_check_in(monkeypatch):
	client = FakeClient(statuses=[signed()])
	kwargs = install(monkeypatch, client)
	assert run() == (True, None, None)
	assert [x['method'] for x in client.calls] == ['GET', 'GET']
	assert kwargs[0]['trust_env'] is False


def test_unsigned_account_hands_off_authenticated_identity_without_http_post(monkeypatch):
	client = FakeClient(statuses=[signed(False)])
	install(monkeypatch, client)
	provider = AppConfig.load_from_env().providers['xiaobai']
	result = (True, None, {'signedToday': True, 'check_in_phase': 'confirmed'})
	browser = AsyncMock(return_value=result)
	monkeypatch.setattr(checkin, '_run_xiaobai_check_in_in_page', browser)
	assert run() == result
	browser.assert_awaited_once_with('小白Code', provider, 'access-secret', expected_user_id='1', user_info_before=None)
	assert [call['method'] for call in client.calls] == ['GET', 'GET']


@pytest.mark.parametrize('balance', [12.34, '12.34', 0, -0.5])
def test_signed_today_reports_balance_without_an_extra_request(monkeypatch, balance):
	client = FakeClient(profiles=[profile_ok(balance=balance)], statuses=[signed()])
	install(monkeypatch, client)
	success, before, after = run()
	assert success and before == after
	assert after['quota'] == float(balance)
	assert [call['method'] for call in client.calls] == ['GET', 'GET']


def test_unsigned_account_preserves_browser_balance_result(monkeypatch):
	client = FakeClient(profiles=[profile_ok(balance=10, used_balance=2)], statuses=[signed(False)])
	install(monkeypatch, client)
	before_info = checkin.parse_sub2api_profile_response(200, profile_ok(balance=10, used_balance=2).text)
	after_info = checkin.parse_sub2api_profile_response(200, profile_ok(balance=10.25, used_balance=2).text)
	browser = AsyncMock(return_value=(True, before_info, after_info))
	monkeypatch.setattr(checkin, '_run_xiaobai_check_in_in_page', browser)
	success, before, after = run()
	assert success
	assert before['quota'] == 10 and after['quota'] == 10.25
	assert before['used_quota'] == after['used_quota'] == 2
	assert browser.await_args.kwargs['user_info_before'] == before_info
	assert [call['method'] for call in client.calls] == ['GET', 'GET']


@pytest.mark.parametrize('after', [None, {'success': False, 'balance_read_error': 'ReadTimeout', 'signedToday': True}])
def test_optional_browser_balance_failure_does_not_undo_checkin_or_rotate_tokens(monkeypatch, after):
	state = FakeState('access-secret', 'refresh-secret')
	client = FakeClient(profiles=[profile_ok(balance=10)], statuses=[signed(False)])
	install(monkeypatch, client, state)
	before_info = checkin.parse_sub2api_profile_response(200, profile_ok(balance=10).text)
	browser = AsyncMock(return_value=(True, before_info, after))
	monkeypatch.setattr(checkin, '_run_xiaobai_check_in_in_page', browser)
	success, before, returned_after = run()
	assert success and before['quota'] == 10 and returned_after is after
	browser.assert_awaited_once()
	assert [call['method'] for call in client.calls] == ['GET', 'GET']
	assert state.saved == []
	assert state.closed


def test_status_only_includes_balance_without_checkin(monkeypatch):
	client = FakeClient(profiles=[profile_ok(balance=0)], statuses=[signed(False)])
	install(monkeypatch, client)
	success, before, after = run(status_only=True)
	assert success and before['quota'] == after['quota'] == 0
	assert after['status_only'] and after['signedToday'] is False
	assert [call['method'] for call in client.calls] == ['GET', 'GET']


@pytest.mark.asyncio
@pytest.mark.parametrize(
	'before_balance, after_balance',
	[(10, 10.25), (None, 10.25), (10, None), (10, 10)],
)
async def test_xiaobai_confirmed_success_keeps_optional_balances_and_full_runs(
	monkeypatch, before_balance, after_balance
):
	client = FakeClient(profiles=[profile_ok(balance=before_balance)], statuses=[signed(False)])
	install(monkeypatch, client)
	before_info = (
		checkin.parse_sub2api_profile_response(200, profile_ok(balance=before_balance).text)
		if before_balance is not None
		else None
	)
	after_info = (
		checkin.parse_sub2api_profile_response(200, profile_ok(balance=after_balance).text)
		if after_balance is not None
		else {'success': False, 'signedToday': True}
	)
	browser = AsyncMock(return_value=(True, before_info, after_info))
	monkeypatch.setattr(checkin, '_run_xiaobai_check_in_in_page', browser)
	monkeypatch.setenv('CHECKIN_DAILY_STATE_FILE', '')
	monkeypatch.setenv('NOTIFY_EVERY_RUN', 'true')
	monkeypatch.delenv('CHECKIN_STATE_GITHUB_TOKEN', raising=False)
	monkeypatch.delenv('CHECKIN_TARGET_ACCOUNTS', raising=False)
	monkeypatch.setattr(checkin, 'format_check_in_time', lambda: '2026-11-13 02:00:00')
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: [account()])
	monkeypatch.setattr(checkin, 'load_balance_hash', lambda: None)
	monkeypatch.setattr(checkin, 'save_balance_hash', Mock())
	monkeypatch.setattr(checkin, 'is_debug_enabled', lambda: False)
	# Exercise the real async dispatch and worker-thread handoff from the full main.
	runner = AsyncMock(wraps=checkin._run_account_checkin)
	monkeypatch.setattr(checkin, '_run_account_checkin', runner)
	sender = Mock(return_value={'Telegram': True})
	monkeypatch.setattr(checkin.notify, 'push_message', sender)
	with pytest.raises(SystemExit) as first:
		await checkin.main()
	assert first.value.code == 0
	sender.assert_called_once()
	message = sender.call_args.args[1]
	assert message.count('小白Code') == 1 and '1/1 成功' in message
	if after_balance is not None and before_balance is not None:
		assert f'余额 ${after_balance:.2f}' in message
	assert '未确认到账' not in message
	assert '余额 $0.00' not in message
	assert [call['method'] for call in client.calls] == ['GET', 'GET']
	browser.assert_awaited_once()
	assert browser.await_args.kwargs['user_info_before'] == before_info
	runner.reset_mock()
	sender.reset_mock()
	browser.reset_mock()
	# A later full round still visits the account but honors server already-signed
	# status without another POST or another credited-reward claim.
	client.profiles = iter([profile_ok(balance=after_balance)])
	client.statuses = iter([signed(True)])
	with pytest.raises(SystemExit) as second:
		await checkin.main()
	assert second.value.code == 0
	runner.assert_awaited_once()
	sender.assert_called_once()
	assert '1/1 成功' in sender.call_args.args[1]
	assert '签到 +$' not in sender.call_args.args[1]
	browser.assert_not_awaited()
	assert [call['method'] for call in client.calls] == ['GET', 'GET', 'GET', 'GET']


def test_expired_session_refreshes_correct_path_and_persists_before_status(monkeypatch):
	state = FakeState('old-access', 'old-refresh')
	client = FakeClient(profiles=[expired(), profile_ok()], statuses=[signed()], refreshes=[refreshed()])
	client.on_refresh = lambda: state.saved[-1][2] or pytest.fail('Rotation happened before pending state was saved')
	install(monkeypatch, client, state)
	assert run(account('old-access', 'old-refresh')) == (True, None, None)
	assert client.calls[1]['url'] == 'https://token.dialoguedui.com/api/v1/auth/refresh'
	assert client.calls[1]['headers'] == {}  # Do not send the expired access token to refresh.
	assert state.saved == [('old-access', 'old-refresh', True), ('new-access', 'new-refresh', False)]
	assert client.calls[-1]['headers']['Authorization'] == 'Bearer new-access'


@pytest.mark.parametrize('expires_at', ['profile', 'status'])
def test_browser_receives_persisted_refreshed_access_and_authenticated_identity(monkeypatch, expires_at):
	state = FakeState('old-access', 'old-refresh')
	client = FakeClient(
		profiles=[expired(), profile_ok(id=73, balance=4.5)]
		if expires_at == 'profile'
		else [profile_ok(id=73, balance=4.5), profile_ok(id=73, balance=4.5)],
		statuses=[signed(False)] if expires_at == 'profile' else [expired(), signed(False)],
		refreshes=[refreshed()],
	)
	client.on_refresh = lambda: state.saved[-1][2] or pytest.fail('Refresh needs a persisted pending marker')
	install(monkeypatch, client, state)
	provider = AppConfig.load_from_env().providers['xiaobai']
	before_info = checkin.parse_sub2api_profile_response(200, profile_ok(id=73, balance=4.5).text)
	after_info = {**before_info, 'quota': 4.75, 'signedToday': True}
	result = (True, before_info, after_info)

	async def verified_browser(name, passed_provider, access, *, expected_user_id, user_info_before):
		assert state.saved == [('old-access', 'old-refresh', True), ('new-access', 'new-refresh', False)]
		assert not state.closed
		assert name == '小白Code' and passed_provider == provider
		assert access == 'new-access'
		assert expected_user_id == '73'
		assert user_info_before == before_info
		assert client.calls[-1]['url'].endswith('/checkin/api/status')
		assert client.calls[-1]['headers']['Authorization'] == 'Bearer new-access'
		return result

	browser = AsyncMock(side_effect=verified_browser)
	monkeypatch.setattr(checkin, '_run_xiaobai_check_in_in_page', browser)
	assert run(account('old-access', 'old-refresh')) == result
	browser.assert_awaited_once()
	assert state.closed
	assert sum(call['method'] == 'POST' for call in client.calls) == 1
	assert all(call['url'].endswith('/auth/refresh') for call in client.calls if call['method'] == 'POST')


def test_browser_restores_latest_state_instead_of_stale_account_tokens(monkeypatch):
	state = FakeState('latest-access', 'latest-refresh')
	client = FakeClient(profiles=[profile_ok(id=73)], statuses=[signed(False)])
	install(monkeypatch, client, state)
	browser = AsyncMock(return_value=(True, None, {'signedToday': True}))
	monkeypatch.setattr(checkin, '_run_xiaobai_check_in_in_page', browser)
	assert run(account('stale-access', 'stale-refresh'))[0] is True
	browser.assert_awaited_once_with(
		'小白Code',
		AppConfig.load_from_env().providers['xiaobai'],
		'latest-access',
		expected_user_id='73',
		user_info_before=None,
	)
	assert all(call['headers']['Authorization'] == 'Bearer latest-access' for call in client.calls)
	assert state.saved == []
	assert state.closed


def test_status_refresh_identity_change_stops_before_browser(monkeypatch):
	state = FakeState('old-access', 'old-refresh')
	client = FakeClient(profiles=[profile_ok(id=73), profile_ok(id=74)], statuses=[expired()], refreshes=[refreshed()])
	install(monkeypatch, client, state)
	result = run(account('old-access', 'old-refresh'))
	assert result[0] is False
	assert 'different user identity' in result[2]['check_in_error']
	checkin._run_xiaobai_check_in_in_page.assert_not_called()
	assert state.saved == [('old-access', 'old-refresh', True), ('new-access', 'new-refresh', False)]
	assert [call['method'] for call in client.calls] == ['GET', 'GET', 'POST', 'GET']
	assert state.closed


def test_next_run_restores_rotated_tokens_without_using_stale_secret(monkeypatch):
	state = FakeState('new-access', 'new-refresh')
	client = FakeClient(statuses=[signed()])
	install(monkeypatch, client, state)
	assert run(account('old-access', 'old-refresh'))[0] is True
	assert client.calls[0]['headers']['Authorization'] == 'Bearer new-access'
	assert state.saved == []


def test_refresh_token_only_can_start(monkeypatch):
	state = FakeState('', 'old-refresh')
	client = FakeClient(statuses=[signed()], refreshes=[refreshed()])
	install(monkeypatch, client, state)
	assert run(account('', 'old-refresh'))[0] is True
	assert client.calls[0]['url'].endswith('/api/v1/auth/refresh')
	assert state.saved[-1] == ('new-access', 'new-refresh', False)


def test_invalid_refresh_stops_without_status_or_checkin(monkeypatch):
	state = FakeState('old-access', 'old-refresh')
	client = FakeClient(profiles=[expired()], refreshes=[FakeResponse(401, {'reason': 'REFRESH_TOKEN_INVALID'})])
	install(monkeypatch, client, state)
	result = run(account('old-access', 'old-refresh'))
	assert result[0] is False
	assert 'HTTP 401 (REFRESH_TOKEN_INVALID)' in result[2]['check_in_error']
	assert all('/checkin/api/' not in x['url'] for x in client.calls)
	assert state.saved[-1][2] is False


def test_missing_storage_prevents_real_rotation(monkeypatch):
	client = FakeClient(profiles=[expired()])
	install(monkeypatch, client)
	result = run(account('old-access', 'old-refresh'))
	assert 'refusing an unpersisted rotation' in result[2]['check_in_error']
	assert all(x['method'] == 'GET' for x in client.calls)


def test_pending_write_failure_prevents_rotation(monkeypatch):
	state = FakeState('old-access', 'old-refresh', fail_pending=True)
	client = FakeClient(profiles=[expired()])
	install(monkeypatch, client, state)
	assert run(account('old-access', 'old-refresh'))[0] is False
	assert [x['method'] for x in client.calls] == ['GET']
	checkin._run_xiaobai_check_in_in_page.assert_not_called()


def test_new_token_write_failure_stops_before_checkin(monkeypatch):
	state = FakeState('old-access', 'old-refresh', fail_saved=True)
	client = FakeClient(profiles=[expired()], refreshes=[refreshed()])
	install(monkeypatch, client, state)
	assert run(account('old-access', 'old-refresh'))[0] is False
	assert [x['method'] for x in client.calls] == ['GET', 'POST']
	checkin._run_xiaobai_check_in_in_page.assert_not_called()


def test_refresh_timeout_is_not_retried_and_remains_pending(monkeypatch):
	state = FakeState('old-access', 'old-refresh')
	client = FakeClient(profiles=[expired()], refreshes=[httpx.ReadTimeout('old-refresh')])
	install(monkeypatch, client, state)
	result = run(account('old-access', 'old-refresh'))
	assert result[0] is False
	assert result[2]['check_in_error'].endswith('ReadTimeout')
	assert state.saved == [('old-access', 'old-refresh', True)]


def test_status_502_with_valid_profile_does_not_refresh_or_blindly_post(monkeypatch):
	state = FakeState('access-secret', 'refresh-secret')
	client = FakeClient(statuses=[FakeResponse(502, {}) for _ in range(3)])
	install(monkeypatch, client, state)
	result = run(account('access-secret', 'refresh-secret'))
	assert result[0] is False
	assert result[2]['check_in_error'] == 'Check-in status request failed - HTTP 502'
	assert all(x['method'] == 'GET' for x in client.calls)
	assert state.saved == []


def test_status_502_recovers_on_retry(monkeypatch):
	client = FakeClient(statuses=[FakeResponse(502, {}), signed()])
	install(monkeypatch, client)
	assert run()[0] is True


@pytest.mark.parametrize('stage', ['profile', 'status'])
def test_get_connection_error_recovers_without_rotating_tokens(monkeypatch, capsys, stage):
	error = httpx.ConnectError('private-token')
	client = FakeClient(
		profiles=[error, profile_ok()] if stage == 'profile' else None,
		statuses=[error, signed()] if stage == 'status' else [signed()],
	)
	state = FakeState('access-secret', 'refresh-secret')
	install(monkeypatch, client, state)
	assert run(account('access-secret', 'refresh-secret'))[0] is True
	assert [x['method'] for x in client.calls] == ['GET', 'GET', 'GET']
	assert state.saved == []
	assert 'private-token' not in capsys.readouterr().out


def test_profile_connection_failure_stops_after_three_attempts(monkeypatch, capsys):
	client = FakeClient(profiles=[httpx.ConnectError('private-token') for _ in range(3)])
	state = FakeState('access-secret', 'refresh-secret')
	install(monkeypatch, client, state)
	result = run(account('access-secret', 'refresh-secret'))
	assert result[0] is False and result[2]['check_in_error'].endswith('ConnectError')
	assert [x['method'] for x in client.calls] == ['GET', 'GET', 'GET']
	assert state.saved == []
	assert 'private-token' not in capsys.readouterr().out


def test_status_401_refreshes_once_and_revalidates_profile(monkeypatch):
	state = FakeState('access-secret', 'refresh-secret')
	client = FakeClient(statuses=[expired(), signed()], refreshes=[refreshed()])
	install(monkeypatch, client, state)
	assert run(account('access-secret', 'refresh-secret'))[0] is True
	assert [x['method'] for x in client.calls] == ['GET', 'GET', 'POST', 'GET', 'GET']


def test_status_only_never_submits_checkin(monkeypatch):
	client = FakeClient(statuses=[signed(False)])
	install(monkeypatch, client)
	result = run(status_only=True)
	assert result == (True, None, {'success': True, 'status_only': True, 'signedToday': False})
	assert all(x['method'] == 'GET' for x in client.calls)
	checkin._run_xiaobai_check_in_in_page.assert_not_called()


@pytest.mark.parametrize('status_only, already_signed', [(True, False), (False, True)])
def test_refresh_then_status_only_or_already_signed_never_opens_browser(monkeypatch, status_only, already_signed):
	state = FakeState('old-access', 'old-refresh')
	client = FakeClient(profiles=[expired(), profile_ok()], statuses=[signed(already_signed)], refreshes=[refreshed()])
	install(monkeypatch, client, state)
	assert run(account('old-access', 'old-refresh'), status_only=status_only)[0] is True
	checkin._run_xiaobai_check_in_in_page.assert_not_called()
	assert state.saved == [('old-access', 'old-refresh', True), ('new-access', 'new-refresh', False)]
	assert all(call['url'].endswith('/auth/refresh') for call in client.calls if call['method'] == 'POST')
	assert state.closed


def test_missing_tokens_fails_without_network(monkeypatch):
	client = FakeClient()
	install(monkeypatch, client)
	assert run(account('', ''))[2]['check_in_error'] == 'Xiaobai requires access_token or refresh_token'
	assert client.calls == []


@pytest.mark.parametrize('payload', [{}, {'code': 0, 'data': {}}, {'code': 'failure', 'data': {'id': 1}}])
def test_200_without_profile_identity_does_not_authenticate(monkeypatch, payload):
	client = FakeClient(profiles=[FakeResponse(200, payload)])
	install(monkeypatch, client)
	assert run()[0] is False
	assert len(client.calls) == 1


def test_html_refresh_error_preserves_http_status_and_type(monkeypatch):
	state = FakeState('old-access', 'old-refresh')
	client = FakeClient(profiles=[expired()], refreshes=[FakeResponse(200, None, content_type='text/html')])
	install(monkeypatch, client, state)
	result = run(account('old-access', 'old-refresh'))
	assert 'HTTP 200, non-JSON response (text/html)' in result[2]['check_in_error']
	assert state.saved[-1][2] is True  # Ambiguous response is not safe to replay.


def test_html_profile_is_not_treated_as_valid_session(monkeypatch):
	client = FakeClient(profiles=[FakeResponse(200, None, content_type='text/html')])
	install(monkeypatch, client)
	assert 'non-JSON' in run()[2]['check_in_error']
	assert len(client.calls) == 1


@pytest.mark.parametrize('github_actions', ['true', 'false'])
def test_logs_never_echo_tokens_or_arbitrary_server_messages(monkeypatch, capsys, github_actions):
	monkeypatch.setenv('GITHUB_ACTIONS', github_actions)
	state = FakeState('old-access', 'old-refresh')
	client = FakeClient(
		profiles=[expired()], refreshes=[FakeResponse(401, {'code': 'old-refresh', 'message': 'old-access'})]
	)
	install(monkeypatch, client, state)
	run(account('old-access', 'old-refresh'))
	lines = capsys.readouterr().out.splitlines()
	# Runner protocol commands register redaction; they are not displayed logs.
	mask_commands = [line for line in lines if line.startswith('::add-mask::')]
	assert mask_commands == (['::add-mask::old-access', '::add-mask::old-refresh'] if github_actions == 'true' else [])
	output = '\n'.join(line for line in lines if not line.startswith('::add-mask::'))
	assert 'old-access' not in output
	assert 'old-refresh' not in output
