"""Temporary two-account validation, no notifications or shared state changes."""

import asyncio
import contextlib
import io

from checkin import run_twinkle_check_in
from utils.config import AppConfig, load_accounts_config
from utils.proxy import active_proxy_node


async def main():
	with contextlib.redirect_stdout(io.StringIO()):
		config = AppConfig.load_from_env()
		accounts = load_accounts_config() or []
	names = {'Twinkle-Dodo', 'Twinkle-112581647'}
	selected = [a for a in accounts if a.provider == 'twinkle' and a.name in names]
	assert len(selected) == 2 and {a.name for a in selected} == names
	provider = config.providers['twinkle']
	assert provider.domain == 'https://big-model.smart-agi.com'
	failures = 0
	for account in selected:
		node = 'Fast-B1-1' if account.name == 'Twinkle-Dodo' else 'Fast-B1-2'
		with active_proxy_node(node, account_name=account.name):
			success, _, after = await run_twinkle_check_in(account, account.name, provider)
		print('RESULT', account.name, 'CONFIRMED' if success else (after or {}).get('check_in_error', 'failed'))
		failures += not success
	raise SystemExit(1 if failures else 0)


if __name__ == '__main__':
	asyncio.run(main())
