"""Read-only identity, eligibility, and group diagnostics for two Nexa accounts."""

import base64
import json
from decimal import Decimal, InvalidOperation

import httpx

from utils.config import AccountConfig, AppConfig, load_accounts_config
from utils.xiaobai_token_state import create_xiaobai_token_state, mask_tokens

ACCOUNTS = ('NexaVlinks-27', 'NexaVlinks-38')
NUMERIC_STATUS_FIELDS = (
	'usage_today',
	'usage_yesterday',
	'today_usage',
	'yesterday_usage',
	'today_cost',
	'yesterday_cost',
	'required_usage',
	'usage_threshold',
	'today_quota',
	'yesterday_quota',
)


def select_accounts(accounts: list[AccountConfig]) -> list[AccountConfig]:
	selected = []
	for name in ACCOUNTS:
		matches = [account for account in accounts if account.name == name]
		if len(matches) != 1 or matches[0].provider != 'nexavlinks':
			raise ValueError(f'Expected exactly one nexavlinks account named {name}; nothing was run')
		if not matches[0].access_token:
			raise ValueError(f'{name} requires an existing access token; no refresh or login permitted')
		selected.append(matches[0])
	return selected


def _data(response, phase):
	if response.status_code != 200:
		raise ValueError(f'{phase}: HTTP {response.status_code}; no refresh or submission attempted')
	try:
		payload = response.json()
	except ValueError:
		raise ValueError(f'{phase}: invalid JSON') from None
	data = payload.get('data') if isinstance(payload, dict) and payload.get('code') == 0 else None
	if not isinstance(data, dict):
		raise ValueError(f'{phase}: invalid response schema')
	return data


def _number(value):
	if value is None or isinstance(value, bool):
		return None
	try:
		number = Decimal(str(value))
		return str(number) if number.is_finite() else None
	except InvalidOperation:
		return None


def _diagnostic_payload(response):
	result = {'status': response.status_code}
	try:
		result['payload'] = response.json()
	except ValueError:
		result['invalid_json'] = True
	return result


def read_status(account, provider, include_diagnostics=False):
	result = {'account': account.name, 'provider': account.provider, 'status_read_ok': False}
	state = None
	try:
		if provider.name != 'nexavlinks' or provider.domain != 'https://asia.nexavlinks.com':
			raise ValueError('Unexpected provider identity; nothing was run')
		state = create_xiaobai_token_state(account, provider.token_state_domain or provider.domain)
		access, refresh = state.load() if state else (account.access_token or '', '')
		if not access:
			raise ValueError('No current access token; no refresh permitted')
		mask_tokens(access, refresh)
		headers = {'Authorization': f'Bearer {access}', 'Accept': 'application/json'}
		with httpx.Client(headers=headers, timeout=30, trust_env=False, follow_redirects=False) as client:
			profile = _data(client.get(provider.domain + provider.user_info_path), 'Identity read')
			if not isinstance(profile.get('id'), int):
				raise ValueError('Identity read: missing numeric user ID')
			result.update(user_id=profile['id'], balance=_number(profile.get('balance')))
			status = _data(client.get(provider.domain + provider.check_in_status_path), 'Check-in status')
			if type(status.get('checked_in_today')) is not bool or type(status.get('eligible')) is not bool:
				raise ValueError('Check-in status: expected boolean daily status and eligibility')
			result.update(status_read_ok=True, checked_in_today=status['checked_in_today'], eligible=status['eligible'])
			result['usage_fields'] = {key: _number(status[key]) for key in NUMERIC_STATUS_FIELDS if key in status}
			result['submission'] = 'not_attempted_status_only'
			if include_diagnostics:
				result['diagnostics'] = {
					path: _diagnostic_payload(client.get(provider.domain + path))
					for path in (
						'/api/v1/announcements',
						'/api/v1/groups/available',
						'/api/v1/groups/rates',
						'/api/v1/channels/available',
						'/api/v1/status',
					)
				}
	except ValueError as exc:
		result['error'] = str(exc)
	except Exception as exc:
		result['error'] = f'{type(exc).__name__}; no refresh or submission attempted'
	finally:
		if state:
			state.close()
	return result


def run() -> int:
	accounts = select_accounts(load_accounts_config() or [])
	provider = AppConfig.load_from_env().providers['nexavlinks']
	failures = 0
	print(
		'[STATUS] Exactly two Nexa accounts; GET identity and daily status only. No refresh, challenge or check-in POST.'
	)
	for account in accounts:
		result = read_status(account, provider, include_diagnostics=account is accounts[0])
		diagnostics = result.pop('diagnostics', None)
		print('NEXA_STATUS ' + json.dumps(result, ensure_ascii=False), flush=True)
		if diagnostics is not None:
			encoded = base64.b64encode(json.dumps(diagnostics, ensure_ascii=False).encode()).decode()
			print('NEXA_DIAGNOSTICS_B64 ' + encoded, flush=True)
		failures += not result['status_read_ok']
	return int(bool(failures))


if __name__ == '__main__':
	raise SystemExit(run())
