"""Read-only diagnostics for the three Nianhua accounts; no token minting or check-in POST."""

import asyncio
import json
import os

from checkin import run_gorouter_check_in_in_page
from utils.config import AccountConfig, AppConfig, load_accounts_config

RECOVERY_ACCOUNTS = {
	'Nianhua-Dodo': 'nianhua',
	'Nianhua-thy1119': 'nianhua',
	'Nianhua-thy1121': 'nianhua',
}

STATUS_GROUPS = {
	'legacy': RECOVERY_ACCOUNTS,
}


def select_accounts(accounts: list[AccountConfig], group: str = 'legacy') -> list[AccountConfig]:
	if group not in STATUS_GROUPS:
		raise ValueError('Unknown GoRouter status group; nothing was run')
	selected = []
	for name, provider in STATUS_GROUPS[group].items():
		matches = [account for account in accounts if account.name == name]
		if len(matches) != 1 or matches[0].provider != provider:
			raise ValueError(f'Expected exactly one {provider} account named {name}; nothing was run')
		account = matches[0]
		if not account.access_token or not account.api_user:
			raise ValueError(f'{name} requires an existing PAT and user ID for a read-only check; nothing was run')
		selected.append(account)
	return selected


async def run() -> int:
	group = os.environ.get('GOROUTER_STATUS_GROUP', 'legacy')
	if group not in STATUS_GROUPS:
		raise ValueError('Unknown GoRouter status group; nothing was run')
	accounts = select_accounts(load_accounts_config() or [], group)
	config = AppConfig.load_from_env()
	failures = 0
	print(
		f'[STATUS] Exactly {len(accounts)} accounts; identity + daily status only. No refresh, challenge or check-in submission.'
	)
	for account in accounts:
		success, _, info = await run_gorouter_check_in_in_page(
			account,
			account.get_display_name(0),
			config.providers[account.provider],
			status_only=True,
		)
		info = info or {}
		result = {
			'account': account.name,
			'provider': account.provider,
			'status_read_ok': bool(success),
			'checked_in_today': info.get('checked_in_today') if success else None,
			'balance': info.get('quota'),
			'user_id': info.get('user_id'),
			'username': info.get('username'),
		}
		print('GOROUTER_STATUS ' + json.dumps(result, ensure_ascii=False), flush=True)
		failures += not success
	return 1 if failures else 0


if __name__ == '__main__':
	raise SystemExit(asyncio.run(run()))
