"""Explicit single-account test entry point; never invokes the multi-site main."""

import json
import os

from checkin import run_xiaobai_check_in
from utils.config import AccountConfig, AppConfig


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
	source, account = select_account_with_source(
		name, {slot: os.getenv(slot, '') for slot in ('EXTRA_ACCOUNTS_16', 'EXTRA_ACCOUNTS_13')}
	)
	print(f'[CONFIG] Account {name!r} is in production Secret {source}', flush=True)
	if os.getenv('XIAOBAI_CONFIG_ONLY', 'false').lower() == 'true':
		# One-time encrypted transfer for a user-authorized update of one account.
		# Removed from the repair branch immediately after the transfer is consumed.
		if name != '小白Code-1125' or source != 'EXTRA_ACCOUNTS_13':
			raise ValueError('Credential update scope mismatch; nothing exported')
		from pathlib import Path
		from cryptography.fernet import Fernet

		key = os.environ['XIAOBAI_TOKEN_STATE_KEY'].encode()
		envelope = json.dumps({'source': source, 'account': name, 'run_id': os.environ['GITHUB_RUN_ID'],
			'configuration': os.environ[source]}, ensure_ascii=False).encode()
		ciphertext = Fernet(key).encrypt(envelope)
		directory = Path('.xiaobai-token-recovery')
		directory.mkdir(mode=0o700, exist_ok=True)
		destination = directory / 'account-config.fernet'
		with destination.open('xb') as output:
			output.write(ciphertext)
		destination.chmod(0o600)
		print('[CONFIG] Encrypted configuration prepared; no plaintext credentials logged', flush=True)
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
