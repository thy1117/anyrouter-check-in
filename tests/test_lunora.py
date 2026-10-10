import json
from pathlib import Path

import httpx
import pytest

from checkin import run_bearer_check_in
from utils.config import AccountConfig, AppConfig, _account_env_names, load_accounts_config


def lunora_provider(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	return AppConfig.load_from_env().providers['lunora']


def test_lunora_uses_checkin_api_and_existing_token_refresh(monkeypatch):
	provider = lunora_provider(monkeypatch)
	assert provider.domain == 'https://www.uselunora.com'
	assert provider.api_style == 'sub2api'
	assert provider.login_path == '/checkin'
	assert provider.sign_in_path == '/api/v1/checkin/claim'
	assert provider.check_in_status_path == '/api/v1/checkin/status'
	assert provider.user_info_path == '/api/v1/auth/me'
	assert provider.auth_refresh_path == '/api/v1/auth/refresh'
	assert provider.api_user_key == ''
	assert provider.use_proxy is False


def test_lunora_slot_appends_without_overwriting_existing_accounts(monkeypatch):
	for name in _account_env_names():
		monkeypatch.delenv(name, raising=False)
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', json.dumps([{'name': 'existing', 'access_token': 'existing-token'}]))
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_78',
		json.dumps(
			[
				{
					'name': 'Lunora-1125',
					'provider': 'lunora',
					'access_token': 'test-access',
					'refresh_token': 'test-refresh',
				}
			]
		),
	)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_79',
		json.dumps(
			[
				{
					'name': 'Lunora-5237',
					'provider': 'lunora',
					'access_token': 'second-access',
					'refresh_token': 'second-refresh',
				}
			]
		),
	)
	accounts = load_accounts_config()
	assert accounts is not None
	assert [account.name for account in accounts] == ['existing', 'Lunora-1125', 'Lunora-5237']
	assert accounts[0].access_token == 'existing-token'
	assert accounts[1].provider == 'lunora'
	assert accounts[1].access_token == 'test-access'
	assert accounts[1].refresh_token == 'test-refresh'
	assert accounts[2].provider == 'lunora'
	assert accounts[2].access_token == 'second-access'
	assert accounts[2].refresh_token == 'second-refresh'
	workflow = Path(__file__).resolve().parents[1] / '.github/workflows/checkin.yml'
	assert 'EXTRA_ACCOUNTS_78: ${{ secrets.EXTRA_ACCOUNTS_78 }}' in workflow.read_text()
	assert 'EXTRA_ACCOUNTS_79: ${{ secrets.EXTRA_ACCOUNTS_79 }}' in workflow.read_text()


@pytest.mark.parametrize(
	('checked', 'eligible', 'success', 'submissions'),
	[(False, True, True, 1), (True, True, True, 0), (False, False, False, 0)],
)
def test_lunora_checkin_respects_status(monkeypatch, checked, eligible, success, submissions):
	provider = lunora_provider(monkeypatch)
	account = AccountConfig(cookies=None, name='Lunora-1125', provider='lunora', access_token='test-access')
	requests = []

	def handler(request):
		requests.append(request)
		assert request.headers['Authorization'] == 'Bearer test-access'
		if request.url.path == '/api/v1/auth/me':
			assert request.method == 'GET'
			return httpx.Response(200, json={'code': 0, 'data': {'balance': 0.0828}})
		if request.url.path == '/api/v1/checkin/status':
			assert request.method == 'GET'
			return httpx.Response(200, json={'code': 0, 'data': {'checked_today': checked, 'eligible': eligible}})
		assert request.url.path == '/api/v1/checkin/claim' and request.method == 'POST'
		return httpx.Response(
			200, json={'code': 0, 'data': {'record': {'attendance_day': 1, 'reward_amount': 0.1}, 'created': True}}
		)

	client = httpx.Client
	monkeypatch.setattr(
		'checkin.httpx.Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)
	monkeypatch.setattr('checkin.create_xiaobai_token_state', lambda *args: None)
	result, before, after = run_bearer_check_in(account, 'Lunora-1125', provider)
	assert result is success
	assert before is not None and after is not None
	assert before['quota'] == after['quota'] == 0.0828
	assert len([request for request in requests if request.method == 'POST']) == submissions
