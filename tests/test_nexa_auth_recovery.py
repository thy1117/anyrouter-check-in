"""Recover only account 27 through the existing durable auth path."""

from unittest.mock import MagicMock

import httpx

from utils.config import AccountConfig, AppConfig


def test_recovery_refreshes_once_persists_and_reads_status_only(monkeypatch):
	from scripts import recover_nexa27

	account = AccountConfig(
		cookies=None,
		name='NexaVlinks-27',
		provider='nexavlinks',
		access_token='old-access',
		refresh_token='old-refresh',
	)
	state = MagicMock()
	state.load.return_value = ('old-access', 'old-refresh')
	monkeypatch.setattr('checkin.create_xiaobai_token_state', lambda *args: state)
	requests = []

	def handler(request):
		requests.append(request)
		if request.url.path == '/api/v1/auth/refresh':
			assert request.method == 'POST'
			assert state.save.call_args.kwargs == {'pending': True}
			return httpx.Response(
				200, json={'code': 0, 'data': {'access_token': 'new-access', 'refresh_token': 'new-refresh'}}
			)
		assert request.method == 'GET'
		if request.url.path == '/api/v1/auth/me':
			if request.headers['Authorization'] == 'Bearer old-access':
				return httpx.Response(401, json={'code': 'TOKEN_EXPIRED'})
			assert state.save.call_args.args == ('new-access', 'new-refresh')
			return httpx.Response(200, json={'code': 0, 'data': {'id': 27}})
		assert request.url.path == '/api/v1/check-in'
		return httpx.Response(200, json={'code': 0, 'data': {'checked_in_today': False, 'eligible': False}})

	client = httpx.Client
	monkeypatch.setattr(
		'checkin.httpx.Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)
	read = MagicMock(
		return_value={
			'account': account.name,
			'status_read_ok': True,
			'user_id': 27,
			'eligible': False,
			'checked_in_today': False,
		}
	)
	monkeypatch.setattr(recover_nexa27, 'read_status', read)
	result = recover_nexa27.recover(account, AppConfig.load_from_env().providers['nexavlinks'])
	assert result['status_read_ok'] is True and result['eligible'] is False
	assert sum(request.method == 'POST' for request in requests) == 1
	assert [request.url.path for request in requests] == [
		'/api/v1/auth/me',
		'/api/v1/auth/refresh',
		'/api/v1/auth/me',
		'/api/v1/check-in',
	]
	state.save.assert_any_call('old-access', 'old-refresh', pending=True)
	state.save.assert_any_call('new-access', 'new-refresh')
	read.assert_called_once_with(account, AppConfig.load_from_env().providers['nexavlinks'])


def test_recovery_selection_never_includes_other_accounts():
	from scripts import recover_nexa27

	accounts = [
		AccountConfig(cookies=None, name=f'NexaVlinks-{n}', provider='nexavlinks', access_token='access')
		for n in (27, 38)
	]
	assert recover_nexa27.select_account(accounts).name == 'NexaVlinks-27'


def test_recovery_workflow_is_fixed_scope_and_serialized():
	from pathlib import Path

	import yaml

	config = yaml.safe_load(Path('.github/workflows/nexa-auth-recovery.yml').read_text())
	assert config['concurrency']['group'] == 'checkin-session-state-${{ github.repository }}'
	assert config['concurrency']['cancel-in-progress'] is False
	assert set(config['jobs']) == {'nexa27-auth-recovery'}
	job = config['jobs']['nexa27-auth-recovery']
	assert job['permissions'] == {'contents': 'write'}
	assert job['environment'] == 'production'
	step = next(step for step in job['steps'] if 'scripts.recover_nexa27' in step.get('run', ''))
	assert {key for key in step['env'] if key.startswith('EXTRA_ACCOUNTS')} == {'EXTRA_ACCOUNTS_94'}
	assert not any('TELEGRAM' in key or 'PROXY_SUBSCRIPTION' in key for key in step['env'])


def test_scripts_resolve_as_one_package_for_cli_and_type_checks():
	from pathlib import Path

	import scripts

	assert scripts.__file__ is not None
	assert Path(scripts.__file__).name == '__init__.py'
