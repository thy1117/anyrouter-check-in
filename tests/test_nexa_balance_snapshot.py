import json

import pytest

from utils.nexa_balance_snapshot import write_snapshot


def test_snapshot_contains_only_confirmed_balances(tmp_path, monkeypatch):
	monkeypatch.setenv('GITHUB_RUN_ID', '123')
	monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '2')
	path = tmp_path / 'balances.json'
	write_snapshot([('NexaVlinks-1125', 1.92), ('NexaVlinks-g12', 1.19)], path)
	data = json.loads(path.read_text())
	assert data['run_id'] == '123' and data['run_attempt'] == '2'
	assert data['balances'] == {'NexaVlinks-1125': '1.92', 'NexaVlinks-g12': '1.19'}
	assert set(data) == {'version', 'run_id', 'run_attempt', 'observed_at', 'balances'}


@pytest.mark.parametrize('items', [[('x', float('nan'))], [('x', -1)], [('x', 1), ('x', 2)]])
def test_invalid_balances_are_not_exported(tmp_path, items):
	path = tmp_path / 'balances.json'
	with pytest.raises(ValueError):
		write_snapshot(items, path)
	assert not path.exists()


def test_empty_snapshot_is_not_written(tmp_path):
	path = tmp_path / 'balances.json'
	write_snapshot([], path)
	assert not path.exists()
