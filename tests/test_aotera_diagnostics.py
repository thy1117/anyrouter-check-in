from unittest.mock import AsyncMock

import pytest
import yaml

from scripts import check_gorouter
from utils.config import AccountConfig


async def test_aotera_status_group_reads_only_the_five_aotera_accounts(monkeypatch):
	monkeypatch.setenv('GOROUTER_STATUS_GROUP', 'aotera')
	accounts = [
		AccountConfig(cookies=None, provider='aotera', name=f'Aotera-thy{i}', access_token='dummy', api_user=str(i))
		for i in range(1117, 1122)
	]
	accounts.append(AccountConfig(cookies=None, provider='nianhua', name='Nianhua-Dodo'))
	monkeypatch.setattr(check_gorouter, 'load_accounts_config', lambda: accounts)
	check = AsyncMock(return_value=(True, {}, {'checked_in_today': False}))
	monkeypatch.setattr(check_gorouter, 'run_gorouter_check_in_in_page', check)
	assert await check_gorouter.run() == 0
	assert [call.args[0].name for call in check.await_args_list] == [f'Aotera-thy{i}' for i in range(1117, 1122)]
	assert all(call.kwargs == {'status_only': True} for call in check.await_args_list)


async def test_unknown_status_group_fails_before_account_loading(monkeypatch):
	monkeypatch.setenv('GOROUTER_STATUS_GROUP', 'all')
	monkeypatch.setattr(check_gorouter, 'load_accounts_config', lambda: pytest.fail('must reject group first'))
	with pytest.raises(ValueError, match='status group'):
		await check_gorouter.run()


def test_aotera_status_workflow_wires_exact_group_and_slots():
	from pathlib import Path

	workflow = yaml.safe_load(Path('.github/workflows/checkin.yml').read_text())
	trigger = workflow.get('on') or workflow[True]
	group = trigger['workflow_dispatch']['inputs']['gorouter_status_group']
	assert group['default'] == 'legacy'
	assert group['options'] == ['legacy', 'aotera']
	job = workflow['jobs']['gorouter-status']
	assert job['permissions'] == {'contents': 'read'}
	step = next(step for step in job['steps'] if 'scripts.check_gorouter' in step.get('run', ''))
	assert step['env']['GOROUTER_STATUS_GROUP'] == '${{ inputs.gorouter_status_group }}'
	for slot in range(73, 78):
		assert step['env'][f'EXTRA_ACCOUNTS_{slot}'] == '${{ secrets.EXTRA_ACCOUNTS_' + str(slot) + ' }}'
	assert not any('TELEGRAM' in key for key in step['env'])
