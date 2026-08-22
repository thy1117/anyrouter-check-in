import asyncio
import json

from utils.browser import read_access_token


class FakePage:
	def __init__(self, stored):
		self.stored = stored

	async def evaluate(self, script, key):
		return self.stored


def run(stored):
	return asyncio.run(read_access_token(FakePage(stored)))


def test_reads_flat_access_token():
	assert run(json.dumps({'access_token': 'flat-token', 'token_type': 'Bearer'})) == 'flat-token'


def test_reads_zustand_nested_camel_case():
	payload = {'state': {'auth': {'accessToken': 'nested-token'}}}
	assert run(json.dumps(payload)) == 'nested-token'


def test_returns_none_when_absent():
	assert run(json.dumps({'session': {'sid': 'abc'}})) is None


def test_returns_none_on_invalid_json():
	assert run('not-json') is None


def test_returns_none_when_storage_empty():
	assert run(None) is None
