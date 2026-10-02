"""Explicit single-account test entry point; never invokes the multi-site main."""

import json
import os

from checkin import run_xiaobai_check_in
from utils.config import AccountConfig, AppConfig

# Include the later Xiaobai slots as well as the legacy slot mapping.
XIAOBAI_TEST_SLOTS = tuple(f'EXTRA_ACCOUNTS_{n}' for n in (13, 16, 42, 43, 44, 45))


def select_account_with_source(name: str, slots: dict[str, str]) -> tuple[str, AccountConfig]:
	matches = []
	for slot, raw in slots.items():
		if not raw:
			continue
		entries = json.loads(raw)
		if not isinstance(entries, list):
			raise ValueError('Account Secret must contain a JSON list')
		for entry in entries:
			if isinstance(entry, dict) and entry.get('provider') == 'xiaobai' and entry.get('name') == name:
				matches.append((slot, AccountConfig.from_dict(entry, 0)))
	if len(matches) != 1:
		raise ValueError(f'Expected exactly one matching Xiaobai account, found {len(matches)}; nothing was run')
	return matches[0]


def select_account(name: str, slots: dict[str, str]) -> AccountConfig:
	return select_account_with_source(name, slots)[1]


def main():
	name = os.environ['XIAOBAI_TEST_ACCOUNT'].strip()
	if not name:
		raise ValueError('An exact account name is required; nothing was run')
	source, account = select_account_with_source(name, {slot: os.getenv(slot, '') for slot in XIAOBAI_TEST_SLOTS})
	print(f'[CONFIG] Account {name!r} is in production Secret {source}', flush=True)
	if os.getenv('XIAOBAI_CONFIG_ONLY', 'false').lower() == 'true':
		print('[CONFIG] Configuration lookup only; no authentication, refresh or check-in attempted', flush=True)
		return 0
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
