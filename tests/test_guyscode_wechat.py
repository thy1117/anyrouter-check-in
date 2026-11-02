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


def test_wechat_secrets_only_load_in_regular_checkin_job():
	root = Path(__file__).resolve().parents[1]
	workflow = (root / '.github/workflows/checkin.yml').read_text()
	regular_job = workflow.split('\n  checkin:\n', 1)[1].split('\n  gorouter-status:', 1)[0]
	readme = (root / 'README.md').read_text()
	for name, slot in (('GuysCode-wechat', 127), ('GuysCode-wechat1', 128)):
		mapping = f'EXTRA_ACCOUNTS_{slot}: ${{{{ secrets.EXTRA_ACCOUNTS_{slot} }}}}'
		assert mapping in regular_job
		assert workflow.count(f'EXTRA_ACCOUNTS_{slot}:') == 1
		assert f'`{name}` 保存在仓库级 Secret `EXTRA_ACCOUNTS_{slot}`' in readme
