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

	def load(self):
		return self.access, self.refresh

	def save(self, access, refresh, *, pending=False):
		self.saved.append((access, refresh, pending))
		if self.fail_pending and pending or self.fail_saved and not pending:
			raise TokenStateError('Encrypted state write failed')
		self.access, self.refresh = access, refresh

	def close(self):
		pass


class FakeClient:
	def __init__(self, *, profiles=None, statuses=(), checkins=(), refreshes=(), on_refresh=None):
		self.profiles = iter(profiles) if profiles is not None else None
		self.statuses = iter(statuses)
		self.checkins = iter(checkins)
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
		return next(self.checkins)


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
	return kwargs_seen


def run(acct=None, **kwargs):
	return run_xiaobai_check_in(acct or account(), '小白Code', AppConfig.load_from_env().providers['xiaobai'], **kwargs)


def test_signed_today_skips_duplicate_check_in(monkeypatch):
	client = FakeClient(statuses=[signed()])
	kwargs = install(monkeypatch, client)
	assert run() == (True, None, None)
	assert [x['method'] for x in client.calls] == ['GET', 'GET']
	assert kwargs[0]['trust_env'] is False


def test_unsigned_account_posts_empty_json(monkeypatch):
	client = FakeClient(
		statuses=[signed(False)],
		checkins=[FakeResponse(200, {'ok': True, 'data': {'record': {'reward_amount': 0.25}}})],
	)
	install(monkeypatch, client)
	assert run() == (True, None, None)
	assert client.calls[2]['url'].endswith('/checkin/api/checkin')
	assert client.calls[2]['json'] == {}


@pytest.mark.parametrize('balance', [12.34, '12.34', 0, -0.5])
def test_signed_today_reports_balance_without_an_extra_request(monkeypatch, balance):
	client = FakeClient(profiles=[profile_ok(balance=balance)], statuses=[signed()])
	install(monkeypatch, client)
	success, before, after = run()
	assert success and before == after
	assert after['quota'] == float(balance)
	assert [call['method'] for call in client.calls] == ['GET', 'GET']


def test_unsigned_account_reads_updated_balance(monkeypatch):
	client = FakeClient(
		profiles=[profile_ok(balance=10, used_balance=2), profile_ok(balance=10.25, used_balance=2)],
		statuses=[signed(False)],
		checkins=[FakeResponse(200, {'ok': True, 'data': {'record': {'reward_amount': 0.25}}})],
	)
	install(monkeypatch, client)
	success, before, after = run()
	assert success
	assert before['quota'] == 10 and after['quota'] == 10.25
	assert before['used_quota'] == after['used_quota'] == 2
	assert [call['method'] for call in client.calls] == ['GET', 'GET', 'POST', 'GET']
	assert client.calls[-1]['url'].endswith('/api/v1/auth/me')


@pytest.mark.parametrize(
	'failure',
	[
		FakeResponse(503, {}),
		expired(),
		FakeResponse(200, None, content_type='text/html'),
		profile_ok(),
		profile_ok(balance=None),
		profile_ok(balance=True),
		profile_ok(balance='private-token'),
		profile_ok(balance='NaN'),
		profile_ok(balance='Infinity'),
		FakeResponse(200, {'code': 0, 'data': {'balance': 10}}),
		httpx.ReadTimeout('private-token'),
	],
)
def test_optional_balance_failure_does_not_undo_checkin_or_rotate_tokens(monkeypatch, capsys, failure):
	state = FakeState('access-secret', 'refresh-secret')
	client = FakeClient(
		profiles=[profile_ok(balance=10), failure, failure, failure],
		statuses=[signed(False)],
		checkins=[FakeResponse(200, {'ok': True, 'data': {'record': {'reward_amount': 0.25}}})],
	)
	install(monkeypatch, client, state)
	success, before, after = run()
	assert success and before['quota'] == 10 and after is None
	assert sum(call['method'] == 'POST' for call in client.calls) == 1
	assert state.saved == []
	output = capsys.readouterr().out
	assert 'balance unavailable' in output
	assert 'private-token' not in output


def test_status_only_includes_balance_without_checkin(monkeypatch):
	client = FakeClient(profiles=[profile_ok(balance=0)], statuses=[signed(False)])
	install(monkeypatch, client)
	success, before, after = run(status_only=True)
	assert success and before['quota'] == after['quota'] == 0
	assert after['status_only'] and after['signedToday'] is False
	assert [call['method'] for call in client.calls] == ['GET', 'GET']


@pytest.mark.asyncio
@pytest.mark.parametrize(
	'before_balance, after_balance, confirmed',
	[(10, 10.25, True), (None, 10.25, False), (10, None, False), (10, 10, False)],
)
async def test_xiaobai_balance_notification_keeps_daily_success_skip(
	monkeypatch, tmp_path, before_balance, after_balance, confirmed
):
	client = FakeClient(
		profiles=[profile_ok(balance=before_balance), profile_ok(balance=after_balance)],
		statuses=[signed(False)],
		checkins=[FakeResponse(200, {'ok': True, 'data': {'record': {'reward_amount': 0.25}}})],
	)
	install(monkeypatch, client)
	monkeypatch.setenv('CHECKIN_DAILY_STATE_FILE', str(tmp_path / 'daily.json'))
	monkeypatch.setenv('NOTIFY_EVERY_RUN', 'true')
	monkeypatch.delenv('CHECKIN_STATE_GITHUB_TOKEN', raising=False)
	monkeypatch.delenv('CHECKIN_TARGET_ACCOUNTS', raising=False)
	monkeypatch.setattr(checkin, 'format_check_in_time', lambda: '2026-11-13 02:00:00')
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: [account()])
	monkeypatch.setattr(checkin, 'load_balance_hash', lambda: None)
	monkeypatch.setattr(checkin, 'save_balance_hash', Mock())
	monkeypatch.setattr(checkin, 'is_debug_enabled', lambda: False)
	runner = AsyncMock(side_effect=lambda acct, *args, **kwargs: run(acct))
	monkeypatch.setattr(checkin, 'check_in_account', runner)
	sender = Mock()
	monkeypatch.setattr(checkin.notify, 'push_message', sender)
	with pytest.raises(SystemExit) as first:
		await checkin.main()
	assert first.value.code == (0 if confirmed else 1)
	sender.assert_called_once()
	message = sender.call_args.args[1]
	assert message.count('小白Code') == 1 and f'{int(confirmed)}/1 成功' in message
	assert (f'余额 ${after_balance:.2f}' if after_balance is not None else '余额暂不可用') in message
	assert '余额 $0.00' not in message
	assert [call['method'] for call in client.calls] == ['GET', 'GET', 'POST', 'GET']
	if not confirmed:
		from utils.daily_success import DailySuccessState

		state = DailySuccessState('2026-11-13', str(tmp_path / 'daily.json'))
		assert not state.contains(account(), 0)
		state.close()
		return

	runner.reset_mock()
	sender.reset_mock()
	with pytest.raises(SystemExit) as second:
		await checkin.main()
	assert second.value.code == 0
	runner.assert_not_awaited()
	sender.assert_not_called()
	assert len(client.calls) == 4


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


def test_new_token_write_failure_stops_before_checkin(monkeypatch):
	state = FakeState('old-access', 'old-refresh', fail_saved=True)
	client = FakeClient(profiles=[expired()], refreshes=[refreshed()])
	install(monkeypatch, client, state)
	assert run(account('old-access', 'old-refresh'))[0] is False
	assert [x['method'] for x in client.calls] == ['GET', 'POST']


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
