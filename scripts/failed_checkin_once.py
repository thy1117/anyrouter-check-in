"""Isolated manual-run branch: verify three accounts and stop at human verification."""

import asyncio
import json
from pathlib import Path

from checkin import run_gorouter_check_in_in_page
from scripts.check_nexa_status import read_status
from utils.config import AccountConfig, AppConfig, load_accounts_config

TARGETS = {
	'NexaVlinks-27': ('nexavlinks', 6940),
	'NexaVlinks-38': ('nexavlinks', 6946),
	'Nianhua-thy1119': ('nianhua', 8108),
}


def select_accounts(accounts: list[AccountConfig]) -> list[AccountConfig]:
	selected = []
	for name, (provider, _) in TARGETS.items():
		matches = [account for account in accounts if account.name == name]
		if len(matches) != 1 or matches[0].provider != provider:
			raise ValueError(f'Expected exactly one {provider} account named {name}; nothing was run')
		account = matches[0]
		if not account.access_token or (provider == 'nianhua' and not account.api_user):
			raise ValueError(f'{name} requires an existing web token; no login or refresh permitted')
		selected.append(account)
	return selected


def classify(name, read_ok, checked, user_id, eligible=None):
	result = {
		'account': name,
		'user_id': user_id,
		'status_read_ok': bool(read_ok),
		'checked_in_today': checked if read_ok else None,
		'check_in_submitted': False,
	}
	if not read_ok or type(checked) is not bool:
		result['phase'] = 'status_read_failed'
	elif user_id != TARGETS[name][1]:
		result.update(phase='identity_mismatch', status_read_ok=False, checked_in_today=None)
	elif checked:
		result['phase'] = 'confirmed'
	elif eligible is False:
		result['phase'] = 'not_eligible'
	else:
		result['phase'] = 'human_verification_required'
	if type(eligible) is bool:
		result['eligible'] = eligible
	return result


async def run() -> int:
	accounts = select_accounts(load_accounts_config() or [])
	providers = AppConfig.load_from_env().providers
	results = []
	print(
		'[SCOPED] Exactly three failed accounts; existing authentication only. Stop before human verification; never request or answer a challenge, refresh credentials, or submit check-in.',
		flush=True,
	)
	for account in accounts:
		provider = providers[account.provider]
		if provider.name != account.provider:
			raise ValueError('Provider identity mismatch; no account request')
		if account.provider == 'nexavlinks':
			info = read_status(account, provider)
			result = classify(
				account.name,
				info['status_read_ok'],
				info.get('checked_in_today'),
				info.get('user_id'),
				eligible=info.get('eligible'),
			)
		else:
			ok, _, info = await run_gorouter_check_in_in_page(account, account.name, provider, status_only=True)
			info = info or {}
			result = classify(account.name, ok, info.get('checked_in_today'), info.get('user_id'))
		results.append(result)
		print('SCOPED_CHECKIN ' + json.dumps(result, ensure_ascii=False), flush=True)
	assert len(results) == 3 and {r['account'] for r in results} == set(TARGETS)
	Path('scoped-checkin-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
	confirmed = sum(result['phase'] == 'confirmed' for result in results)
	print(f'[RESULT] {confirmed}/3 confirmed; remaining accounts were not submitted.', flush=True)
	return 0 if confirmed == 3 else 1


if __name__ == '__main__':
	raise SystemExit(asyncio.run(run()))
