import base64
import io
import json
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import numpy as np
import pytest
from PIL import Image, ImageDraw

import checkin
from captcha_ocr.slider import solve_challenge
from utils.config import AccountConfig, AppConfig, load_accounts_config


def test_91token_accounts_keep_their_ids_and_are_wired_into_workflow(monkeypatch):
	for name in list(checkin.os.environ):
		if name in ('ANYROUTER_ACCOUNTS', 'EXTRA_ACCOUNTS', 'CHECKIN_DISABLED_PROVIDERS') or name.startswith(
			'EXTRA_ACCOUNTS_'
		):
			monkeypatch.delenv(name)
	for slot, name, uid in (
		(139, '1117', '1791'),
		(140, '1118', '2098'),
		(141, '1119', '2108'),
		(142, '1120', '2110'),
		(143, '1121', '2113'),
		(144, '1122', '2115'),
		(145, '1123', '2117'),
		(146, '1125', '2120'),
	):
		monkeypatch.setenv(
			f'EXTRA_ACCOUNTS_{slot}',
			json.dumps(
				[{'name': f'91Token-{name}', 'provider': '91token', 'api_user': uid, 'access_token': 'test-pat'}]
			),
		)
		workflow = Path('.github/workflows/checkin.yml').read_text(encoding='utf-8')
		assert f'EXTRA_ACCOUNTS_{slot}: ${{{{ secrets.EXTRA_ACCOUNTS_{slot} }}}}' in workflow
	accounts = load_accounts_config()
	assert [(account.name, account.api_user) for account in accounts] == [
		('91Token-1117', '1791'),
		('91Token-1118', '2098'),
		('91Token-1119', '2108'),
		('91Token-1120', '2110'),
		('91Token-1121', '2113'),
		('91Token-1122', '2115'),
		('91Token-1123', '2117'),
		('91Token-1125', '2120'),
	]
	assert checkin.filter_accounts_by_target(accounts, '91token') == accounts


HTTP_ONLY_ACCOUNT = {
	'name': '91Token-1126',
	'provider': '91token',
	'api_user': '90001',
	'access_token': 'test-http-only-pat',
	'http_only': True,
}
PROFILE_PATH = '/api/user/self'
CHECKIN_PATH = '/api/user/daily-reward'
CHECKIN_STATUS_PATH = f'{CHECKIN_PATH}?month=2026-11'
CAPTCHA_PATH = f'{CHECKIN_PATH}/captcha'
VERIFY_PATH = f'{CAPTCHA_PATH}/verify'


@pytest.mark.parametrize(('slot', 'suffix'), [(147, '1126'), (148, '1127')])
def test_91token_http_only_account_is_wired_into_workflow(monkeypatch, slot, suffix):
	for name in tuple(checkin.os.environ):
		if name in ('ANYROUTER_ACCOUNTS', 'EXTRA_ACCOUNTS', 'CHECKIN_DISABLED_PROVIDERS') or name.startswith(
			'EXTRA_ACCOUNTS_'
		):
			monkeypatch.delenv(name)
	account = {**HTTP_ONLY_ACCOUNT, 'name': f'91Token-{suffix}'}
	monkeypatch.setenv(f'EXTRA_ACCOUNTS_{slot}', json.dumps([account]))

	workflow = Path('.github/workflows/checkin.yml').read_text(encoding='utf-8')
	assert f'EXTRA_ACCOUNTS_{slot}: ${{{{ secrets.CHECKIN_ACCOUNT_{suffix} }}}}' in workflow
	accounts = load_accounts_config()
	assert accounts is not None
	assert len(accounts) == 1
	assert accounts[0].name == account['name']
	assert accounts[0].provider == '91token'
	assert accounts[0].api_user == HTTP_ONLY_ACCOUNT['api_user']
	assert accounts[0].http_only is True


@pytest.mark.parametrize('value', ['true', 'false', 1])
def test_91token_http_only_requires_a_boolean(value):
	with pytest.raises(ValueError, match='http_only'):
		AccountConfig.from_dict({**HTTP_ONLY_ACCOUNT, 'http_only': value}, 0)


@pytest.fixture
def run_91token_http_only(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	app_config = AppConfig.load_from_env()
	account = AccountConfig.from_dict(HTTP_ONLY_ACCOUNT, 0)
	assert account.http_only is True

	class FixedDatetime(datetime):
		@classmethod
		def now(cls, tz=None):
			return cls(2026, 10, 31, 16, 15, tzinfo=UTC).astimezone(tz)

	monkeypatch.setattr(checkin, 'datetime', FixedDatetime)
	monkeypatch.setattr('captcha_ocr.slider.solve_challenge', Mock(return_value=128))

	def forbid_browser_or_proxy(*args, **kwargs):
		pytest.fail('http_only unexpectedly initiated browser verification or proxy switching')

	for name in (
		'get_waf_cookies_with_browser',
		'launch_login_context',
		'resolve_account_proxy_node',
		'run_gorouter_check_in_in_page',
		'solve_turnstile_in_page',
	):
		monkeypatch.setattr(checkin, name, forbid_browser_or_proxy)

	client = httpx.AsyncClient

	async def run(*expected_requests, use_account_entrypoint=False, http_only=True):
		account.http_only = http_only
		pending = deque(expected_requests)
		requests = []

		def handle(request):
			if not pending:
				pytest.fail(f'Unexpected request: {request.method} {request.url}')
			requests.append(request)
			response = pending.popleft()[2]
			if isinstance(response, Exception):
				raise response
			return response

		monkeypatch.setattr(
			httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs)
		)
		if use_account_entrypoint:
			result = await checkin.check_in_account(account, 0, app_config)
		else:
			provider = app_config.providers['91token']
			result = await checkin._run_account_checkin(account, account.get_display_name(0), provider)
		assert not pending
		for request, (method, path, _) in zip(requests, expected_requests, strict=True):
			assert request.method == method
			assert str(request.url) == f'https://91token.com{path}'
			assert request.headers['Authorization'] == 'Bearer test-http-only-pat'
			assert request.headers['New-Api-User'] == HTTP_ONLY_ACCOUNT['api_user']
			assert 'X-Turnstile-Token' not in request.headers
			if request.method == 'POST' and request.url.path == CHECKIN_PATH:
				assert request.headers['X-Captcha-Token'] == 'test-slider-token'
			else:
				assert 'X-Captcha-Token' not in request.headers
			if request.url.path in (CAPTCHA_PATH, VERIFY_PATH):
				assert request.headers['X-Captcha-Request'] == '1'
			if request.url.path == VERIFY_PATH:
				assert json.loads(request.content) == {'id': 'test-puzzle', 'x': 128}
		return result

	return run


def _profile_response(user_id=90001):
	return httpx.Response(200, json={'success': True, 'data': {'id': user_id, 'quota': 500000}})


def _status_response(checked):
	return httpx.Response(200, json={'success': True, 'data': {'stats': {'checked_in_today': checked}}})


def _captcha_response(**overrides):
	return httpx.Response(
		200,
		json={'success': True, 'data': {'id': 'test-puzzle', 'expires_in': 120, **overrides}},
	)


def _verification_response(**overrides):
	return httpx.Response(
		200,
		json={'success': True, 'data': {'token': 'test-slider-token', 'expires_in': 60, **overrides}},
	)


@pytest.mark.parametrize('user_id', [None, 90002], ids=['missing', 'different'])
async def test_91token_http_only_requires_matching_profile_id(run_91token_http_only, user_id):
	success, _before, after = await run_91token_http_only(('GET', PROFILE_PATH, _profile_response(user_id)))

	assert success is False
	assert after['check_in_error']
	assert after['check_in_submitted'] is False


async def test_91token_http_only_does_not_submit_when_status_is_forbidden(run_91token_http_only):
	success, before, after = await run_91token_http_only(
		('GET', PROFILE_PATH, _profile_response()),
		('GET', CHECKIN_STATUS_PATH, httpx.Response(403, text='<html>Forbidden</html>')),
		use_account_entrypoint=True,
	)

	assert success is False
	assert before['success'] is True
	assert after['check_in_error']
	assert after['check_in_submitted'] is False


async def test_91token_forbidden_status_reports_reason_without_access_token(run_91token_http_only):
	success, _before, after = await run_91token_http_only(
		('GET', PROFILE_PATH, _profile_response()),
		(
			'GET',
			CHECKIN_STATUS_PATH,
			httpx.Response(
				403, json={'success': False, 'message': 'Access denied\n' + HTTP_ONLY_ACCOUNT['access_token']}
			),
		),
	)

	assert success is False
	assert after['check_in_error'].endswith(': Access denied [REDACTED]')
	assert HTTP_ONLY_ACCOUNT['access_token'] not in after['check_in_error']
	assert after['check_in_submitted'] is False


async def test_91token_http_only_skips_an_already_checked_in_account(run_91token_http_only):
	success, before, after = await run_91token_http_only(
		('GET', PROFILE_PATH, _profile_response()),
		('GET', CHECKIN_STATUS_PATH, _status_response(True)),
	)

	assert success is True
	assert before['success'] is True
	assert after['checked_in_today'] is True


@pytest.mark.parametrize('http_only', [False, True])
@pytest.mark.parametrize('use_account_entrypoint', [False, True])
async def test_91token_verifies_slider_submits_once_and_confirms_status(
	run_91token_http_only, http_only, use_account_entrypoint
):
	success, before, after = await run_91token_http_only(
		('GET', PROFILE_PATH, _profile_response()),
		('GET', CHECKIN_STATUS_PATH, _status_response(False)),
		('POST', CAPTCHA_PATH, _captcha_response()),
		('POST', VERIFY_PATH, _verification_response()),
		('POST', CHECKIN_PATH, httpx.Response(200, json={'success': True})),
		('GET', CHECKIN_STATUS_PATH, _status_response(True)),
		('GET', PROFILE_PATH, _profile_response()),
		http_only=http_only,
		use_account_entrypoint=use_account_entrypoint,
	)

	assert success is True
	assert before['success'] is True
	assert after['checked_in_today'] is True
	assert after['check_in_phase'] == 'confirmed'


@pytest.mark.parametrize('failure', ['turnstile', 'verification_page', 'redirect'])
async def test_91token_http_only_stops_after_a_rejected_submission(run_91token_http_only, failure):
	if failure == 'turnstile':
		response = httpx.Response(200, json={'success': False, 'message': 'Turnstile token is empty'})
	elif failure == 'verification_page':
		response = httpx.Response(403, text='<html>Website verification required</html>')
	else:
		response = httpx.Response(307, headers={'Location': '/profile'})

	success, before, after = await run_91token_http_only(
		('GET', PROFILE_PATH, _profile_response()),
		('GET', CHECKIN_STATUS_PATH, _status_response(False)),
		('POST', CAPTCHA_PATH, _captcha_response()),
		('POST', VERIFY_PATH, _verification_response()),
		('POST', CHECKIN_PATH, response),
	)

	assert success is False
	assert before['success'] is True
	assert after['check_in_error']
	assert after['check_in_submitted'] is True
	assert after['check_in_phase'] == 'submission_rejected'


def test_91token_uses_current_slider_endpoints(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	provider = AppConfig.load_from_env().providers['91token']
	assert provider.domain == 'https://91token.com'
	assert provider.login_path == '/profile'
	assert provider.sign_in_path == provider.check_in_status_path == CHECKIN_PATH
	assert provider.captcha_path == CAPTCHA_PATH
	assert provider.checkin_turnstile is False
	assert provider.turnstile_header == ''
	assert provider.use_proxy is False


@pytest.mark.parametrize('stage', ['issue', 'verify', 'token'])
async def test_91token_failed_slider_never_submits_checkin(run_91token_http_only, stage):
	requests = [
		('GET', PROFILE_PATH, _profile_response()),
		('GET', CHECKIN_STATUS_PATH, _status_response(False)),
		('POST', CAPTCHA_PATH, httpx.Response(403) if stage == 'issue' else _captcha_response()),
	]
	if stage != 'issue':
		requests.append(
			('POST', VERIFY_PATH, httpx.Response(403) if stage == 'verify' else _verification_response(token=''))
		)
	success, _before, after = await run_91token_http_only(*requests)
	assert success is False
	assert after['check_in_phase'] == 'not_submitted'
	assert after['check_in_submitted'] is False


async def test_91token_ambiguous_slider_never_verifies(monkeypatch, run_91token_http_only):
	monkeypatch.setattr('captcha_ocr.slider.solve_challenge', Mock(side_effect=ValueError('ambiguous')))
	success, _before, after = await run_91token_http_only(
		('GET', PROFILE_PATH, _profile_response()),
		('GET', CHECKIN_STATUS_PATH, _status_response(False)),
		('POST', CAPTCHA_PATH, _captcha_response()),
	)
	assert success is False
	assert 'ambiguous' in after['check_in_error']
	assert after['check_in_submitted'] is False


@pytest.mark.parametrize('expired_stage', ['challenge', 'token'])
async def test_91token_never_submits_expired_challenge_or_token(monkeypatch, run_91token_http_only, expired_stage):
	clock = [0, 121] if expired_stage == 'challenge' else [0, 0, 0, 61]
	monkeypatch.setattr(checkin, 'time', SimpleNamespace(monotonic=Mock(side_effect=clock)))
	requests = [
		('GET', PROFILE_PATH, _profile_response()),
		('GET', CHECKIN_STATUS_PATH, _status_response(False)),
		('POST', CAPTCHA_PATH, _captcha_response()),
	]
	if expired_stage == 'token':
		requests.append(('POST', VERIFY_PATH, _verification_response()))
	success, _before, after = await run_91token_http_only(*requests)
	assert success is False
	assert 'expired' in after['check_in_error']
	assert after['check_in_submitted'] is False


@pytest.mark.parametrize('submission', ['gateway_error', 'transport_error'])
async def test_91token_uncertain_submission_is_confirmed_without_replay(run_91token_http_only, submission, capsys):
	response = httpx.Response(502) if submission == 'gateway_error' else httpx.ReadTimeout('private-request')
	success, _before, after = await run_91token_http_only(
		('GET', PROFILE_PATH, _profile_response()),
		('GET', CHECKIN_STATUS_PATH, _status_response(False)),
		('POST', CAPTCHA_PATH, _captcha_response()),
		('POST', VERIFY_PATH, _verification_response()),
		('POST', CHECKIN_PATH, response),
		('GET', CHECKIN_STATUS_PATH, _status_response(True)),
		('GET', PROFILE_PATH, _profile_response()),
	)
	assert success is True
	assert after['check_in_phase'] == 'confirmed'
	assert after['check_in_submitted'] is True
	assert 'test-slider-token' not in capsys.readouterr().out


async def test_91token_reward_response_alone_does_not_confirm_checkin(run_91token_http_only):
	success, _before, after = await run_91token_http_only(
		('GET', PROFILE_PATH, _profile_response()),
		('GET', CHECKIN_STATUS_PATH, _status_response(False)),
		('POST', CAPTCHA_PATH, _captcha_response()),
		('POST', VERIFY_PATH, _verification_response()),
		('POST', CHECKIN_PATH, httpx.Response(200, json={'success': True})),
		('GET', CHECKIN_STATUS_PATH, _status_response(False)),
	)
	assert success is False
	assert after['check_in_phase'] == 'submitted_unconfirmed'
	assert after['check_in_submitted'] is True


def _data_url(image, image_format):
	output = io.BytesIO()
	image.save(output, image_format)
	return f'data:image/{image_format.lower()};base64,' + base64.b64encode(output.getvalue()).decode()


def _slider_challenge(*, duplicate=False):
	rng = np.random.default_rng(91)
	background = Image.fromarray(rng.integers(0, 256, (40, 80, 3), dtype=np.uint8)).resize((320, 160))
	x, y, size = 192, 56, 52
	mask = Image.new('L', (size, size))
	draw = ImageDraw.Draw(mask)
	draw.rectangle((8, 8, 44, 44), fill=255)
	draw.ellipse((18, 0, 34, 16), fill=255)
	piece = background.crop((x, y, x + size, y + size)).convert('RGBA')
	piece.putalpha(mask)
	shade = Image.fromarray((np.asarray(piece)[:, :, :3] * 0.45).astype(np.uint8))
	for offset in (64, x) if duplicate else (x,):
		background.paste(shade, (offset, y), mask)
	return {
		'width': 320,
		'height': 160,
		'piece_size': size,
		'y': y,
		'background': _data_url(background, 'JPEG'),
		'piece': _data_url(piece, 'PNG'),
	}


def test_slider_matches_a_shaded_jpeg_puzzle():
	assert solve_challenge(_slider_challenge()) == 192


def test_slider_rejects_ambiguous_positions():
	with pytest.raises(ValueError, match='ambiguous'):
		solve_challenge(_slider_challenge(duplicate=True))


@pytest.mark.parametrize(
	'override',
	[{'y': -1}, {'height': 4096}, {'piece_size': True}, {'background': 'invalid'}, {'width': 321}],
)
def test_slider_rejects_invalid_geometry_or_image_data(override):
	with pytest.raises(ValueError):
		solve_challenge({**_slider_challenge(), **override})
