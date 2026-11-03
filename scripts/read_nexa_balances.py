"""Read NexaVlinks wallet balances without check-in or status requests."""

import json
import os
import re
from decimal import Decimal
from pathlib import Path

import httpx

from utils.config import AccountConfig, AppConfig, load_accounts_config
from utils.nexa_balance_snapshot import parse_balance, write_snapshot
from utils.xiaobai_token_state import TokenStateError, create_xiaobai_token_state, mask_tokens

NEXA_ORIGIN = 'https://asia.nexavlinks.com'
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
NEXA_SLOTS = (64, 65, 83, 84, 85, *range(88, 98), 118, 119)


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
	identities = [
		ALIASES.get(str(name).removeprefix('NexaVlinks-'), str(name).removeprefix('NexaVlinks-')) for name in names
	]
	if len(set(identities)) != len(identities):
		raise ValueError('NexaVlinks account aliases resolve to duplicate identities; nothing was run')
	missing = EXPECTED_ACCOUNTS - set(identities)
	if missing:
		raise ValueError(f'Missing NexaVlinks identities: {sorted(missing)}; nothing was run')
	return selected


def read_balance(account: AccountConfig, provider) -> Decimal:
	if (
		account.provider != 'nexavlinks'
		or provider.name != 'nexavlinks'
		or provider.domain != NEXA_ORIGIN
		or provider.token_state_domain not in (None, NEXA_ORIGIN)
		or provider.user_info_path != '/api/v1/auth/me'
		or provider.auth_refresh_path != '/api/v1/auth/refresh'
		or account.has_login_credentials()
		or provider.use_proxy
	):
		raise ValueError('Invalid Nexa balance-only configuration; nothing was run')
	state = None
	access, refresh = account.access_token or '', account.refresh_token or ''
	mask_tokens(access, refresh)
	try:
		state = create_xiaobai_token_state(account, NEXA_ORIGIN)
		if state is not None:
			access, refresh = state.load()
		with httpx.Client(
			http2=provider.http2,
			timeout=30.0,
			trust_env=False,
			follow_redirects=False,
			auth=None,
			headers={'Accept': 'application/json', 'Origin': NEXA_ORIGIN, 'Referer': NEXA_ORIGIN + '/dashboard'},
		) as client:
			refresh_attempted = False

			def refresh_tokens():
				nonlocal access, refresh, refresh_attempted
				if refresh_attempted or state is None or not refresh:
					raise ValueError('Token refresh requires encrypted token-state storage and a refresh token')
				state.save(access, refresh, pending=True)
				refresh_attempted = True
				response = client.post(NEXA_ORIGIN + '/api/v1/auth/refresh', json={'refresh_token': refresh})
				if response.status_code in (400, 401):
					state.save(access, refresh)
				if response.status_code != 200:
					raise ValueError(f'Authentication refresh failed: HTTP {response.status_code}')
				try:
					payload = response.json()
				except ValueError:
					raise ValueError('Authentication refresh failed: invalid JSON') from None
				data = payload.get('data') if isinstance(payload, dict) and payload.get('code') == 0 else None
				if not isinstance(data, dict) or data.get('requires_2fa'):
					raise ValueError('Authentication refresh failed: invalid response schema')
				new_access, new_refresh = data.get('access_token'), data.get('refresh_token') or refresh
				if (
					not isinstance(new_access, str)
					or not new_access
					or not isinstance(new_refresh, str)
					or not new_refresh
				):
					raise ValueError('Authentication refresh returned unusable tokens')
				mask_tokens(new_access, new_refresh)
				state.save(new_access, new_refresh)
				access, refresh = new_access, new_refresh

			if not access and refresh:
				refresh_tokens()
			if not access:
				raise ValueError('No usable Nexa access token')
			response = client.get(NEXA_ORIGIN + '/api/v1/auth/me', headers={'Authorization': f'Bearer {access}'})
			if response.status_code == 401 and refresh:
				refresh_tokens()
				response = client.get(NEXA_ORIGIN + '/api/v1/auth/me', headers={'Authorization': f'Bearer {access}'})
			return parse_balance(response)
	except TokenStateError as exc:
		raise ValueError(str(exc)) from None
	except httpx.HTTPError as exc:
		raise ValueError(f'Balance read failed: {type(exc).__name__}') from None
	finally:
		if state is not None:
			state.close()


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
	write_snapshot(balances, request_id=os.getenv('NEXA_BALANCE_REQUEST_ID'), mode='read-only-balance')
	print(json.dumps({'read': len(balances), 'failed': failures, 'snapshot': 'nexa-balances.json'}, ensure_ascii=False))
	return int(bool(failures))


if __name__ == '__main__':
	raise SystemExit(run())
