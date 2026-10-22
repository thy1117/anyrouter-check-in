from itertools import product
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path('.github/workflows/checkin.yml')


def test_gorouter_status_dispatch_is_isolated_and_read_only():
	config = yaml.safe_load(WORKFLOW.read_text())
	trigger = config.get('on') or config.get(True)
	assert trigger['workflow_dispatch']['inputs']['gorouter_status_only']['default'] is False
	assert 'inputs.gorouter_status_only' in config['jobs']['checkin']['if']
	job = config['jobs']['gorouter-status']
	assert job['if'] == "${{ github.event_name == 'workflow_dispatch' && inputs.gorouter_status_only }}"
	assert job['permissions'] == {'contents': 'read'}
	steps = job['steps']
	commands = '\n'.join(step.get('run', '') for step in steps)
	assert 'uv run python -m scripts.check_gorouter' in commands
	assert 'uv run checkin.py' not in commands
	target = next(step for step in steps if 'scripts.check_gorouter' in step.get('run', ''))
	assert 'TELEGRAM_BOT_TOKEN' not in target.get('env', {})
	assert 'PROXY_SUBSCRIPTION_URL' not in target.get('env', {})
	assert {key for key in target['env'] if key.startswith('EXTRA_ACCOUNTS')} == {
		'EXTRA_ACCOUNTS_9',
		'EXTRA_ACCOUNTS_54',
		'EXTRA_ACCOUNTS_55',
		'EXTRA_ACCOUNTS_56',
	}


def _workflow_job_runs(job_if, flags):
	expression = job_if.removeprefix('${{ ').removesuffix(' }}')
	expression = expression.replace("github.event_name != 'workflow_dispatch'", 'False')
	expression = expression.replace("github.event_name == 'workflow_dispatch'", 'True')
	for name, value in flags.items():
		expression = expression.replace(f'inputs.{name}', str(value))
	expression = expression.replace('&&', ' and ').replace('||', ' or ').replace('!', ' not ')
	return bool(eval(expression, {'__builtins__': {}}, {}))


@pytest.mark.parametrize('values', product([False, True], repeat=5))
def test_gorouter_status_only_excludes_every_conflicting_dispatch_job(values):
	config = yaml.safe_load(WORKFLOW.read_text())
	flags = dict(
		zip(
			(
				'gorouter_status_only',
				'sidrune_exchange_only',
				'xiaobai_test',
				'xiaobai_config_only',
				'sheapi_proxy_only',
			),
			values,
		)
	)

	runs = {
		name: _workflow_job_runs(config['jobs'][name]['if'], flags)
		for name in ('checkin', 'gorouter-status', 'sidrune-exchange-test', 'xiaobai-test', 'sheapi-proxy-test')
	}
	assert runs == {
		'checkin': not any(flags.values()),
		'gorouter-status': flags['gorouter_status_only'],
		'sidrune-exchange-test': flags['sidrune_exchange_only'] and not flags['gorouter_status_only'],
		'xiaobai-test': (
			not flags['gorouter_status_only']
			and not flags['sidrune_exchange_only']
			and not flags['sheapi_proxy_only']
			and (flags['xiaobai_test'] or flags['xiaobai_config_only'])
		),
		'sheapi-proxy-test': (
			not flags['gorouter_status_only'] and not flags['sidrune_exchange_only'] and flags['sheapi_proxy_only']
		),
	}
