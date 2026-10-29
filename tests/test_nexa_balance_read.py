import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import yaml

from scripts import read_nexa_balances
from utils.config import AccountConfig, AppConfig
from utils.nexa_balance_snapshot import parse_balance
from utils.xiaobai_token_state import TokenStateError


def account(name='NexaVlinks-test'):
	return AccountConfig(cookies=None, name=name, provider='nexavlinks', access_token='test-access')


@pytest.mark.parametrize('expired', [False, True])
def test_balance_uses_saved_auth_and_never_requests_checkin(monkeypatch, expired):
	provider = AppConfig.load_from_env().providers['nexavlinks']
	requests = []
	state = MagicMock()
	state.load.return_value = ('saved-access', 'saved-refresh')
	factory = MagicMock(return_value=state)

	def handler(request):
		requests.append(request)
		assert request.url.host == 'www.nexavlinks.com'
		if request.method == 'POST':
			assert request.url.path == '/api/v1/auth/refresh'
			assert json.loads(request.content) == {'refresh_token': 'saved-refresh'}
			state.save.assert_called_once_with('saved-access', 'saved-refresh', pending=True)
			return httpx.Response(
				200, json={'code': 0, 'data': {'access_token': 'new-access', 'refresh_token': 'new-refresh'}}
			)
		assert request.method == 'GET' and request.url.path == '/api/v1/auth/me'
		if expired and len(requests) == 1:
			return httpx.Response(401)
		if expired:
			state.save.assert_called_with('new-access', 'new-refresh')
		assert request.headers['Authorization'] == f'Bearer {"new-access" if expired else "saved-access"}'
		return httpx.Response(200, json={'code': 0, 'data': {'balance': '0.15000123', 'private': 'never-print'}})

	monkeypatch.setattr('checkin.create_xiaobai_token_state', factory)
	client = httpx.Client
	monkeypatch.setattr(
		'checkin.httpx.Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)

	a = account()
	assert read_nexa_balances.read_balance(a, provider) == Decimal('0.15000123')
	factory.assert_called_once_with(a, 'https://asia.nexavlinks.com')
	assert len(requests) == (3 if expired else 1)
	if not expired:
		state.save.assert_not_called()
	state.close.assert_called_once()


@pytest.mark.parametrize('mode', ['pending', 'save_failed', 'refresh_timeout', 'rejected'])
def test_authentication_failure_never_replays_or_checks_in(monkeypatch, mode):
	state = MagicMock()
	state.load.return_value = ('saved-access', 'saved-refresh')
	if mode == 'pending':
		state.load.side_effect = TokenStateError('Previous refresh interrupted')
	if mode == 'save_failed':
		state.save.side_effect = TokenStateError('Cannot save encrypted token state')
	requests = []

	def handler(request):
		requests.append(request)
		if request.url.path == '/api/v1/auth/me':
			return httpx.Response(401)
		assert request.url.path == '/api/v1/auth/refresh'
		if mode == 'refresh_timeout':
			raise httpx.ReadTimeout('private upstream detail')
		return httpx.Response(401, json={'error': 'private upstream detail'})

	monkeypatch.setattr('checkin.create_xiaobai_token_state', lambda *args: state)
	client = httpx.Client
	monkeypatch.setattr(
		'checkin.httpx.Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)
	with pytest.raises(ValueError) as error:
		read_nexa_balances.read_balance(account(), AppConfig.load_from_env().providers['nexavlinks'])
	assert 'private upstream detail' not in str(error.value)
	assert len(requests) == {'pending': 0, 'save_failed': 1, 'refresh_timeout': 2, 'rejected': 2}[mode]
	state.close.assert_called_once()


@pytest.mark.parametrize(
	'response',
	[
		httpx.Response(401),
		httpx.Response(200, text='not-json'),
		httpx.Response(200, json={'code': 0, 'data': {}}),
		httpx.Response(200, json={'code': 0, 'data': {'balance': 'NaN'}}),
		httpx.Response(200, json={'code': 0, 'data': {'balance': 'Infinity'}}),
		httpx.Response(200, json={'code': 1, 'data': {'balance': '1'}}),
	],
)
def test_parse_balance_rejects_untrusted_values(response):
	with pytest.raises(ValueError):
		parse_balance(response)


def test_parse_balance_preserves_negative_debt():
	assert parse_balance(httpx.Response(200, json={'code': 0, 'data': {'balance': '-0.15000123'}})) == Decimal(
		'-0.15000123'
	)


def test_select_accounts_requires_all_18_and_ignores_other_providers():
	accounts = [account('NexaVlinks-' + name) for name in sorted(read_nexa_balances.EXPECTED_ACCOUNTS)]
	selected = read_nexa_balances.select_accounts(
		accounts + [AccountConfig(cookies=None, name='Other', provider='sidrune')]
	)
	assert selected == accounts
	with pytest.raises(ValueError, match='unique'):
		read_nexa_balances.select_accounts(accounts + accounts[:1])
	with pytest.raises(ValueError, match='Missing'):
		read_nexa_balances.select_accounts(accounts[:17])


def test_select_accounts_accepts_known_aliases_and_extra_configured_names():
	accounts = [account('NexaVlinks-' + name) for name in sorted(read_nexa_balances.EXPECTED_ACCOUNTS - {'tthx', '12'})]
	accounts += [account('NexaVlinks-tthxyc'), account('NexaVlinks-g12'), account('NexaVlinks-extra')]
	assert read_nexa_balances.select_accounts(accounts) == accounts


@pytest.mark.parametrize('failed', [False, True])
def test_snapshot_contains_only_fresh_successes(monkeypatch, tmp_path, capsys, failed):
	monkeypatch.chdir(tmp_path)
	Path('nexa-balances.json').write_text('{"stale": true}')
	accounts = [account(name) for name in sorted(read_nexa_balances.EXPECTED_ACCOUNTS)]
	monkeypatch.setattr(read_nexa_balances, 'load_accounts_config', lambda: accounts)
	monkeypatch.setenv('GITHUB_RUN_ID', '123')
	monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '2')
	monkeypatch.setenv('NEXA_BALANCE_REQUEST_ID', 'request-123')

	def read(a, provider):
		if failed and a.name == accounts[0].name:
			raise ValueError('Balance read failed: HTTP 401')
		return Decimal('0.15000123')

	monkeypatch.setattr(read_nexa_balances, 'read_balance', read)
	assert read_nexa_balances.run() == int(failed)
	payload = json.loads(Path('nexa-balances.json').read_text())
	assert payload['run_id'] == '123' and payload['run_attempt'] == '2'
	assert payload['request_id'] == 'request-123' and payload['version'] == 2
	assert len(payload['balances']) == 18 - int(failed)
	assert set(payload['balances'].values()) == {'0.15000123'}
	assert 'stale' not in payload and 'observed_at' in payload
	if failed:
		assert accounts[0].name not in payload['balances']
		assert 'HTTP 401' in capsys.readouterr().out


def test_balance_only_rejects_changed_paths_before_network(monkeypatch):
	provider = AppConfig.load_from_env().providers['nexavlinks']
	provider.user_info_path = '/api/v1/check-in'
	client = MagicMock()
	monkeypatch.setattr('checkin.httpx.Client', client)
	with pytest.raises(ValueError, match='nothing was run'):
		read_nexa_balances.read_balance(account(), provider)
	client.assert_not_called()


def test_workflow_isolates_balances_even_with_other_inputs():
	config = yaml.safe_load(Path('.github/workflows/checkin.yml').read_text())
	jobs = config['jobs']
	job = jobs['nexa-balance-read-only']
	assert config['concurrency']['cancel-in-progress'] is False
	for name, other in jobs.items():
		if name == 'nexa-balance-read-only':
			continue
		assert ('!(inputs.nexa_balance_only ||' if name == 'checkin' else '!inputs.nexa_balance_only &&') in other['if']
	step = next(s for s in job['steps'] if 'scripts.read_nexa_balances' in s.get('run', ''))
	assert {key for key in step['env'] if key.startswith('EXTRA_ACCOUNTS')} == {
		f'EXTRA_ACCOUNTS_{i}' for i in (64, 65, 83, 84, 85, *range(88, 98))
	}
	assert not any('TELEGRAM' in key or 'PROXY' in key for key in step['env'])
	assert all('checkin.py' not in s.get('run', '') for s in job['steps'])
	assert job['permissions'] == {'contents': 'write'}
	assert any(s.get('with', {}).get('path') == '.xiaobai-token-recovery/*.fernet' for s in job['steps'])


def test_configured_sources_prints_names_without_tokens(monkeypatch, capsys):
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_64',
		json.dumps(
			[
				{'name': 'NexaVlinks-1125', 'provider': 'nexavlinks', 'access_token': 'never-print'},
				{'name': 'Other', 'provider': 'sidrune', 'access_token': 'never-print'},
			]
		),
	)
	assert read_nexa_balances.configured_sources() == {'EXTRA_ACCOUNTS_64': ['NexaVlinks-1125']}
	assert 'never-print' not in capsys.readouterr().out
