import json
from pathlib import Path

import httpx
import pytest

from checkin import run_bearer_check_in
from utils.config import AppConfig, _account_env_names, load_accounts_config


@pytest.mark.parametrize('claimed', [True, False])
def test_mtnapi_account_checkin(monkeypatch, claimed):
	for name in _account_env_names():
		monkeypatch.delenv(name, raising=False)
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	monkeypatch.delenv('CHECKIN_DISABLED_PROVIDERS', raising=False)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_130',
		json.dumps(
			[
				{
					'name': 'MTNAPI-1125',
					'provider': 'mtnapi',
					'access_token': 'test-access',
					'refresh_token': 'test-refresh',
				}
			]
		),
	)
	(account,) = load_accounts_config()
	assert account.name == 'MTNAPI-1125'
	assert account.refresh_token == 'test-refresh'
	provider = AppConfig.load_from_env().providers[account.provider]
	assert provider.domain == 'https://mtnapi.com'
	assert provider.auth_refresh_path == '/api/v1/auth/refresh'
	monkeypatch.setattr('checkin.create_xiaobai_token_state', lambda *args: None)
	requests = []
	confirmed = claimed

	def handler(request):
		nonlocal confirmed
		requests.append(request)
		assert request.headers['Authorization'] == 'Bearer test-access'
		if request.url.path == '/api/v1/auth/me':
			return httpx.Response(200, json={'code': 0, 'data': {'balance': 1.25}})
		assert request.url.path == '/api/v1/redeem/checkin'
		if request.method == 'POST':
			confirmed = True
		return httpx.Response(200, json={'code': 0, 'data': {'claimed': confirmed, 'amount': 0.26}})

	client = httpx.Client
	monkeypatch.setattr(
		'checkin.httpx.Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)
	success, before, after = run_bearer_check_in(account, account.name, provider)
	assert success is True
	assert before['quota'] == after['quota'] == 1.25
	assert sum(request.method == 'POST' for request in requests) == int(not claimed)
	workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/checkin.yml').read_text()
	regular_job = workflow.split('\n  checkin:\n', 1)[1].split('\n  gorouter-status:', 1)[0]
	assert 'EXTRA_ACCOUNTS_130: ${{ secrets.EXTRA_ACCOUNTS_130 }}' in regular_job
