"""Read NexaVlinks wallet balances without check-in or status requests."""

import json
from decimal import Decimal
from pathlib import Path

from checkin import run_bearer_check_in
from utils.config import AccountConfig, AppConfig, load_accounts_config
from utils.nexa_balance_snapshot import write_snapshot

EXPECTED_ACCOUNTS = {
	'NexaVlinks-' + name
	for name in (
		'9308',
		'1125',
		'5237',
		'8746',
		'3069',
		'tthxyc',
		'g12',
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
	)
}


def select_accounts(accounts: list[AccountConfig]) -> list[AccountConfig]:
	selected = [account for account in accounts if account.provider == 'nexavlinks']
	if not selected:
		raise ValueError('No NexaVlinks accounts configured')
	names = [account.name for account in selected]
	if any(not name for name in names) or len(set(names)) != len(names):
		raise ValueError('NexaVlinks accounts must have unique names')
	if set(names) != EXPECTED_ACCOUNTS:
		raise ValueError('Expected exactly the 18 configured NexaVlinks accounts; nothing was run')
	return selected


def read_balance(account: AccountConfig, provider) -> Decimal:
	success, _, info = run_bearer_check_in(account, account.name, provider, balance_only=True)
	if not success:
		raise ValueError((info or {}).get('check_in_error', 'Balance unavailable'))
	return Decimal(info['balance'])


def run() -> int:
	Path('nexa-balances.json').unlink(missing_ok=True)
	provider = AppConfig.load_from_env().providers['nexavlinks']
	accounts = select_accounts(load_accounts_config() or [])
	balances = []
	failures = 0
	print('[BALANCE] NexaVlinks balance only; safe authentication refresh if needed, no check-in/status/challenge.')
	for account in accounts:
		try:
			balance = read_balance(account, provider)
			balances.append((account.name, balance))
			print(json.dumps({'account': account.name, 'balance': str(balance)}, ensure_ascii=False), flush=True)
		except Exception as exc:
			failures += 1
			error = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
			print(json.dumps({'account': account.name, 'error': error}, ensure_ascii=False), flush=True)
	write_snapshot(balances)
	print(json.dumps({'read': len(balances), 'failed': failures, 'snapshot': 'nexa-balances.json'}, ensure_ascii=False))
	return int(bool(failures))


if __name__ == '__main__':
	raise SystemExit(run())
