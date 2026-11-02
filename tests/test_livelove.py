import json
from pathlib import Path
from unittest.mock import MagicMock, call

import httpx
import pytest

from checkin import run_bearer_check_in
from utils.config import AccountConfig, AppConfig, _account_env_names, load_accounts_config

ACCESS = 'fixture-' + 'access'
REFRESH = 'fixture-' + 'refresh'
ROTATED_ACCESS = 'rotated-' + 'access'
ROTATED_REFRESH = 'rotated-' + 'refresh'


@pytest.fixture
def livelove_provider(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	return AppConfig.load_from_env().providers['livelove']


def install_client(monkeypatch, handler):
	client = httpx.Client
	monkeypatch.setattr(
		'checkin.httpx.Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)


@pytest.mark.parametrize(
	('initial', 'http_status', 'business_code', 'malformed', 'confirm', 'success', 'posts', 'error_fragment'),
	[
		({'checked_in': True, 'eligible': True, 'enabled': True}, 200, 0, False, True, True, 0, None),
		({'checked_in': False, 'eligible': True, 'enabled': True}, 200, 0, False, True, True, 1, None),
		({'checked_in': False, 'eligible': True, 'enabled': True}, 200, 0, False, False, False, 1, 'not confirmed'),
		({'checked_in': False, 'eligible': False, 'enabled': True}, 200, 0, False, True, False, 0, 'not eligible'),
		({'checked_in': False, 'eligible': True, 'enabled': False}, 200, 0, False, True, False, 0, 'disabled'),
		({'checked_in': False, 'eligible': True}, 200, 0, False, True, False, 0, 'invalid'),
		({'checked_in': 0, 'eligible': True, 'enabled': True}, 200, 0, False, True, False, 0, 'invalid'),
		({'checked_in': False, 'eligible': True, 'enabled': True}, 200, 1, False, True, False, 0, 'invalid'),
		({'checked_in': False, 'eligible': True, 'enabled': True}, 503, 0, False, True, False, 0, 'invalid'),
		({'checked_in': False, 'eligible': True, 'enabled': True}, 200, 0, True, True, False, 0, None),
	],
)
def test_livelove_status_gate_and_confirmation(
	monkeypatch,
	livelove_provider,
	initial,
	http_status,
	business_code,
	malformed,
	confirm,
	success,
	posts,
	error_fragment,
):
	monkeypatch.setattr('checkin.create_xiaobai_token_state', lambda *args: None)
	account = AccountConfig(
		cookies=None, name='LiveLove-112581647', provider='livelove', access_token=ACCESS, refresh_token=REFRESH
	)
	checked_in = initial['checked_in']
	requests = []

	def handler(request):
		nonlocal checked_in
		requests.append(request)
		assert request.headers['Authorization'] == f'Bearer {ACCESS}'
		if request.url.path == '/api/v1/auth/me':
			return httpx.Response(200, json={'code': 0, 'data': {'balance': 1.25}})
		assert request.url.path == '/api/v1/user/checkin'
		if request.method == 'POST':
			assert request.content == b'{}'
			checked_in = confirm
			return httpx.Response(200, json={'code': 0, 'data': {'reward': 0.1}})
		if malformed:
			return httpx.Response(200, content=b'not-json', headers={'content-type': 'application/json'})
		return httpx.Response(
			http_status,
			json={
				'code': business_code,
				'data': {**initial, 'checked_in': checked_in},
			},
		)

	install_client(monkeypatch, handler)
	result, before, after = run_bearer_check_in(account, 'LiveLove-112581647', livelove_provider)
	assert result is success
	assert sum(request.method == 'POST' for request in requests) == posts
	if success:
		assert before['quota'] == after['quota'] == 1.25
	if error_fragment:
		assert error_fragment in after['check_in_error']
	elif not success:
		assert after['check_in_error']


def test_livelove_refresh_uses_durable_state(monkeypatch, livelove_provider):
	state = MagicMock()
	state.load.return_value = (ACCESS, REFRESH)
	monkeypatch.setattr('checkin.create_xiaobai_token_state', lambda *args: state)
	account = AccountConfig(
		cookies=None, name='LiveLove-112581647', provider='livelove', access_token=ACCESS, refresh_token=REFRESH
	)
	requests = []

	def handler(request):
		requests.append(request)
		if request.url.path == '/api/v1/auth/me':
			if request.headers['Authorization'] != f'Bearer {ROTATED_ACCESS}':
				return httpx.Response(401, json={'code': 401})
			return httpx.Response(200, json={'code': 0, 'data': {'balance': 1.25}})
		if request.url.path == '/api/v1/auth/refresh':
			assert request.method == 'POST'
			assert json.loads(request.content) == {'refresh_token': REFRESH}
			state.save.assert_called_once_with(ACCESS, REFRESH, pending=True)
			return httpx.Response(
				200, json={'code': 0, 'data': {'access_token': ROTATED_ACCESS, 'refresh_token': ROTATED_REFRESH}}
			)
		assert request.url.path == '/api/v1/user/checkin'
		return httpx.Response(200, json={'code': 0, 'data': {'checked_in': True, 'eligible': True, 'enabled': True}})

	install_client(monkeypatch, handler)
	success, before, after = run_bearer_check_in(account, 'LiveLove-112581647', livelove_provider)
	assert success is True and before['quota'] == after['quota'] == 1.25
	assert [request.url.path for request in requests] == [
		'/api/v1/auth/me',
		'/api/v1/auth/refresh',
		'/api/v1/auth/me',
		'/api/v1/user/checkin',
		'/api/v1/auth/me',
	]
	assert state.save.call_args_list == [
		call(ACCESS, REFRESH, pending=True),
		call(ROTATED_ACCESS, ROTATED_REFRESH),
	]
	state.close.assert_called_once()


def test_livelove_slot_and_regular_workflow_mapping(monkeypatch):
	for name in _account_env_names():
		monkeypatch.delenv(name, raising=False)
	monkeypatch.delenv('CHECKIN_DISABLED_PROVIDERS', raising=False)
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', json.dumps([{'name': 'existing', 'access_token': ACCESS}]))
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_129',
		json.dumps(
			[
				{
					'name': 'LiveLove-112581647',
					'provider': 'livelove',
					'access_token': ACCESS,
					'refresh_token': REFRESH,
				}
			]
		),
	)
	accounts = load_accounts_config()
	assert [account.name for account in accounts] == ['existing', 'LiveLove-112581647']
	assert accounts[1].provider == 'livelove'
	assert accounts[1].access_token == ACCESS and accounts[1].refresh_token == REFRESH
	assert accounts[1].password is None
	root = Path(__file__).resolve().parents[1]
	workflow = (root / '.github/workflows/checkin.yml').read_text()
	regular_job = workflow.split('\n  checkin:\n', 1)[1].split('\n  gorouter-status:', 1)[0]
	assert 'EXTRA_ACCOUNTS_129: ${{ secrets.EXTRA_ACCOUNTS_129 }}' in regular_job
	assert workflow.count('EXTRA_ACCOUNTS_129:') == 1
