import asyncio
import json

from utils.browser import read_access_token, read_auth_session


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


def test_read_auth_session_returns_token_and_sid():
	payload = {
		'access_token': 'tok',
		'token_type': 'Bearer',
		'session': {'sid': 'sid-123', 'current': True},
	}
	assert asyncio.run(read_auth_session(FakePage(json.dumps(payload)))) == ('tok', 'sid-123')


def test_read_auth_session_finds_sid_in_zustand_shape():
	payload = {'state': {'auth': {'accessToken': 'tok2', 'session': {'sid': 'sid-456'}}}}
	assert asyncio.run(read_auth_session(FakePage(json.dumps(payload)))) == ('tok2', 'sid-456')


def test_read_auth_session_handles_missing_sid():
	assert asyncio.run(read_auth_session(FakePage(json.dumps({'access_token': 'tok3'})))) == ('tok3', None)


def test_read_auth_session_empty_storage():
	assert asyncio.run(read_auth_session(FakePage(None))) == (None, None)
