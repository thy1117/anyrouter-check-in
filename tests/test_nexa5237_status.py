"""A one-account diagnostic must only GET current Nexa identity/status."""

import httpx
import pytest

from utils.config import AccountConfig, AppConfig


def test_select_exact_nexa_5237_only():
	from scripts.check_nexa5237_status import select_account

	account = AccountConfig(cookies=None, name='NexaVlinks-5237', provider='nexavlinks', access_token='test')
	other = AccountConfig(cookies=None, name='NexaVlinks-38', provider='nexavlinks', access_token='test')
	assert select_account([account, other]) is account
	for rows in (
		[other],
		[account, account],
		[AccountConfig(cookies=None, name=account.name, provider='wrong', access_token='test')],
	):
		with pytest.raises(ValueError):
			select_account(rows)


def test_status_reads_only_www_and_never_refreshes_or_submits(monkeypatch):
	from scripts.check_nexa5237_status import select_account
	from scripts.check_nexa_status import read_status

	account = select_account(
		[AccountConfig(cookies=None, name='NexaVlinks-5237', provider='nexavlinks', access_token='seed')]
	)
	provider = AppConfig.load_from_env().providers['nexavlinks']
	state = type('State', (), {'load': lambda self: ('saved', 'refresh'), 'close': lambda self: None})()
	monkeypatch.setattr('scripts.check_nexa_status.create_xiaobai_token_state', lambda *args: state)
	requests = []

	def handler(request):
		requests.append(request)
		assert request.method == 'GET'
		if request.url.path == '/api/v1/auth/me':
			return httpx.Response(200, json={'code': 0, 'data': {'id': 6475, 'balance': 0}})
		return httpx.Response(200, json={'code': 0, 'data': {'checked_in_today': False, 'eligible': True}})

	client = httpx.Client
	monkeypatch.setattr(
		'scripts.check_nexa_status.httpx.Client',
		lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs),
	)
	result = read_status(account, provider)
	assert result['user_id'] == 6475 and result['checked_in_today'] is False and result['eligible'] is True
	assert {request.url.host for request in requests} == {'www.nexavlinks.com'}
	assert all(request.method == 'GET' for request in requests)


def test_existing_workflow_dispatch_cannot_run_regular_job_for_status_only():
	from pathlib import Path

	import yaml

	workflow = yaml.safe_load(Path('.github/workflows/checkin.yml').read_text())
	job = workflow['jobs']['nexa5237-status-only']
	assert job['permissions'] == {'contents': 'read'}
	assert workflow['jobs']['checkin']['if'].find("inputs.target_accounts == 'NexaVlinks-5237'") >= 0
	step = next(s for s in job['steps'] if 'scripts.check_nexa5237_status' in s.get('run', ''))
	assert set(k for k in step['env'] if k.startswith('EXTRA_ACCOUNTS_')) == {'EXTRA_ACCOUNTS_64', 'EXTRA_ACCOUNTS_65'}
	assert not any('TELEGRAM' in k or 'PROXY_SUBSCRIPTION' in k for k in step['env'])
