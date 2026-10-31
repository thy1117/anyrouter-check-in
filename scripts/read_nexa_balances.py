"""Read NexaVlinks wallet balances without check-in or status requests."""

import json
import os
import re
from decimal import Decimal
from pathlib import Path

from checkin import run_bearer_check_in
from utils.config import AccountConfig, AppConfig, load_accounts_config
from utils.nexa_balance_snapshot import write_snapshot

EXPECTED_ACCOUNTS = {
	'9308',
	'1125',
	'5237',
	'8746',
	'3069',
	'tthx',
	'12',
	'13',
	'15',
	'24',
	'26',
	'27',
	'28',
	'37',
	'38',
	'31',
	'tthx1',
	'hotmail',
	'wechat',
	'wechat1',
}
ALIASES = {'tthxyc': 'tthx', 'g12': '12'}


def configured_sources() -> dict[str, list[str]]:
	sources = {}
	names = ['ANYROUTER_ACCOUNTS', 'EXTRA_ACCOUNTS']
	names += sorted(
		(name for name in os.environ if re.fullmatch(r'EXTRA_ACCOUNTS_\d+', name)),
		key=lambda name: int(name.rsplit('_', 1)[1]),
	)
	for env_name in names:
		try:
			accounts = json.loads(os.getenv(env_name, '[]'))
		except (TypeError, ValueError):
			continue
		if not isinstance(accounts, list):
			continue
		matches = [
			str(item.get('name'))
			for item in accounts
			if isinstance(item, dict) and str(item.get('provider', '')).strip().lower() == 'nexavlinks'
		]
		if matches:
			sources[env_name] = matches
	return sources


def select_accounts(accounts: list[AccountConfig]) -> list[AccountConfig]:
	selected = [account for account in accounts if account.provider == 'nexavlinks']
	if not selected:
		raise ValueError('No NexaVlinks accounts configured')
	names = [account.name for account in selected]
	if any(not name for name in names) or len(set(names)) != len(names):
		raise ValueError('NexaVlinks accounts must have unique names')
	identities = [ALIASES.get(name.removeprefix('NexaVlinks-'), name.removeprefix('NexaVlinks-')) for name in names]
	if len(set(identities)) != len(identities):
		raise ValueError('NexaVlinks account aliases resolve to duplicate identities; nothing was run')
	missing = EXPECTED_ACCOUNTS - set(identities)
	if missing:
		raise ValueError(f'Missing NexaVlinks identities: {sorted(missing)}; nothing was run')
	return selected


def read_balance(account: AccountConfig, provider) -> Decimal:
	success, _, info = run_bearer_check_in(account, account.name, provider, balance_only=True)
	if not success:
		raise ValueError((info or {}).get('check_in_error', 'Balance unavailable'))
	return Decimal(info['balance'])


def run() -> int:
	Path('nexa-balances.json').unlink(missing_ok=True)
	provider = AppConfig.load_from_env().providers['nexavlinks']
	print('[BALANCE] Account sources: ' + json.dumps(configured_sources(), ensure_ascii=False), flush=True)
	accounts = select_accounts(load_accounts_config() or [])
	balances = []
	failures = 0
	print(f'[BALANCE] {len(accounts)} NexaVlinks accounts; no check-in/status/challenge.')
	for account in accounts:
		try:
			balance = read_balance(account, provider)
			balances.append((account.name, balance))
			print(json.dumps({'account': account.name, 'balance': str(balance)}, ensure_ascii=False), flush=True)
		except Exception as exc:
			failures += 1
			error = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
			print(json.dumps({'account': account.name, 'error': error}, ensure_ascii=False), flush=True)
	write_snapshot(balances, request_id=os.getenv('NEXA_BALANCE_REQUEST_ID'))
	print(json.dumps({'read': len(balances), 'failed': failures, 'snapshot': 'nexa-balances.json'}, ensure_ascii=False))
	return int(bool(failures))


if __name__ == '__main__':
	raise SystemExit(run())
