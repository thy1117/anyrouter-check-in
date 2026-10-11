"""Exchange only the six Sidrune accounts; never invoke multi-site check-in."""

import json
import os

from checkin import resolve_check_in_error, run_bearer_check_in
from utils.config import AccountConfig, AppConfig

SIDRUNE_SLOTS = tuple(f'EXTRA_ACCOUNTS_{number}' for number in range(67, 73))


def select_accounts(slots: dict[str, str]) -> list[AccountConfig]:
	accounts: list[AccountConfig] = []
	names = set()
	for slot in SIDRUNE_SLOTS:
		raw = slots.get(slot, '')
		if not raw:
			raise ValueError(f'Missing Sidrune Secret {slot}; nothing was run')
		try:
			entries = json.loads(raw)
		except ValueError:
			raise ValueError(f'Invalid Sidrune Secret {slot}; nothing was run') from None
		if not isinstance(entries, list) or len(entries) != 1:
			raise ValueError(f'Expected exactly one account in {slot}; nothing was run')
		entry = entries[0]
		if not isinstance(entry, dict) or entry.get('provider') != 'sidrune':
			raise ValueError(f'Non-Sidrune account in {slot}; nothing was run')
		name = entry.get('name')
		if not isinstance(name, str) or not name.strip() or name in names:
			raise ValueError(f'Missing or duplicate account name in {slot}; nothing was run')
		names.add(name)
		accounts.append(AccountConfig.from_dict(entry, len(accounts)))
	return accounts


def main():
	accounts = select_accounts({slot: os.getenv(slot, '') for slot in SIDRUNE_SLOTS})
	provider = AppConfig.load_from_env().providers['sidrune']
	failed = 0
	for account in accounts:
		name = account.get_display_name(0)
		success, before, after = run_bearer_check_in(account, name, provider, exchange_only=True)
		before_balance = before['quota'] if before and before.get('success') else None
		after_balance = after['quota'] if after and after.get('success') else None
		verified = success and before_balance is not None and after_balance is not None
		change = (
			round(after_balance - before_balance, 4)
			if before_balance is not None and after_balance is not None
			else None
		)
		print(
			json.dumps(
				{
					'account': name,
					'success': bool(verified),
					'wallet_before': before_balance,
					'wallet_after': after_balance,
					'wallet_change': change,
					'error': None if verified else resolve_check_in_error(before, after),
				},
				ensure_ascii=False,
			),
			flush=True,
		)
		failed += not verified
	return 1 if failed else 0


if __name__ == '__main__':
	raise SystemExit(main())
