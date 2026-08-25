import json

from checkin import run_xiaobai_check_in
from utils.config import AccountConfig, AppConfig


class FakeResponse:
	def __init__(self, status_code, payload):
		self.status_code = status_code
		self._payload = payload
		self.text = json.dumps(payload)

	def json(self):
		return self._payload


class FakeClient:
	def __init__(self, *, get_responses=(), checkin_responses=(), refresh_responses=()):
		self.get_responses = iter(get_responses)
		self.checkin_responses = iter(checkin_responses)
		self.refresh_responses = iter(refresh_responses)
		self.calls = []

	def __enter__(self):
		return self

	def __exit__(self, exc_type, exc, tb):
		return False

	def get(self, url, *, headers, timeout):
		self.calls.append({'method': 'GET', 'url': url, 'headers': headers.copy(), 'timeout': timeout})
		return next(self.get_responses)

	def post(self, url, *, headers=None, json=None, timeout):
		self.calls.append(
			{'method': 'POST', 'url': url, 'headers': (headers or {}).copy(), 'json': json, 'timeout': timeout}
		)
		if url.endswith('/api/v1/auth/refresh'):
			return next(self.refresh_responses)
		return next(self.checkin_responses)


def account(*, access_token=None, refresh_token=None):
	return AccountConfig(
		cookies=None,
		name='小白Code',
		provider='xiaobai',
		access_token=access_token,
		refresh_token=refresh_token,
	)


def provider():
	return AppConfig.load_from_env().providers['xiaobai']


def install_client(monkeypatch, client):
	monkeypatch.setattr('checkin.httpx.Client', lambda *args, **kwargs: client)


def test_signed_today_skips_duplicate_check_in(monkeypatch):
	client = FakeClient(
		get_responses=[FakeResponse(200, {'ok': True, 'data': {'signedToday': True}})],
	)
	install_client(monkeypatch, client)

	result = run_xiaobai_check_in(account(access_token='access-secret'), '小白Code', provider())

	assert result == (True, None, None)
	assert [call['method'] for call in client.calls] == ['GET']


def test_unsigned_account_posts_empty_json_and_accepts_reward_record(monkeypatch):
	client = FakeClient(
		get_responses=[FakeResponse(200, {'ok': True, 'data': {'signedToday': False, 'config': {'enabled': True}}})],
		checkin_responses=[
			FakeResponse(
				200,
				{
					'ok': True,
					'data': {
						'status': {'signedToday': True},
						'record': {'reward_amount': 0.25},
						'alreadyChecked': False,
					},
				},
			)
		],
	)
	install_client(monkeypatch, client)

	result = run_xiaobai_check_in(account(access_token='access-secret'), '小白Code', provider())

	assert result == (True, None, None)
	assert client.calls[1]['url'] == 'https://token.dialoguedui.com/checkin/api/checkin'
	assert client.calls[1]['json'] == {}


def test_401_refreshes_access_token_and_retries_status(monkeypatch):
	client = FakeClient(
		get_responses=[
			FakeResponse(401, {'ok': False, 'message': 'expired'}),
			FakeResponse(200, {'ok': True, 'data': {'signedToday': True}}),
		],
		refresh_responses=[
			FakeResponse(
				200,
				{'code': 0, 'data': {'access_token': 'rotated-access', 'refresh_token': 'rotated-refresh'}},
			)
		],
	)
	install_client(monkeypatch, client)

	result = run_xiaobai_check_in(
		account(access_token='expired-access', refresh_token='refresh-secret'),
		'小白Code',
		provider(),
	)

	assert result == (True, None, None)
	assert client.calls[1]['url'] == 'https://token.dialoguedui.com/api/v1/auth/refresh'
	assert client.calls[2]['headers']['Authorization'] == 'Bearer rotated-access'


def test_refresh_token_only_can_start_check_in(monkeypatch):
	client = FakeClient(
		get_responses=[FakeResponse(200, {'ok': True, 'data': {'signedToday': True}})],
		refresh_responses=[
			FakeResponse(
				200,
				{'code': 0, 'data': {'access_token': 'rotated-access', 'refresh_token': 'rotated-refresh'}},
			)
		],
	)
	install_client(monkeypatch, client)

	result = run_xiaobai_check_in(account(refresh_token='refresh-secret'), '小白Code', provider())

	assert result == (True, None, None)
	assert client.calls[0]['url'] == 'https://token.dialoguedui.com/api/v1/auth/refresh'
	assert client.calls[1]['headers']['Authorization'] == 'Bearer rotated-access'


def test_missing_tokens_fails_without_network(monkeypatch):
	client = FakeClient()
	install_client(monkeypatch, client)

	result = run_xiaobai_check_in(account(), '小白Code', provider())

	assert result == (
		False,
		None,
		{'success': False, 'check_in_error': 'Xiaobai requires access_token or refresh_token'},
	)
	assert client.calls == []


def test_status_502_retries_and_reports_final_error(monkeypatch):
	client = FakeClient(
		get_responses=[
			FakeResponse(502, {}),
			FakeResponse(502, {}),
			FakeResponse(502, {}),
		],
	)
	install_client(monkeypatch, client)
	monkeypatch.setattr('checkin.time.sleep', lambda seconds: None)

	result = run_xiaobai_check_in(account(access_token='access-secret'), '小白Code', provider())

	assert result == (
		False,
		None,
		{'success': False, 'check_in_error': 'Check-in status request failed - HTTP 502'},
	)
	assert [call['method'] for call in client.calls] == ['GET', 'GET', 'GET']


def test_status_502_recovers_on_retry(monkeypatch):
	client = FakeClient(
		get_responses=[
			FakeResponse(502, {}),
			FakeResponse(200, {'ok': True, 'data': {'signedToday': True}}),
		],
	)
	install_client(monkeypatch, client)
	monkeypatch.setattr('checkin.time.sleep', lambda seconds: None)

	result = run_xiaobai_check_in(account(access_token='access-secret'), '小白Code', provider())

	assert result == (True, None, None)
	assert [call['method'] for call in client.calls] == ['GET', 'GET']


def test_logs_never_include_tokens(monkeypatch, capsys):
	client = FakeClient(
		get_responses=[
			FakeResponse(401, {'ok': False, 'message': 'expired'}),
			FakeResponse(200, {'ok': True, 'data': {'signedToday': True}}),
		],
		refresh_responses=[
			FakeResponse(
				200,
				{'code': 0, 'data': {'access_token': 'rotated-access', 'refresh_token': 'rotated-refresh'}},
			)
		],
	)
	install_client(monkeypatch, client)

	run_xiaobai_check_in(
		account(access_token='expired-access', refresh_token='refresh-secret'),
		'小白Code',
		provider(),
	)

	output = capsys.readouterr().out
	for token in ('expired-access', 'refresh-secret', 'rotated-access', 'rotated-refresh'):
		assert token not in output
