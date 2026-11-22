import asyncio
import json
from pathlib import Path

import httpx
import pytest

from checkin import check_in_account, run_bearer_check_in
from utils.config import AccountConfig, AppConfig, _account_env_names, load_accounts_config


@pytest.mark.parametrize(('slot', 'name'), [(130, 'MTNAPI-1125'), (131, 'MTNAPI-5237'), (149, 'MTNAPI-8746')])
@pytest.mark.parametrize('claimed', [True, False])
def test_mtnapi_account_checkin(monkeypatch, claimed, slot, name):
	for env_name in _account_env_names():
		monkeypatch.delenv(env_name, raising=False)
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	monkeypatch.delenv('CHECKIN_DISABLED_PROVIDERS', raising=False)
	monkeypatch.setenv(
		f'EXTRA_ACCOUNTS_{slot}',
		json.dumps(
			[
				{
					'name': name,
					'provider': 'mtnapi',
					'http_only': slot == 149,
					'access_token': 'test-access',
					'refresh_token': 'test-refresh',
				}
			]
		),
	)
	(account,) = load_accounts_config()
	assert account.name == name
	assert account.api_user is None
	assert account.http_only == (slot == 149)
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
	workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/checkin.yml').read_text(encoding='utf-8')
	regular_job = workflow.split('\n  checkin:\n', 1)[1].split('\n  gorouter-status:', 1)[0]
	binding = 'EXTRA_ACCOUNTS_%d: ${{ secrets.EXTRA_ACCOUNTS_%d }}' % (slot, slot)
	assert regular_job.count(binding) == workflow.count(binding) == 1


@pytest.fixture(scope='module')
def offline_runner():
	with asyncio.Runner() as runner:
		yield runner


@pytest.mark.parametrize('phase', ['profile', 'status', 'submit'])
@pytest.mark.parametrize(
	('status', 'headers', 'body'),
	[
		(200, {'Content-Type': 'text/html'}, '<html>Verify</html>'),
		(401, {'Content-Type': 'application/json'}, '<html>Verify</html>'),
		(403, {'Content-Type': 'application/json'}, '{"code":403}'),
		(429, {'Content-Type': 'application/json'}, '{"code":429}'),
		(302, {'Location': 'https://example.invalid/verify'}, ''),
		(200, {'Content-Type': 'application/json', 'cf-mitigated': 'challenge'}, '{"code":0}'),
	],
)
def test_mtnapi_http_only_stops_on_challenge(monkeypatch, offline_runner, phase, status, headers, body):
	for env_name in ('PROVIDERS', 'EXTRA_PROVIDERS', 'CHECKIN_DISABLED_PROVIDERS'):
		monkeypatch.delenv(env_name, raising=False)
	account = AccountConfig(
		name='MTNAPI-8746',
		cookies=None,
		provider='mtnapi',
		access_token='test-access',
		refresh_token='test-refresh',
		http_only=True,
	)
	monkeypatch.setattr('checkin.create_xiaobai_token_state', lambda *args: None)
	for forbidden in ('SafeLineAuth', 'resolve_account_proxy_node', 'get_proxy_server'):
		monkeypatch.setattr(f'checkin.{forbidden}', lambda *args, **kwargs: pytest.fail('HTTP-only scope violated'))
	steps = [('GET', '/api/v1/auth/me'), ('GET', '/api/v1/redeem/checkin'), ('POST', '/api/v1/redeem/checkin')]
	expected = steps[: {'profile': 1, 'status': 2, 'submit': 3}[phase]]
	requests = []

	def handler(request):
		requests.append((request.method, request.url.path))
		assert request.url.host == 'mtnapi.com'
		if len(requests) >= len(expected):
			return httpx.Response(status, headers=headers, text=body)
		data = {'balance': 1.25} if len(requests) == 1 else {'claimed': False}
		return httpx.Response(200, json={'code': 0, 'data': data})

	client = httpx.Client
	monkeypatch.setattr(
		'checkin.httpx.Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)
	success, _, _ = offline_runner.run(check_in_account(account, 0, AppConfig.load_from_env()))
	assert not success
	assert requests == expected
