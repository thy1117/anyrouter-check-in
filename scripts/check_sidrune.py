"""Check in every configured Sidrune account without exchanging welfare balance."""

import json
import os

from checkin import resolve_check_in_error, run_bearer_check_in
from scripts.exchange_sidrune import SIDRUNE_SLOTS, select_accounts
from utils.config import AppConfig

CHECKIN_SLOTS = SIDRUNE_SLOTS + tuple(f'EXTRA_ACCOUNTS_{number}' for number in range(98, 110))


def main():
	accounts = select_accounts({slot: os.getenv(slot, '') for slot in CHECKIN_SLOTS}, CHECKIN_SLOTS)
	provider = AppConfig.load_from_env().providers['sidrune']
	failed = 0
	for account in accounts:
		name = account.get_display_name(0)
		success, before, after = run_bearer_check_in(account, name, provider, checkin_only=True)
		details = (after or {}).get('sidrune_checkin', {})
		verified = success and details.get('checked_in_today') is True
		status = 'already_checked_in' if details.get('already_checked_in') else 'checked_in'
		print(
			json.dumps(
				{
					'account': name,
					'success': bool(verified),
					'status': status if verified else 'failed',
					**details,
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
