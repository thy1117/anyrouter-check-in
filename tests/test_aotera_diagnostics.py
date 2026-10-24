from unittest.mock import AsyncMock

import pytest
import yaml

from scripts import check_gorouter
from utils.config import AccountConfig


@pytest.mark.parametrize('group', [None, 'legacy'])
@pytest.mark.parametrize('include_retired', [False, True])
async def test_legacy_status_reads_only_three_nianhua_accounts(monkeypatch, capsys, group, include_retired):
	if group is None:
		monkeypatch.delenv('GOROUTER_STATUS_GROUP', raising=False)
	else:
		monkeypatch.setenv('GOROUTER_STATUS_GROUP', group)
	names = ['Nianhua-1117', 'Nianhua-thy1119', 'Nianhua-thy1121']
	accounts = [
		AccountConfig(cookies=None, provider='nianhua', name=name, access_token='dummy', api_user='1')
		for name in reversed(names)
	]
	if include_retired:
		accounts.extend(
			AccountConfig(cookies=None, provider='aotera', name=f'Aotera-thy{i}', access_token='dummy', api_user=str(i))
			for i in range(1117, 1122)
		)
	accounts.append(AccountConfig(cookies=None, provider='nianhua', name='Nianhua-unrelated'))
	monkeypatch.setattr(check_gorouter, 'load_accounts_config', lambda: accounts)
	check = AsyncMock(return_value=(True, {}, {'checked_in_today': False}))
	monkeypatch.setattr(check_gorouter, 'run_gorouter_check_in_in_page', check)
	assert await check_gorouter.run() == 0
	assert [call.args[0].name for call in check.await_args_list] == names
	assert all(call.kwargs == {'status_only': True} for call in check.await_args_list)
	output = capsys.readouterr().out
	assert '[STATUS] Exactly 3 accounts;' in output
	assert 'Aotera' not in output


@pytest.mark.parametrize('group', ['aotera', 'all'])
async def test_retired_or_unknown_status_group_fails_before_account_loading(monkeypatch, group):
	monkeypatch.setenv('GOROUTER_STATUS_GROUP', group)
	monkeypatch.setattr(check_gorouter, 'load_accounts_config', lambda: pytest.fail('must reject group first'))
	with pytest.raises(ValueError, match='status group'):
		await check_gorouter.run()


def test_retired_aotera_status_workflow_keeps_only_explicit_groups():
	from pathlib import Path

	workflow = yaml.safe_load(Path('.github/workflows/checkin.yml').read_text())
	trigger = workflow.get('on') or workflow[True]
	group = trigger['workflow_dispatch']['inputs']['gorouter_status_group']
	assert group['default'] == 'legacy'
	assert group['options'] == ['legacy', 'nexa']
	description = trigger['workflow_dispatch']['inputs']['gorouter_status_only']['description']
	assert 'exact account group' in description
	assert 'Aotera' not in description
	job = workflow['jobs']['gorouter-status']
	assert job['permissions'] == {'contents': 'read'}
	step = next(step for step in job['steps'] if 'scripts.check_gorouter' in step.get('run', ''))
	assert 'three' in step['name']
	assert step['env']['GOROUTER_STATUS_GROUP'] == '${{ inputs.gorouter_status_group }}'
	assert not any('TELEGRAM' in key for key in step['env'])


@pytest.mark.parametrize('slot', range(73, 78))
def test_workflows_do_not_wire_retired_aotera_slots(slot):
	from pathlib import Path

	for workflow in Path('.github/workflows').glob('*.y*ml'):
		assert f'EXTRA_ACCOUNTS_{slot}' not in workflow.read_text(), workflow.name


def test_load_accounts_aliases_nianhua_dodo_to_nianhua_1117(monkeypatch):
	import json

	from utils.config import _account_env_names, load_accounts_config

	for name in _account_env_names():
		monkeypatch.delenv(name, raising=False)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_9',
		json.dumps([{'name': 'Nianhua-Dodo', 'provider': 'nianhua', 'access_token': 'pat', 'api_user': '1'}]),
	)
	accounts = load_accounts_config()
	assert accounts is not None
	assert len(accounts) == 1
	assert accounts[0].name == 'Nianhua-1117'
