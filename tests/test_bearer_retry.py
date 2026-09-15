import httpx
import pytest

from checkin import _bearer_login, run_bearer_check_in
from utils.config import AccountConfig, ProviderConfig


def test_bearer_login_recovers_from_read_timeout(monkeypatch):
	login_requests = []
	sleeps: list[float] = []

	def handler(request):
		if request.url.path == '/api/v1/auth/login':
			login_requests.append(request)
			if len(login_requests) == 1:
				raise httpx.ReadTimeout('The read operation timed out', request=request)
			return httpx.Response(200, json={'code': 0, 'data': {'access_token': 'test-access-token'}})
		if request.url.path == '/api/v1/user/profile':
			return httpx.Response(200, json={'code': 0, 'data': {'balance': 31.2}})
		assert request.method == 'GET'
		assert request.url.path == '/api/v1/user/daily-checkin'
		return httpx.Response(200, json={'code': 0, 'data': {'checked_in_today': True}})

	original_client = httpx.Client
	monkeypatch.setattr(
		'checkin.httpx.Client',
		lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
	)
	monkeypatch.setattr('checkin.time.sleep', sleeps.append)
	account = AccountConfig(
		cookies=None,
		name='Twinkle-test',
		provider='twinkle',
		email='account@example.test',
		password='test-password',
	)
	provider = ProviderConfig(
		name='twinkle',
		domain='https://twinkle.example.test',
		api_style='sub2api',
		login_api_path='/api/v1/auth/login',
		user_info_path='/api/v1/user/profile',
		sign_in_path='/api/v1/user/daily-checkin',
	)

	success, before, after = run_bearer_check_in(account, 'Twinkle-test', provider)

	assert success is True
	assert before is not None and after is not None
	assert before['quota'] == after['quota'] == 31.2
	assert len(login_requests) == 2
	assert sleeps == [2]


def login_account_and_provider():
	return (
		AccountConfig(cookies=None, email='account@example.test', password='test-password'),
		ProviderConfig(
			name='twinkle',
			domain='https://twinkle.example.test',
			login_api_path='/api/v1/auth/login',
		),
	)


@pytest.mark.parametrize('error_type', [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError])
def test_bearer_login_stops_after_three_transient_failures(monkeypatch, capsys, error_type):
	requests = []
	sleeps: list[float] = []
	account, provider = login_account_and_provider()

	def handler(request):
		requests.append(request)
		raise error_type('Sensitive upstream detail: test-password', request=request)

	monkeypatch.setattr('checkin.time.sleep', sleeps.append)
	with httpx.Client(transport=httpx.MockTransport(handler)) as client:
		with pytest.raises(RuntimeError, match=f'Bearer login failed after 3 attempts \\({error_type.__name__}\\)'):
			_bearer_login(client, account, 'Twinkle-test', provider, {})

	assert len(requests) == 3
	assert sleeps == [2, 4]
	output = capsys.readouterr().out
	assert 'test-password' not in output
	assert 'account@example.test' not in output


@pytest.mark.parametrize('status', [400, 401, 403, 429])
def test_bearer_login_does_not_retry_authentication_errors_or_rate_limits(monkeypatch, status):
	requests = []
	sleeps: list[float] = []
	account, provider = login_account_and_provider()

	def handler(request):
		requests.append(request)
		return httpx.Response(status, json={'message': 'Login rejected'})

	monkeypatch.setattr('checkin.time.sleep', sleeps.append)
	with httpx.Client(transport=httpx.MockTransport(handler)) as client:
		response = _bearer_login(client, account, 'Twinkle-test', provider, {})

	assert response.status_code == status
	assert len(requests) == 1
	assert sleeps == []
