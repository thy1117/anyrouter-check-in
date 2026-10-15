from pathlib import Path

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
	assert 'EXTRA_ACCOUNTS_9' in target['env']
	assert 'EXTRA_ACCOUNTS_73' in target['env']
	assert 'EXTRA_ACCOUNTS_77' in target['env']
