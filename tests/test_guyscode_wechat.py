import json
import os
from pathlib import Path

from utils.config import load_accounts_config


def test_wechat_slots_preserve_existing_guyscode_account(monkeypatch):
	for name in tuple(os.environ):
		if name == 'ANYROUTER_ACCOUNTS' or name.startswith('EXTRA_ACCOUNTS'):
			monkeypatch.delenv(name)
	monkeypatch.delenv('CHECKIN_DISABLED_PROVIDERS', raising=False)
	for slot, name, token in (
		(47, 'GuysCode-5237', 'existing'),
		(127, 'GuysCode-wechat', 'wechat'),
		(128, 'GuysCode-wechat1', 'wechat1'),
	):
		monkeypatch.setenv(
			f'EXTRA_ACCOUNTS_{slot}',
			json.dumps(
				[{'name': name, 'provider': 'guyscode', 'access_token': token, 'refresh_token': f'{token}-refresh'}]
			),
		)
	accounts = load_accounts_config()
	assert accounts is not None
	assert [account.name for account in accounts] == ['GuysCode-5237', 'GuysCode-wechat', 'GuysCode-wechat1']
	assert [account.provider for account in accounts] == ['guyscode', 'guyscode', 'guyscode']
	assert [account.access_token for account in accounts] == ['existing', 'wechat', 'wechat1']
	assert [account.refresh_token for account in accounts] == ['existing-refresh', 'wechat-refresh', 'wechat1-refresh']


def test_retired_guyscode_slots_are_not_loaded_and_lunora_is_retained():
	root = Path(__file__).resolve().parents[1]
	workflow = (root / '.github/workflows/checkin.yml').read_text()
	regular_job = workflow.split('\n  checkin:\n', 1)[1].split('\n  gorouter-status:', 1)[0]
	readme = (root / 'README.md').read_text()
	for slot in (47, 48, 49, 50, 51, 127, 128):
		for source in (root / '.github/workflows').glob('*.yml'):
			assert f'EXTRA_ACCOUNTS_{slot}:' not in source.read_text()
	assert 'EXTRA_ACCOUNTS_87: ${{ secrets.EXTRA_ACCOUNTS_87 }}' in regular_job
	assert 'Lunora 全部账号继续保留，包含 `Lunora-wechat1`' in readme
