import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, call

import httpx
import pytest
import yaml

from utils.config import AccountConfig, AppConfig

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/checkin.yml'
NEXA_SLOTS = {f'EXTRA_ACCOUNTS_{number}' for number in (64, 65, 83, 84, 85, *range(88, 98), 118, 119)}


def test_workflow_restores_balance_dispatch_without_changing_production():
	config = yaml.safe_load(WORKFLOW.read_text())
	trigger = config.get('on') or config.get(True)
	inputs = trigger['workflow_dispatch']['inputs']
	assert 'nexa_balance_only' in inputs, 'The still-referenced balance-only dispatch input is missing'
	assert inputs['nexa_balance_only']['type'] == 'boolean'
	assert inputs['nexa_balance_only']['default'] is False
	assert inputs['nexa_balance_request_id']['type'] == 'string'
	assert inputs['nexa_balance_request_id']['default'] == ''
	assert set(trigger) == {'workflow_dispatch'}
	assert config['run-name'] == (
		"${{ inputs.nexa_balance_request_id && format('Nexa balance {0}', inputs.nexa_balance_request_id) || github.workflow }}"
	)
	assert config['concurrency'] == {
		'group': 'checkin-session-state-${{ github.repository }}',
		'cancel-in-progress': False,
	}
	jobs = config['jobs']
	job = jobs['nexa-balances']
	for name, other in jobs.items():
		if name == 'nexa-balances':
			continue
		assert ('!(inputs.nexa_balance_only ||' if name == 'checkin' else '!inputs.nexa_balance_only &&') in other['if']
	assert job['if'] == "${{ github.event_name == 'workflow_dispatch' && inputs.nexa_balance_only }}"
	assert job['permissions'] == {'contents': 'write'}
	assert job['environment'] == 'production'
	step = next(step for step in job['steps'] if 'scripts.read_nexa_balances' in step.get('run', ''))
	assert {key for key in step['env'] if key.startswith('EXTRA_ACCOUNTS')} == NEXA_SLOTS
	assert step['env']['NEXA_BALANCE_REQUEST_ID'] == '${{ inputs.nexa_balance_request_id }}'
	assert step['env']['XIAOBAI_STATE_GITHUB_TOKEN'] == '${{ github.token }}'
	assert step['env']['XIAOBAI_TOKEN_STATE_KEY'] == '${{ secrets.XIAOBAI_TOKEN_STATE_KEY }}'
	assert set(step['env']) == NEXA_SLOTS | {
		'PYTHONUNBUFFERED',
		'NEXA_BALANCE_REQUEST_ID',
		'XIAOBAI_TOKEN_STATE_KEY',
		'XIAOBAI_STATE_GITHUB_TOKEN',
		'PROVIDERS',
		'EXTRA_PROVIDERS',
	}
	commands = '\n'.join(step.get('run', '') for step in job['steps'])
	assert 'checkin.py' not in commands
	assert not any(word in commands for word in ('cloakbrowser install', 'mihomo', 'xvfb-run'))
	artifacts = {
		step.get('with', {}).get('name'): step
		for step in job['steps']
		if step.get('uses') == 'actions/upload-artifact@v4'
	}
	balance = artifacts['nexa-live-balances-${{ github.run_id }}-${{ github.run_attempt }}']
	assert balance['with']['path'] == 'nexa-balances.json'
	assert balance['if'] == 'always()'
	assert any(step['with']['path'] == '.xiaobai-token-recovery/*.fernet' for step in artifacts.values())
	production = jobs['checkin']
	assert production['env']['CHECKIN_DISABLED_PROVIDERS'] == 'qingjiu,vsllm,nexavlinks,thousandyuan'
	assert production['env']['NOTIFY_EVERY_RUN'] is True
	assert production['env']['CHECKIN_TARGET_ACCOUNTS'] == '${{ inputs.target_accounts }}'
	checkin = next(step for step in production['steps'] if 'uv run checkin.py' in step.get('run', ''))
	for number in (125, 126, 127, 128, 129, 130):
		assert checkin['env'][f'EXTRA_ACCOUNTS_{number}'] == '${{ secrets.EXTRA_ACCOUNTS_' + str(number) + ' }}'


@pytest.mark.parametrize('expired', [False, True])
def test_balance_reads_saved_profile_with_at_most_one_persisted_refresh(monkeypatch, capsys, expired):
	assert importlib.util.find_spec('scripts.read_nexa_balances') is not None, 'Balance-only reader is missing'
	from scripts import read_nexa_balances

	provider = AppConfig.load_from_env().providers['nexavlinks']
	account = AccountConfig(cookies=None, name='NexaVlinks-1125', provider='nexavlinks', access_token='seed-access')
	state = MagicMock()
	state.load.return_value = ('saved-access', 'saved-refresh')
	factory = MagicMock(return_value=state)
	requests = []

	def handler(request):
		requests.append(request)
		assert request.url.host == 'asia.nexavlinks.com'
		if request.method == 'POST':
			assert request.url.path == '/api/v1/auth/refresh'
			assert json.loads(request.content) == {'refresh_token': 'saved-refresh'}
			assert 'Authorization' not in request.headers
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

	monkeypatch.setattr(read_nexa_balances, 'create_xiaobai_token_state', factory)
	client = httpx.Client
	monkeypatch.setattr(
		read_nexa_balances.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)
	assert read_nexa_balances.read_balance(account, provider) == Decimal('0.15000123')
	factory.assert_called_once_with(account, 'https://asia.nexavlinks.com')
	assert [(request.method, request.url.path) for request in requests] == (
		[('GET', '/api/v1/auth/me'), ('POST', '/api/v1/auth/refresh'), ('GET', '/api/v1/auth/me')]
		if expired
		else [('GET', '/api/v1/auth/me')]
	)
	assert state.save.call_args_list == (
		[call('saved-access', 'saved-refresh', pending=True), call('new-access', 'new-refresh')] if expired else []
	)
	state.close.assert_called_once()
	assert 'never-print' not in capsys.readouterr().out


@pytest.mark.parametrize('failed', [False, True])
def test_runner_exports_only_fresh_balances_for_all_feature_accounts(monkeypatch, tmp_path, capsys, failed):
	from scripts import read_nexa_balances

	assert hasattr(read_nexa_balances, 'run'), 'Balance-only entrypoint is missing'
	monkeypatch.chdir(tmp_path)
	Path('nexa-balances.json').write_text('{"stale":true}')
	assert read_nexa_balances.EXPECTED_ACCOUNTS == {
		'9308',
		'1125',
		'5237',
		'8746',
		'3069',
		'tthx',
		'12',
		'13',
		'15',
		'24',
		'26',
		'27',
		'28',
		'37',
		'38',
		'31',
		'tthx1',
		'hotmail',
		'wechat',
		'wechat1',
	}
	accounts = [
		AccountConfig(cookies=None, name='NexaVlinks-' + name, provider='nexavlinks', access_token='test-access')
		for name in sorted(read_nexa_balances.EXPECTED_ACCOUNTS)
	]
	monkeypatch.setattr(read_nexa_balances, 'load_accounts_config', lambda: accounts)
	monkeypatch.setenv('GITHUB_RUN_ID', '123')
	monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '2')
	monkeypatch.setenv('NEXA_BALANCE_REQUEST_ID', 'request-123')
	reads = []

	def read(account, provider):
		reads.append(account)
		if failed and account is accounts[0]:
			raise ValueError('Balance read failed: HTTP 401')
		return Decimal('0.15000123')

	monkeypatch.setattr(read_nexa_balances, 'read_balance', read)
	assert read_nexa_balances.run() == int(failed)
	assert reads == accounts
	payload = json.loads(Path('nexa-balances.json').read_text())
	assert payload['version'] == 2
	assert payload['mode'] == 'read-only-balance'
	assert payload['run_id'] == '123' and payload['run_attempt'] == '2'
	assert payload['request_id'] == 'request-123'
	assert 'stale' not in payload and 'observed_at' in payload
	assert len(payload['balances']) == len(accounts) - int(failed)
	assert set(payload['balances'].values()) == {'0.15000123'}
	if failed:
		assert accounts[0].name not in payload['balances']
		assert 'HTTP 401' in capsys.readouterr().out


def test_account_selection_requires_feature_identities_and_rejects_duplicate_aliases():
	from scripts import read_nexa_balances

	assert hasattr(read_nexa_balances, 'select_accounts'), 'Balance-only account selection is missing'
	accounts = [
		AccountConfig(cookies=None, name='NexaVlinks-' + name, provider='nexavlinks', access_token='test-access')
		for name in sorted(read_nexa_balances.EXPECTED_ACCOUNTS - {'tthx', '12'})
	]
	accounts += [
		AccountConfig(cookies=None, name='NexaVlinks-' + name, provider='nexavlinks', access_token='test-access')
		for name in ('tthxyc', 'g12', 'extra')
	]
	assert read_nexa_balances.select_accounts(accounts + [AccountConfig(cookies=None, provider='sidrune')]) == accounts
	with pytest.raises(ValueError, match='Missing'):
		read_nexa_balances.select_accounts(accounts[:1])
	with pytest.raises(ValueError, match='unique'):
		read_nexa_balances.select_accounts(accounts + accounts[:1])
	with pytest.raises(ValueError, match='duplicate identities'):
		read_nexa_balances.select_accounts(
			accounts + [AccountConfig(cookies=None, name='NexaVlinks-12', provider='nexavlinks')]
		)
