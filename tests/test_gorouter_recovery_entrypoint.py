import json

import pytest

from scripts.check_gorouter import RECOVERY_ACCOUNTS, select_accounts
from utils.config import AccountConfig


def test_recovery_selects_exact_failed_accounts_in_fixed_order():
	accounts = [
		AccountConfig(cookies=None, provider=provider, name=name, access_token='test-pat', api_user='1')
		for name, provider in reversed(list(RECOVERY_ACCOUNTS.items()))
	]
	accounts.append(AccountConfig(cookies=None, provider='sheapi', name='unrelated'))
	selected = select_accounts(accounts)
	assert [account.name for account in selected] == list(RECOVERY_ACCOUNTS)
	assert all(account.provider == RECOVERY_ACCOUNTS[account.name] for account in selected)


@pytest.mark.parametrize('scenario', ['missing', 'wrong_provider', 'duplicate', 'missing_pat'])
def test_recovery_rejects_incomplete_or_mismatched_accounts_before_requests(scenario):
	accounts = [
		AccountConfig(cookies=None, provider=provider, name=name, access_token='test-pat', api_user='1')
		for name, provider in RECOVERY_ACCOUNTS.items()
	]
	if scenario == 'missing':
		accounts.pop()
	elif scenario == 'wrong_provider':
		accounts[0].provider = 'sheapi'
	elif scenario == 'duplicate':
		accounts.append(accounts[0])
	else:
		accounts[0].access_token = None
	with pytest.raises(ValueError):
		select_accounts(accounts)


async def test_status_runner_reports_only_safe_fields_and_never_other_accounts(monkeypatch, capsys):
	from unittest.mock import AsyncMock

	import scripts.check_gorouter as entrypoint

	accounts = [
		AccountConfig(cookies=None, provider=provider, name=name, access_token='private-value', api_user='1')
		for name, provider in RECOVERY_ACCOUNTS.items()
	]
	monkeypatch.setattr(entrypoint, 'load_accounts_config', lambda: accounts)
	check = AsyncMock(
		return_value=(True, {}, {'checked_in_today': False, 'quota': 3.0, 'user_id': 1, 'username': 'thy1117'})
	)
	monkeypatch.setattr(entrypoint, 'run_gorouter_check_in_in_page', check)
	assert await entrypoint.run() == 0
	assert check.await_count == 5
	assert all(call.kwargs.get('status_only') is True for call in check.await_args_list)
	output = capsys.readouterr().out
	assert 'private-value' not in output
	results = [
		json.loads(line.removeprefix('GOROUTER_STATUS '))
		for line in output.splitlines()
		if line.startswith('GOROUTER_STATUS ')
	]
	assert len(results) == 5
	assert all(result['checked_in_today'] is False for result in results)
