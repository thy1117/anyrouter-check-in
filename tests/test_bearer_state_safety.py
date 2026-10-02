"""Offline regressions for durable, non-replayable TokenRouter authentication."""

import json
from unittest.mock import MagicMock

import httpx
import pytest

from checkin import run_bearer_check_in
from utils.config import AccountConfig, ProviderConfig
from utils.xiaobai_token_state import TokenStateError


def setup_session(monkeypatch, *, state=None, refresh_response=None, unauthorized=True):
	account = AccountConfig(
		cookies=None, name='test', provider='guyscode', access_token='stale-access', refresh_token='stale-refresh'
	)
	provider = ProviderConfig(
		name='guyscode',
		domain='https://example.test',
		api_style='tokenrouter',
		user_info_path='/profile',
		auth_refresh_path='/refresh',
		sign_in_path='/checkin',
		check_in_status_path='/status',
		use_proxy=False,
	)
	requests = []

	def handler(request):
		requests.append(request)
		if request.url.path == '/refresh':
			assert json.loads(request.content) == {'refresh_token': 'saved-refresh'}
			state.save.assert_called_once_with('saved-access', 'saved-refresh', pending=True)
			if isinstance(refresh_response, Exception):
				raise refresh_response
			return refresh_response or httpx.Response(
				200, json={'code': 0, 'data': {'access_token': 'new-access', 'refresh_token': 'new-refresh'}}
			)
		if request.url.path == '/profile':
			if unauthorized and request.headers['Authorization'] != 'Bearer new-access':
				return httpx.Response(401, json={'code': 401})
			if request.headers['Authorization'] == 'Bearer new-access':
				state.save.assert_called_with('new-access', 'new-refresh')
			return httpx.Response(200, json={'code': 0, 'data': {'balance': 2.1}})
		assert request.method == 'GET' and request.url.path == '/status'
		return httpx.Response(200, json={'code': 0, 'data': {'checked_in_today': True}})

	client = httpx.Client
	monkeypatch.setattr(
		'checkin.httpx.Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)
	monkeypatch.setattr('checkin.create_xiaobai_token_state', lambda *args: state)
	return lambda: run_bearer_check_in(account, 'test', provider), requests


def state_mock():
	state = MagicMock()
	state.load.return_value = ('saved-access', 'saved-refresh')
	return state


def test_refresh_saves_pending_then_replacement_before_any_followup(monkeypatch):
	state = state_mock()
	run, requests = setup_session(monkeypatch, state=state)
	assert run()[0] is True
	assert [r.url.path for r in requests] == ['/profile', '/refresh', '/profile', '/status', '/profile']
	assert requests[0].headers['Authorization'] == 'Bearer saved-access'
	assert state.save.call_count == 2
	state.close.assert_called_once()


@pytest.mark.parametrize('failure', ['load', 'pending', 'replacement'])
def test_state_errors_fail_closed_without_reusing_secret_or_continuing(monkeypatch, failure):
	state = state_mock()
	if failure == 'load':
		state.load.side_effect = TokenStateError('state unavailable')
	else:

		def save(access, refresh, *, pending=False):
			if pending == (failure == 'pending'):
				raise TokenStateError('state unavailable')

		state.save.side_effect = save
	run, requests = setup_session(monkeypatch, state=state)
	success, _, error = run()
	assert not success and 'state unavailable' in error['check_in_error']
	assert [r.url.path for r in requests] == {
		'load': [],
		'pending': ['/profile'],
		'replacement': ['/profile', '/refresh'],
	}[failure]
	state.close.assert_called_once()


def test_missing_store_refuses_to_rotate(monkeypatch):
	run, requests = setup_session(monkeypatch)
	assert 'refusing an unpersisted rotation' in run()[2]['check_in_error']
	assert [r.url.path for r in requests] == ['/profile']


@pytest.mark.parametrize(
	'response',
	[
		httpx.Response(502, text='<html>private-token</html>', headers={'content-type': 'text/html'}),
		httpx.ReadTimeout('private-token'),
		httpx.Response(200, json={'code': 0, 'data': {}}),
	],
)
def test_ambiguous_refresh_keeps_pending_and_is_never_replayed(monkeypatch, capsys, response):
	state = state_mock()
	run, requests = setup_session(monkeypatch, state=state, refresh_response=response)
	assert run()[0] is False
	state.save.assert_called_once_with('saved-access', 'saved-refresh', pending=True)
	assert [r.url.path for r in requests] == ['/profile', '/refresh']
	assert 'private-token' not in capsys.readouterr().out


def test_invalid_refresh_reports_http_status_without_echoing_private_message(monkeypatch, capsys):
	state = state_mock()
	response = httpx.Response(401, json={'reason': 'REFRESH_TOKEN_INVALID', 'message': 'private-token'})
	run, requests = setup_session(monkeypatch, state=state, refresh_response=response)
	error = run()[2]['check_in_error']
	assert 'HTTP 401' in error and 'REFRESH_TOKEN_INVALID' in error
	assert 'update this account' in error
	assert 'private-token' not in capsys.readouterr().out
	assert len(requests) == 2
	state.save.assert_called_with('saved-access', 'saved-refresh')
