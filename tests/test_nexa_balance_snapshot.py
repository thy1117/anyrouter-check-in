import json
from decimal import Decimal

import httpx
import pytest

from utils.nexa_balance_snapshot import parse_balance, write_snapshot


def test_snapshot_contains_only_confirmed_balances(tmp_path, monkeypatch):
	monkeypatch.setenv('GITHUB_RUN_ID', '123')
	monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '2')
	monkeypatch.setenv('NEXA_BALANCE_REQUEST_ID', 'request-123')
	path = tmp_path / 'balances.json'
	write_snapshot([('NexaVlinks-1125', 1.92), ('NexaVlinks-g12', 1.19)], path)
	data = json.loads(path.read_text())
	assert data['run_id'] == '123' and data['run_attempt'] == '2'
	assert data['balances'] == {'NexaVlinks-1125': '1.92', 'NexaVlinks-g12': '1.19'}
	assert data['version'] == 2 and data['request_id'] == 'request-123'
	assert data['mode'] == 'checkin'
	assert set(data) == {'version', 'mode', 'run_id', 'run_attempt', 'request_id', 'observed_at', 'balances'}


@pytest.mark.parametrize('items', [[('x', float('nan'))], [('x', 1), ('x', 2)]])
def test_invalid_balances_are_not_exported(tmp_path, items):
	path = tmp_path / 'balances.json'
	with pytest.raises(ValueError):
		write_snapshot(items, path)
	assert not path.exists()


def test_empty_snapshot_is_not_written(tmp_path):
	path = tmp_path / 'balances.json'
	write_snapshot([], path)
	assert not path.exists()


@pytest.mark.parametrize(
	'response',
	[
		httpx.Response(401, json={'code': 0, 'data': {'balance': '1'}}),
		httpx.Response(200, text='not-json'),
		httpx.Response(200, json={'code': 0, 'data': {}}),
		httpx.Response(200, json={'code': 0, 'data': {'balance': 'NaN'}}),
		httpx.Response(200, json={'code': 0, 'data': {'balance': 'Infinity'}}),
		httpx.Response(200, json={'code': 1, 'data': {'balance': '1'}}),
		httpx.Response(200, json={'code': 0, 'data': {'balance': True}}),
	],
)
def test_parse_balance_rejects_untrusted_responses(response):
	with pytest.raises(ValueError):
		parse_balance(response)


def test_parse_balance_preserves_precision_and_negative_debt():
	response = httpx.Response(200, json={'code': 0, 'data': {'balance': '-0.15000123'}})
	assert parse_balance(response) == Decimal('-0.15000123')


def test_read_only_snapshot_preserves_negative_debt_and_explicit_request_id(tmp_path, monkeypatch):
	monkeypatch.setenv('NEXA_BALANCE_REQUEST_ID', 'different-request')
	path = tmp_path / 'balances.json'
	write_snapshot(
		[('NexaVlinks-1125', Decimal('-0.15000123'))], path, request_id='explicit-request', mode='read-only-balance'
	)
	data = json.loads(path.read_text())
	assert data['version'] == 2 and data['mode'] == 'read-only-balance'
	assert data['request_id'] == 'explicit-request'
	assert data['balances'] == {'NexaVlinks-1125': '-0.15000123'}
