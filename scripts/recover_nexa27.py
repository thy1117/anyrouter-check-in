"""Explicit authentication recovery for account 27, without check-in writes."""

import json

from checkin import run_xiaobai_check_in
from scripts.check_nexa_status import read_status
from utils.config import AccountConfig, AppConfig, load_accounts_config


def select_account(accounts: list[AccountConfig]) -> AccountConfig:
	matches = [account for account in accounts if account.name == 'NexaVlinks-27']
	if len(matches) != 1 or matches[0].provider != 'nexavlinks':
		raise ValueError('Expected exactly NexaVlinks-27; nothing was run')
	return matches[0]


def recover(account, provider):
	if account.name != 'NexaVlinks-27' or account.provider != 'nexavlinks':
		raise ValueError('Recovery is restricted to NexaVlinks-27')
	if provider.name != 'nexavlinks' or provider.domain != 'https://asia.nexavlinks.com':
		raise ValueError('Unexpected provider identity; nothing was run')
	# Reuse the existing compare-and-swap encrypted refresh path. Its explicit
	# status-only return occurs before any challenge or check-in submission.
	success, _, info = run_xiaobai_check_in(account, account.name, provider, status_only=True)
	if not success:
		return {
			'account': account.name,
			'status_read_ok': False,
			'authentication_recovered': False,
			'error': (info or {}).get('check_in_error', 'Authentication recovery failed'),
		}
	result = read_status(account, provider)
	result['authentication_recovered'] = bool(result['status_read_ok'])
	return result


def run() -> int:
	account = select_account(load_accounts_config() or [])
	provider = AppConfig.load_from_env().providers['nexavlinks']
	print(
		'[RECOVERY] Exactly NexaVlinks-27; durable refresh if necessary, then status GET only. No check-in or challenge.'
	)
	result = recover(account, provider)
	print('NEXA_AUTH_RECOVERY ' + json.dumps(result, ensure_ascii=False), flush=True)
	return int(not result['status_read_ok'])


if __name__ == '__main__':
	raise SystemExit(run())
