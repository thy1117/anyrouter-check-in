"""Export only confirmed Nexa wallet balances, without authentication data."""

import json
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo


def parse_balance(response) -> Decimal:
	if response.status_code != 200:
		raise ValueError(f'Balance read failed: HTTP {response.status_code}')
	try:
		payload = response.json()
	except ValueError:
		raise ValueError('Balance read failed: invalid JSON') from None
	data = payload.get('data') if isinstance(payload, dict) and payload.get('code') == 0 else None
	if not isinstance(data, dict):
		raise ValueError('Balance read failed: invalid response schema')
	try:
		balance = Decimal(str(data['balance']))
	except (KeyError, InvalidOperation, TypeError, ValueError):
		raise ValueError('Balance read failed: invalid balance') from None
	if not balance.is_finite():
		raise ValueError('Balance read failed: invalid balance')
	return balance


def write_snapshot(accounts, path='nexa-balances.json', request_id=None):
	balances = {}
	for name, value in accounts:
		balance = Decimal(str(value))
		if not balance.is_finite() or name in balances:
			raise ValueError('Invalid or duplicate Nexa balance')
		balances[name] = str(balance)
	if not balances:
		return
	Path(path).write_text(
		json.dumps(
			{
				'version': 2,
				'run_id': os.getenv('GITHUB_RUN_ID', ''),
				'run_attempt': os.getenv('GITHUB_RUN_ATTEMPT', ''),
				'request_id': request_id or os.getenv('NEXA_BALANCE_REQUEST_ID', ''),
				'observed_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
				'balances': balances,
			},
			ensure_ascii=False,
		),
		encoding='utf-8',
	)
