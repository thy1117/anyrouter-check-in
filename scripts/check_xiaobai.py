"""Explicit single-account test entry point; never invokes the multi-site main."""

import json
import os

from checkin import run_xiaobai_check_in
from utils.config import AccountConfig, AppConfig


def select_account(name: str, slots: dict[str, str]) -> AccountConfig:
	matches = []
	for raw in slots.values():
		if not raw:
			continue
		entries = json.loads(raw)
		if not isinstance(entries, list):
			raise ValueError('Account Secret must contain a JSON list')
		for entry in entries:
			if isinstance(entry, dict) and entry.get('provider') == 'xiaobai' and entry.get('name') == name:
				matches.append(AccountConfig.from_dict(entry, 0))
	if len(matches) != 1:
		raise ValueError(f'Expected exactly one matching Xiaobai account, found {len(matches)}; nothing was run')
	return matches[0]


def main():
	name = os.environ['XIAOBAI_TEST_ACCOUNT'].strip()
	if not name:
		raise ValueError('An exact account name is required; nothing was run')
	account = select_account(name, {slot: os.getenv(slot, '') for slot in ('EXTRA_ACCOUNTS_16', 'EXTRA_ACCOUNTS_13')})
	status_only = os.getenv('XIAOBAI_STATUS_ONLY', 'true').lower() != 'false'
	provider = AppConfig.load_from_env().providers['xiaobai']
	print(f'[TEST] One Xiaobai account only: {name}; status_only={status_only}', flush=True)
	success, _, details = run_xiaobai_check_in(account, name, provider, status_only=status_only)
	print(
		json.dumps(
			{'account': name, 'success': success, 'status_only': status_only, 'details': details}, ensure_ascii=False
		)
	)
	return 0 if success else 1


if __name__ == '__main__':
	raise SystemExit(main())
