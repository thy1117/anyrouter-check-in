"""Scoped, GET-only Nexa diagnostics must not solve challenges or rotate tokens."""

import json
from unittest.mock import MagicMock

import httpx
import pytest
import yaml

from utils.config import AccountConfig, AppConfig


def test_nexa_diagnostic_reads_only_identity_and_status(monkeypatch, capsys):
	from scripts import check_nexa_status

	account = AccountConfig(cookies=None, name='NexaVlinks-27', provider='nexavlinks', access_token='seed-access')
	state = MagicMock()
	state.load.return_value = ('saved-access', 'saved-refresh')
	monkeypatch.setattr(check_nexa_status, 'create_xiaobai_token_state', lambda *args: state)
	requests = []

	def handler(request):
		requests.append(request)
		assert request.method == 'GET'
		assert request.headers['Authorization'] == 'Bearer saved-access'
		if request.url.path == '/api/v1/auth/me':
			return httpx.Response(
				200, json={'code': 0, 'data': {'id': 27, 'balance': 3.78, 'access_token': 'never-print'}}
			)
		assert request.url.path == '/api/v1/check-in'
		return httpx.Response(
			200,
			json={'code': 0, 'data': {'checked_in_today': False, 'eligible': False, 'private_token': 'never-print'}},
		)

	client = httpx.Client
	monkeypatch.setattr(
		check_nexa_status.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)
	result = check_nexa_status.read_status(account, AppConfig.load_from_env().providers['nexavlinks'])
	assert result['status_read_ok'] is True
	assert result['checked_in_today'] is False
	assert result['eligible'] is False
	assert result['user_id'] == 27
	assert [request.url.path for request in requests] == ['/api/v1/auth/me', '/api/v1/check-in']
	state.save.assert_not_called()
	state.close.assert_called_once()
	assert 'never-print' not in json.dumps(result) + capsys.readouterr().out


def test_nexa_diagnostic_does_not_refresh_rejected_access(monkeypatch):
	from scripts import check_nexa_status

	account = AccountConfig(
		cookies=None, name='NexaVlinks-38', provider='nexavlinks', access_token='access', refresh_token='refresh'
	)
	monkeypatch.setattr(check_nexa_status, 'create_xiaobai_token_state', lambda *args: None)
	requests = []

	def handler(request):
		requests.append(request)
		return httpx.Response(401, json={'error': 'sensitive-response'})

	client = httpx.Client
	monkeypatch.setattr(
		check_nexa_status.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)
	result = check_nexa_status.read_status(account, AppConfig.load_from_env().providers['nexavlinks'])
	assert result['status_read_ok'] is False
	assert '401' in result['error']
	assert len(requests) == 1 and requests[0].method == 'GET'
	assert 'sensitive-response' not in json.dumps(result)


def test_nexa_diagnostic_requires_exact_two_account_membership():
	from scripts import check_nexa_status

	accounts = [
		AccountConfig(cookies=None, name=f'NexaVlinks-{number}', provider='nexavlinks', access_token='access')
		for number in (27, 38)
	]
	assert [a.name for a in check_nexa_status.select_accounts(accounts)] == ['NexaVlinks-27', 'NexaVlinks-38']
	for invalid in [
		accounts[:1],
		accounts + accounts[:1],
		[accounts[0], AccountConfig(cookies=None, name='NexaVlinks-38', provider='wrong', access_token='access')],
	]:
		with pytest.raises(ValueError):
			check_nexa_status.select_accounts(invalid)


def test_nexa_diagnostic_workflow_is_opt_in_get_only():
	from pathlib import Path

	config = yaml.safe_load(Path('.github/workflows/checkin.yml').read_text())
	trigger = config.get('on') or config[True]
	assert trigger['workflow_dispatch']['inputs']['gorouter_status_group']['options'] == ['legacy', 'nexa']
	job = config['jobs']['gorouter-status']
	assert job['permissions'] == {'contents': 'read'}
	step = next(step for step in job['steps'] if 'scripts.check_nexa_status' in step.get('run', ''))
	assert 'inputs.gorouter_status_group' in step['if'] and "'nexa'" in step['if']
	assert {key for key in step['env'] if key.startswith('EXTRA_ACCOUNTS')} == {
		'EXTRA_ACCOUNTS_94',
		'EXTRA_ACCOUNTS_97',
	}
	assert not any('TELEGRAM' in key or 'PROXY_SUBSCRIPTION' in key for key in step['env'])
