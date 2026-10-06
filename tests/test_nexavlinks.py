"""Offline checks for NexaVlinks rotation matching and confirmed check-in."""

import base64
import io
import json
from unittest.mock import MagicMock

import httpx
import numpy as np
import pytest
from PIL import Image, ImageFilter

from captcha_ocr.rotate import solve_data_urls
from checkin import run_bearer_check_in
from utils.config import AccountConfig, AppConfig, ProviderConfig


def data_url(image):
	output = io.BytesIO()
	image.save(output, format='PNG')
	return 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()


@pytest.mark.parametrize('rotation', [0, 17, 83, 181, 297])
def test_rotation_recovers_known_clockwise_angle(rotation):
	pixels = np.random.default_rng(42).integers(0, 256, (220, 220, 3), dtype=np.uint8)
	background = Image.fromarray(pixels).filter(ImageFilter.GaussianBlur(1.5)).convert('RGBA')
	thumb = background.crop((40, 40, 180, 180)).rotate(-rotation, resample=Image.Resampling.BICUBIC)
	angle = solve_data_urls(data_url(background), data_url(thumb))
	assert abs((angle - (360 - rotation) + 180) % 360 - 180) <= 1


def test_rotation_rejects_ambiguous_image():
	with pytest.raises(ValueError, match='ambiguous'):
		solve_data_urls(
			data_url(Image.new('RGBA', (220, 220), 'white')), data_url(Image.new('RGBA', (140, 140), 'white'))
		)


@pytest.mark.parametrize('image', ['not-an-image', 'data:image/png;base64,invalid', data_url(Image.new('RGB', (1, 1)))])
def test_rotation_rejects_invalid_images(image):
	with pytest.raises(ValueError):
		solve_data_urls(image, data_url(Image.new('RGBA', (140, 140))))


def test_provider_and_overrides_keep_rotation_defaults(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	provider = AppConfig.load_from_env().providers['nexavlinks']
	assert provider.domain == 'https://asia.nexavlinks.com'
	assert provider.api_style == 'sub2api'
	assert provider.user_info_path == '/api/v1/auth/me'
	assert provider.auth_refresh_path == '/api/v1/auth/refresh'
	assert provider.sign_in_path == provider.check_in_status_path == '/api/v1/check-in'
	assert provider.captcha_path == '/api/v1/check-in/challenge'
	assert provider.checkin_rotate is True and provider.use_proxy is False
	assert provider.login_api_path is None
	assert ProviderConfig.from_dict('nexavlinks', {'domain': provider.domain}, defaults=provider).checkin_rotate is True


def response(data):
	return httpx.Response(200, json={'code': 0, 'data': data})


PROFILE = response({'balance': 2.1})
PENDING = response({'checked_in_today': False, 'eligible': True})
CHECKED = response({'checked_in_today': True, 'eligible': True})
CHALLENGE = response({'mode': 'rotate', 'id': 'test-captcha', 'image': 'image', 'thumb': 'thumb', 'expires_in': 120})


def setup_session(monkeypatch, status=PENDING, challenge=CHALLENGE, claim=None, confirmed=CHECKED):
	provider = AppConfig.load_from_env().providers['nexavlinks']
	account = AccountConfig(cookies=None, name='NexaVlinks-test', provider='nexavlinks', access_token='test-access')
	requests = []
	state = MagicMock()
	state.load.return_value = ('saved-access', 'saved-refresh')
	monkeypatch.setattr('checkin.create_xiaobai_token_state', lambda *args: state)
	monkeypatch.setattr('captcha_ocr.rotate.solve_data_urls', lambda *args: 135)

	def handler(request):
		requests.append(request)
		assert request.headers['Authorization'] == 'Bearer saved-access'
		if request.url.path == '/api/v1/auth/me':
			return PROFILE
		if request.url.path == '/api/v1/check-in/challenge':
			return challenge
		if request.method == 'POST':
			assert request.url.path == '/api/v1/check-in'
			assert json.loads(request.content) == {'captcha_id': 'test-captcha', 'captcha_angle': 135}
			if isinstance(claim, Exception):
				raise claim
			return claim if claim is not None else response({})
		assert request.url.path == '/api/v1/check-in'
		count = sum(r.method == 'GET' and r.url.path == request.url.path for r in requests)
		return status if count == 1 else confirmed

	client = httpx.Client
	monkeypatch.setattr(
		'checkin.httpx.Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)
	return lambda: run_bearer_check_in(account, 'NexaVlinks-test', provider), requests, state


def test_already_checked_in_skips_challenge_and_submission(monkeypatch):
	run, requests, state = setup_session(monkeypatch, status=CHECKED)
	assert run()[0] is True
	assert [r.url.path for r in requests] == ['/api/v1/auth/me', '/api/v1/check-in', '/api/v1/auth/me']
	state.close.assert_called_once()


def test_rotation_submits_once_and_requires_server_confirmation(monkeypatch):
	run, requests, state = setup_session(monkeypatch)
	success, before, after = run()
	assert success is True and before['success'] is True and after['success'] is True
	assert [r.method for r in requests] == ['GET', 'GET', 'GET', 'POST', 'GET', 'GET']
	assert requests[4].url.path == '/api/v1/check-in'
	state.close.assert_called_once()


@pytest.mark.parametrize(
	'status',
	[
		httpx.Response(401, json={'code': 'UNAUTHORIZED'}),
		httpx.Response(200, text='private-token'),
		httpx.Response(200, json={'code': 1, 'data': {'checked_in_today': True}}),
		response({'checked_in_today': 'true', 'eligible': True}),
		response({'checked_in_today': False, 'eligible': False}),
	],
)
def test_invalid_or_ineligible_status_never_submits(monkeypatch, capsys, status):
	run, requests, _ = setup_session(monkeypatch, status=status)
	assert run()[0] is False
	assert all(r.method == 'GET' and not r.url.path.endswith('/challenge') for r in requests)
	assert 'private-token' not in capsys.readouterr().out


@pytest.mark.parametrize(
	'data', [{'mode': 'click'}, {'mode': 'rotate', 'id': ''}, {'mode': 'rotate', 'id': 'test-captcha', 'expires_in': 0}]
)
def test_invalid_or_unsupported_challenge_never_submits(monkeypatch, data):
	run, requests, _ = setup_session(monkeypatch, challenge=response(data))
	assert run()[0] is False
	assert all(r.method == 'GET' for r in requests)


def test_uncertain_match_never_submits(monkeypatch):
	run, requests, _ = setup_session(monkeypatch)

	def ambiguous(*args):
		raise ValueError('Rotation match is ambiguous')

	monkeypatch.setattr('captcha_ocr.rotate.solve_data_urls', ambiguous)
	assert run()[0] is False
	assert all(r.method == 'GET' for r in requests)


@pytest.mark.parametrize(
	'claim', [response({}), httpx.Response(400, text='private-token'), httpx.ReadTimeout('private-token')]
)
def test_unconfirmed_submission_is_failure_and_never_replayed(monkeypatch, capsys, claim):
	run, requests, _ = setup_session(monkeypatch, claim=claim, confirmed=PENDING)
	success, _, after = run()
	assert success is False and 'no retry' in after['check_in_error']
	assert sum(r.method == 'POST' for r in requests) == 1
	assert 'private-token' not in capsys.readouterr().out


def test_post_timeout_recovers_only_via_status_read(monkeypatch):
	run, requests, _ = setup_session(monkeypatch, claim=httpx.ReadTimeout('private-token'))
	assert run()[0] is True
	assert sum(r.method == 'POST' for r in requests) == 1
