"""Export only confirmed Nexa wallet balances, without authentication data."""
import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo


def write_snapshot(accounts, path='nexa-balances.json'):
	balances = {}
	for name, value in accounts:
		balance = Decimal(str(value))
		if not balance.is_finite() or balance < 0 or name in balances:
			raise ValueError('Invalid or duplicate Nexa balance')
		balances[name] = str(balance)
	if not balances:
		return
	Path(path).write_text(json.dumps({
		'version': 1,
		'run_id': os.getenv('GITHUB_RUN_ID', ''),
		'run_attempt': os.getenv('GITHUB_RUN_ATTEMPT', ''),
		'observed_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
		'balances': balances,
	}, ensure_ascii=False), encoding='utf-8')
