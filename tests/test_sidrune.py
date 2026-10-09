import json
from pathlib import Path

import httpx
import pytest

from checkin import run_bearer_check_in
from utils.config import AccountConfig, AppConfig, _account_env_names, load_accounts_config


def sidrune_provider(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	return AppConfig.load_from_env().providers['sidrune']


def test_sidrune_uses_welfare_api_and_existing_token_refresh(monkeypatch):
	provider = sidrune_provider(monkeypatch)
	assert provider.domain == 'https://sidrune.ai'
	assert provider.api_style == 'sub2api'
	assert provider.login_path == '/welfare'
	assert provider.sign_in_path == '/api/v1/welfare/checkin'
	assert provider.check_in_status_path == '/api/v1/welfare/profile'
	assert provider.user_info_path == '/api/v1/auth/me'
	assert provider.auth_refresh_path == '/api/v1/auth/refresh'
	assert provider.api_user_key == ''
	assert provider.use_proxy is False


def test_sidrune_slot_appends_without_overwriting_existing_accounts(monkeypatch):
	for name in _account_env_names():
		monkeypatch.delenv(name, raising=False)
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', json.dumps([{'name': 'existing', 'access_token': 'existing-token'}]))
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_67',
		json.dumps(
			[
				{
					'name': 'Sidrune-dodo',
					'provider': 'sidrune',
					'access_token': 'test-access',
					'refresh_token': 'test-refresh',
				}
			]
		),
	)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_68',
		json.dumps(
			[
				{
					'name': 'Sidrune-tthxyc',
					'provider': 'sidrune',
					'access_token': 'second-access',
					'refresh_token': 'second-refresh',
				}
			]
		),
	)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_69',
		json.dumps(
			[
				{
					'name': 'Sidrune-5237',
					'provider': 'sidrune',
					'access_token': 'third-access',
					'refresh_token': 'third-refresh',
				}
			]
		),
	)
	accounts = load_accounts_config()
	assert accounts is not None
	assert [account.name for account in accounts] == ['existing', 'Sidrune-dodo', 'Sidrune-tthxyc', 'Sidrune-5237']
	assert accounts[0].access_token == 'existing-token'
	assert accounts[1].provider == 'sidrune'
	assert accounts[1].access_token == 'test-access'
	assert accounts[1].refresh_token == 'test-refresh'
	assert accounts[2].provider == 'sidrune'
	assert accounts[2].access_token == 'second-access'
	assert accounts[2].refresh_token == 'second-refresh'
	assert accounts[3].provider == 'sidrune'
	assert accounts[3].access_token == 'third-access'
	assert accounts[3].refresh_token == 'third-refresh'
	workflow = Path(__file__).resolve().parents[1] / '.github/workflows/checkin.yml'
	assert 'EXTRA_ACCOUNTS_67: ${{ secrets.EXTRA_ACCOUNTS_67 }}' in workflow.read_text()
	assert 'EXTRA_ACCOUNTS_68: ${{ secrets.EXTRA_ACCOUNTS_68 }}' in workflow.read_text()
	assert 'EXTRA_ACCOUNTS_69: ${{ secrets.EXTRA_ACCOUNTS_69 }}' in workflow.read_text()


@pytest.mark.parametrize(
	('checked', 'eligible', 'success', 'submissions'),
	[(False, True, True, 1), (True, True, True, 0), (False, False, False, 0)],
)
def test_sidrune_welfare_checkin_respects_status(monkeypatch, checked, eligible, success, submissions):
	provider = sidrune_provider(monkeypatch)
	account = AccountConfig(cookies=None, name='Sidrune-dodo', provider='sidrune', access_token='test-access')
	requests = []

	def handler(request):
		requests.append(request)
		assert request.headers['Authorization'] == 'Bearer test-access'
		if request.url.path == '/api/v1/auth/me':
			assert request.method == 'GET'
			return httpx.Response(200, json={'code': 0, 'data': {'balance': 2.5}})
		if request.url.path == '/api/v1/welfare/profile':
			assert request.method == 'GET'
			return httpx.Response(200, json={'code': 0, 'data': {'checked_in_today': checked, 'eligible': eligible}})
		assert request.url.path == '/api/v1/welfare/checkin' and request.method == 'POST'
		return httpx.Response(200, json={'code': 0, 'data': {'streak_day': 1, 'reward_amount': 0.1}})

	client = httpx.Client
	monkeypatch.setattr(
		'checkin.httpx.Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)
	monkeypatch.setattr('checkin.create_xiaobai_token_state', lambda *args: None)
	result, before, after = run_bearer_check_in(account, 'Sidrune-dodo', provider)
	assert result is success
	assert before is not None and after is not None
	assert before['quota'] == after['quota'] == 2.5
	assert len([request for request in requests if request.method == 'POST']) == submissions
