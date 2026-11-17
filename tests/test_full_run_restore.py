from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import checkin
from utils.config import AccountConfig
from utils.daily_success import DailySuccessState

yaml = import_module('yaml')

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('legacy_input', [None, False, True])
def test_full_dispatch_uses_normal_entrypoint_with_legacy_muse_input(legacy_input):
	import os
	import shutil
	import subprocess
	import tempfile

	workflow = yaml.safe_load((ROOT / '.github/workflows/checkin.yml').read_text())
	inputs = workflow.get('on', workflow.get(True))['workflow_dispatch']['inputs']
	assert inputs['manual_no_challenges']['type'] == 'boolean'
	assert inputs['manual_no_challenges']['default'] is False
	step = next(step for step in workflow['jobs']['checkin']['steps'] if step.get('name') == '执行签到')
	bash = shutil.which('bash')
	if bash is None and os.name == 'nt':
		git = shutil.which('git')
		if git:
			candidate = Path(git).resolve().parents[1] / 'bin' / 'bash.exe'
			if candidate.is_file():
				bash = str(candidate)
	if bash is None:
		pytest.skip('Bash is needed to execute the production workflow step')

	with tempfile.TemporaryDirectory(prefix='checkin-normal-') as directory:
		root = Path(directory)
		(root / 'xvfb-run').write_text(
			'#!/bin/sh\nif [ "$1" = "-a" ]; then shift; fi\ncase "$1" in --server-args=*) shift ;; esac\nexec "$@"\n',
			encoding='utf-8',
		)
		(root / 'uv').write_text('#!/bin/sh\nprintf "%s\\n" "$*" > observed.txt\n', encoding='utf-8')
		(root / 'xvfb-run').chmod(0o755)
		(root / 'uv').chmod(0o755)
		(root / 'workflow-step.sh').write_text(step['run'], encoding='utf-8')
		env = os.environ.copy()
		env.pop('CHECKIN_MANUAL_NO_CHALLENGES', None)
		if legacy_input is not None:
			env['CHECKIN_MANUAL_NO_CHALLENGES'] = str(legacy_input).lower()
		result = subprocess.run(
			[bash, '-c', 'PATH="$PWD:$PATH"; export PATH; bash workflow-step.sh'],
			cwd=root,
			env=env,
			capture_output=True,
			text=True,
			timeout=10,
		)
		assert result.returncode == 0, result.stderr
		assert (root / 'observed.txt').read_text(encoding='utf-8').strip() == 'run checkin.py'


@pytest.mark.asyncio
async def test_production_full_runs_ignore_prior_success_and_report_every_round(monkeypatch, tmp_path):
	workflow = yaml.safe_load((ROOT / '.github/workflows/checkin.yml').read_text())
	production_env = workflow['jobs']['checkin']['env']
	accounts = [AccountConfig(cookies=None, provider='test', name=f'test-{i}') for i in range(2)]
	state_file = tmp_path / 'previous-daily-success.json'
	old_state = DailySuccessState('2026-11-17', str(state_file))
	for index, account in enumerate(accounts):
		old_state.mark_success(account, index)
	old_state.close()
	before_state = state_file.read_bytes()
	for key, value in production_env.items():
		if isinstance(value, str) and not value.startswith('${{'):
			monkeypatch.setenv(key, value)
	monkeypatch.setenv('CHECKIN_TARGET_ACCOUNTS', '')
	monkeypatch.setattr(checkin, 'format_check_in_time', lambda: '2026-11-17 17:58:00')
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: SimpleNamespace(providers={}))
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: accounts)
	monkeypatch.setattr(checkin, 'load_balance_hash', lambda: None)
	monkeypatch.setattr(checkin, 'save_balance_hash', Mock())
	monkeypatch.setattr(checkin, 'is_debug_enabled', lambda: False)
	real_factory = checkin.create_daily_success_state

	def factory(day):
		if production_env.get('CHECKIN_DAILY_STATE_FILE'):
			return DailySuccessState(day, str(state_file))
		return real_factory(day)

	monkeypatch.setattr(checkin, 'create_daily_success_state', factory)
	before = {'success': True, 'quota': 10.0, 'used_quota': 0.0}
	after = {'success': True, 'quota': 10.25, 'used_quota': 0.0}
	runner = AsyncMock(return_value=(True, before, after))
	monkeypatch.setattr(checkin, 'check_in_account', runner)
	sender = Mock(return_value={'Telegram': True})
	monkeypatch.setattr(checkin.notify, 'push_message', sender)
	for round_number in range(3):
		with pytest.raises(SystemExit) as outcome:
			await checkin.main()
		assert outcome.value.code == 0
		assert runner.await_count == (round_number + 1) * 2
		assert sender.call_count == round_number + 1
		assert '签到结果：2/2 成功' in sender.call_args.args[1]
		assert '今日此前已成功' not in sender.call_args.args[1]
	assert state_file.read_bytes() == before_state


@pytest.mark.asyncio
@pytest.mark.parametrize('balance', [10, None])
async def test_original_api_confirmation_counts_without_new_balance_credit(monkeypatch, balance):
	monkeypatch.setenv('CHECKIN_TARGET_ACCOUNTS', '')
	monkeypatch.setenv('CHECKIN_DAILY_STATE_FILE', '')
	monkeypatch.setenv('NOTIFY_EVERY_RUN', 'true')
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: SimpleNamespace(providers={}))
	monkeypatch.setattr(
		checkin, 'load_accounts_config', lambda: [AccountConfig(cookies=None, provider='test', name='confirmed')]
	)
	monkeypatch.setattr(checkin, 'load_balance_hash', lambda: None)
	monkeypatch.setattr(checkin, 'save_balance_hash', Mock())
	monkeypatch.setattr(checkin, 'is_debug_enabled', lambda: False)
	info = {'success': True, 'quota': balance, 'used_quota': 0.0}
	monkeypatch.setattr(checkin, 'check_in_account', AsyncMock(return_value=(True, info, info)))
	sender = Mock(return_value={'Telegram': True})
	monkeypatch.setattr(checkin.notify, 'push_message', sender)
	with pytest.raises(SystemExit) as outcome:
		await checkin.main()
	assert outcome.value.code == 0
	sender.assert_called_once()
	message = sender.call_args.args[1]
	assert '签到结果：1/1 成功' in message
	assert '未确认到账' not in message
	assert '签到 +$' not in message
