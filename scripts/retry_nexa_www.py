"""Read-only status checks for the nine Nexa accounts in the alert."""

import json

from scripts.check_nexa_status import read_status
from utils.config import AccountConfig, AppConfig, load_accounts_config

ACCOUNTS = (
	'NexaVlinks-1125',
	'NexaVlinks-5237',
	'NexaVlinks-3069',
	'NexaVlinks-9308',
	'NexaVlinks-hotmail',
	'NexaVlinks-31',
	'NexaVlinks-24',
	'NexaVlinks-37',
	'NexaVlinks-38',
)


def select_accounts(accounts: list[AccountConfig]) -> list[AccountConfig]:
	selected = []
	for name in ACCOUNTS:
		matches = [account for account in accounts if account.name == name]
		if len(matches) != 1 or matches[0].provider != 'nexavlinks':
			raise ValueError(f'Expected exactly one Nexa account named {name}; nothing was run')
		if not matches[0].access_token:
			raise ValueError(f'{name} requires an existing access token; no refresh or login permitted')
		selected.append(matches[0])
	return selected


def run() -> int:
	accounts = select_accounts(load_accounts_config() or [])
	provider = AppConfig.load_from_env().providers['nexavlinks']
	results = [read_status(account, provider) for account in accounts]
	with open('nexa-www-results.json', 'w', encoding='utf-8') as output:
		json.dump(results, output, ensure_ascii=False, indent=2)
		output.write('\n')
	for result in results:
		print('NEXA_STATUS ' + json.dumps(result, ensure_ascii=False), flush=True)
	failures = sum(not result['status_read_ok'] for result in results)
	print(f'[STATUS] Read {len(results)} exact Nexa statuses; no refresh, challenge or check-in POST. Errors: {failures}')
	return int(bool(failures))


if __name__ == '__main__':
	raise SystemExit(run())
