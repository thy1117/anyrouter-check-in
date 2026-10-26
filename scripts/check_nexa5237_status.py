"""GET-only status verification for NexaVlinks-5237."""

import json

from scripts.check_nexa_status import read_status
from utils.config import AccountConfig, AppConfig, load_accounts_config

ACCOUNT = 'NexaVlinks-5237'


def select_account(accounts: list[AccountConfig]) -> AccountConfig:
	matches = [account for account in accounts if account.name == ACCOUNT]
	if len(matches) != 1 or matches[0].provider != 'nexavlinks':
		raise ValueError(f'Expected exactly one Nexa account named {ACCOUNT}; nothing was run')
	if not matches[0].access_token:
		raise ValueError(f'{ACCOUNT} requires an existing access token; no refresh or login permitted')
	return matches[0]


def run() -> int:
	account = select_account(load_accounts_config() or [])
	provider = AppConfig.load_from_env().providers['nexavlinks']
	result = read_status(account, provider)
	with open('nexa5237-status.json', 'w', encoding='utf-8') as output:
		json.dump(result, output, ensure_ascii=False, indent=2)
		output.write('\n')
	print('NEXA_STATUS ' + json.dumps(result, ensure_ascii=False), flush=True)
	return int(not result['status_read_ok'])


if __name__ == '__main__':
	raise SystemExit(run())
