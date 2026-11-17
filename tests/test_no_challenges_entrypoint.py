import importlib
from unittest.mock import Mock

import httpx
import pytest

import checkin
from utils.config import AccountConfig, AppConfig, ProviderConfig


def entrypoint():
	try:
		return importlib.import_module('scripts.run_checkin_no_challenges')
	except ModuleNotFoundError as exc:
		if exc.name == 'scripts.run_checkin_no_challenges':
			pytest.fail('The fail-closed full-run entrypoint is missing')
		raise


@pytest.fixture
def offline_run(monkeypatch, tmp_path):
	for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
		monkeypatch.delenv(key, raising=False)
	monkeypatch.setenv('CHECKIN_TARGET_ACCOUNTS', '')
	monkeypatch.setenv('CHECKIN_DAILY_STATE_FILE', '')
	monkeypatch.setenv('CHECKIN_PROXY_URL', '')
	monkeypatch.setenv('NOTIFY_EVERY_RUN', 'true')
	monkeypatch.setenv('DEBUG_MODE', 'false')
	monkeypatch.setattr(checkin, 'BALANCE_HASH_FILE', str(tmp_path / 'balance_hash.txt'))
	monkeypatch.setattr(checkin, 'format_check_in_time', lambda: '2026-11-17 18:00:00')
	sender = Mock(return_value={'Telegram': True})
	monkeypatch.setattr(checkin.notify, 'push_message', sender)
	return sender


def test_full_runner_blocks_captcha_before_ocr_import_and_keeps_original_success(monkeypatch, offline_run):
	providers = {
		name: ProviderConfig(
			name=name,
			domain=f'https://{name}.invalid',
			use_proxy=False,
			checkin_captcha=name == 'captcha-provider',
		)
		for name in ('plain', 'captcha-provider', 'anyrouter')
	}
	accounts = [
		AccountConfig(cookies={'session': 'fake-session'}, provider=name, name=name, api_user=str(index + 1))
		for index, name in enumerate(providers)
	]
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: AppConfig(providers=providers))
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: accounts)
	requests = []

	def handler(request):
		requests.append((request.url.host, request.method, request.url.path))
		assert request.url.host in {f'{name}.invalid' for name in providers}
		assert request.headers['cookie'] == 'session=fake-session'
		if request.method == 'GET':
			assert request.url.path == '/api/user/self'
			return httpx.Response(
				200,
				json={'success': True, 'data': {'id': int(request.headers['new-api-user']), 'quota': 500000}},
			)
		assert request.url.host == 'plain.invalid' and request.url.path == '/api/user/sign_in'
		return httpx.Response(200, json={'success': True})

	monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', httpx.MockTransport(handler).handle_request)
	import_builtin = __import__
	ocr_imports = []

	def reject_ocr_import(name, *args, **kwargs):
		if name.startswith('captcha_ocr'):
			ocr_imports.append(name)
			pytest.fail('No-challenges mode reached the CAPTCHA/OCR import boundary')
		return import_builtin(name, *args, **kwargs)

	monkeypatch.setattr('builtins.__import__', reject_ocr_import)
	outcomes = []
	original_account_runner = checkin.check_in_account

	async def observe(*args, **kwargs):
		result = await original_account_runner(*args, **kwargs)
		outcomes.append((args[0].provider, result))
		return result

	monkeypatch.setattr(checkin, 'check_in_account', observe)
	with pytest.raises(SystemExit) as result:
		entrypoint().main()

	assert result.value.code == 1
	assert [name for name, _ in outcomes] == list(providers)
	assert [outcome[0] for _, outcome in outcomes] == [True, False, True]
	blocked = outcomes[1][1][2]
	assert blocked['check_in_phase'] == 'not_submitted'
	assert blocked['check_in_submitted'] is False
	assert blocked['challenge_blocked'] is True
	assert blocked['quota'] == 1.0
	assert not ocr_imports
	assert requests == [
		('plain.invalid', 'GET', '/api/user/self'),
		('plain.invalid', 'POST', '/api/user/sign_in'),
		('plain.invalid', 'GET', '/api/user/self'),
		('captcha-provider.invalid', 'GET', '/api/user/self'),
		('anyrouter.invalid', 'GET', '/api/user/self'),
	]
	offline_run.assert_called_once()
	message = offline_run.call_args.args[1]
	assert '签到结果：2/3 成功' in message
	assert 'captcha-provider' in message and 'not_submitted' in message
	assert '签到 +$' not in message
	assert 'fake-session' not in message


def test_safeline_html_stops_the_original_http_path_without_auth_solver_or_replay(monkeypatch, offline_run):
	providers = {
		name: ProviderConfig(name=name, domain=f'https://{name}.invalid', use_proxy=False)
		for name in ('safeline', 'plain')
	}
	accounts = [
		AccountConfig(cookies=None, access_token='fake-pat', api_user='7', provider=name, name=name)
		for name in providers
	]
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: AppConfig(providers=providers))
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: accounts)

	def forbidden(*args, **kwargs):
		pytest.fail('The original SafeLineAuth or SafeLine solver must never execute')

	monkeypatch.setattr(checkin.SafeLineAuth, '__init__', forbidden)
	monkeypatch.setattr(checkin, 'solve_safeline_guard', forbidden)
	requests = []

	def handler(request):
		requests.append((request.url.host, request.method))
		assert 'guardret=' not in request.headers.get('cookie', '')
		if request.url.host == 'safeline.invalid':
			assert request.method == 'GET'
			return httpx.Response(
				200,
				text='<html>success <script src="/_guard/auto.js"></script></html>',
				headers={'content-type': 'text/html', 'set-cookie': 'guard=fake-guard; Path=/'},
			)
		if request.method == 'POST':
			return httpx.Response(200, json={'success': True})
		return httpx.Response(200, json={'success': True, 'data': {'quota': 500000}})

	monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', httpx.MockTransport(handler).handle_request)
	outcomes = []
	original_account = checkin.check_in_account

	async def observe(*args, **kwargs):
		result = await original_account(*args, **kwargs)
		outcomes.append(result)
		return result

	monkeypatch.setattr(checkin, 'check_in_account', observe)
	with pytest.raises(SystemExit) as result:
		entrypoint().main()

	assert result.value.code == 1
	assert [outcome[0] for outcome in outcomes] == [False, True]
	assert outcomes[0][2]['challenge_blocked'] is True
	assert outcomes[0][2]['check_in_phase'] == 'not_submitted'
	assert outcomes[0][2]['check_in_submitted'] is False
	assert requests == [
		('safeline.invalid', 'GET'),
		('plain.invalid', 'GET'),
		('plain.invalid', 'POST'),
		('plain.invalid', 'GET'),
	]
	message = offline_run.call_args.args[1]
	assert '签到结果：1/2 成功' in message
	assert 'safeline' in message and 'not_submitted' in message
	assert 'fake-pat' not in message and 'fake-guard' not in message


@pytest.mark.parametrize('branch', ['twinkle', 'login_in_page', 'turnstile', 'in_page', 'waf', 'browser_login'])
def test_browser_branches_are_blocked_before_launch_navigation_or_credential_fallback(
	monkeypatch,
	offline_run,
	branch,
):
	provider = ProviderConfig(
		name='twinkle' if branch == 'twinkle' else branch,
		domain='https://browser-required.invalid',
		use_proxy=False,
		login_api_path='/api/user/login' if branch == 'login_in_page' else None,
		login_in_page=branch == 'login_in_page',
		checkin_turnstile=branch == 'turnstile',
		request_in_page=branch == 'in_page',
		bypass_method='waf_cookies' if branch == 'waf' else None,
		waf_cookie_names=['cf_clearance'] if branch == 'waf' else [],
	)
	account = AccountConfig(
		cookies={'session': 'fake-cookie-fallback'},
		provider=provider.name,
		name=branch,
		access_token='fake-pat',
		api_user='5',
		email='fake@example.invalid' if branch in ('login_in_page', 'browser_login') else None,
		password='fake-password' if branch in ('login_in_page', 'browser_login') else None,
	)
	app = AppConfig(providers={provider.name: provider})
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: app)
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: [account])

	def forbidden(*args, **kwargs):
		pytest.fail('Browser launch/navigation, Turnstile or an HTTP credential fallback was invoked')

	for module in (checkin, importlib.import_module('utils.browser')):
		for name in ('launch_async', 'launch_login_context', 'navigate_login_page', 'solve_turnstile_in_page'):
			if hasattr(module, name):
				monkeypatch.setattr(module, name, forbidden)
	monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
	outcomes = []
	original_account = checkin.check_in_account

	async def observe(*args, **kwargs):
		result = await original_account(*args, **kwargs)
		outcomes.append(result)
		return result

	monkeypatch.setattr(checkin, 'check_in_account', observe)
	with pytest.raises(SystemExit) as result:
		entrypoint().main()

	assert result.value.code == 1
	assert len(outcomes) == 1 and outcomes[0][0] is False
	after = outcomes[0][2]
	assert after['challenge_blocked'] is True
	assert after['check_in_phase'] == 'not_submitted'
	assert after['check_in_submitted'] is False
	assert 'quota' not in after
	message = offline_run.call_args.args[1]
	assert '签到结果：0/1 成功' in message
	assert branch in message and 'not_submitted' in message
	assert 'fake-password' not in message and 'fake-pat' not in message


@pytest.mark.parametrize('challenge_at', ['submission', 'balance_read'])
def test_challenge_after_post_is_never_mislabeled_not_submitted(monkeypatch, offline_run, challenge_at):
	provider = ProviderConfig(name='plain', domain='https://plain.invalid', use_proxy=False)
	account = AccountConfig(cookies=None, provider='plain', name='plain', access_token='fake-pat', api_user='8')
	app = AppConfig(providers={'plain': provider})
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: app)
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: [account])
	requests = []

	def handler(request):
		requests.append(request.method)
		if (challenge_at == 'submission' and request.method == 'POST') or (
			challenge_at == 'balance_read' and len(requests) == 3
		):
			return httpx.Response(200, text='<html>success /_guard/auto.js</html>')
		if request.method == 'POST':
			return httpx.Response(200, json={'success': True})
		return httpx.Response(200, json={'success': True, 'data': {'quota': 500000}})

	monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', httpx.MockTransport(handler).handle_request)
	outcomes = []
	original_account = checkin.check_in_account

	async def observe(*args, **kwargs):
		result = await original_account(*args, **kwargs)
		outcomes.append(result)
		return result

	monkeypatch.setattr(checkin, 'check_in_account', observe)
	with pytest.raises(SystemExit) as result:
		entrypoint().main()

	success, before, after = outcomes[0]
	assert success is (challenge_at == 'balance_read')
	assert result.value.code == (0 if success else 1)
	assert before['quota'] == 1.0
	assert after['challenge_blocked'] is True
	assert after['check_in_submitted'] is True
	assert after['check_in_phase'] == ('provider_success' if success else 'submitted_unconfirmed')
	assert 'quota' not in after
	assert requests == (['GET', 'POST', 'GET'] if success else ['GET', 'POST'])
	message = offline_run.call_args.args[1]
	assert 'not_submitted' not in message
	assert '签到 +$' not in message


def test_one_full_original_run_visits_all_88_indices_and_restores_guards(monkeypatch, offline_run):
	providers = {
		name: ProviderConfig(name=name, domain=f'https://{name}.invalid', use_proxy=False)
		for name in ('plain', 'twinkle')
	}
	accounts = [
		AccountConfig(
			cookies=None,
			access_token='fake-pat',
			api_user=str(index + 1),
			provider='plain' if index % 2 == 0 else 'twinkle',
			name=f'account-{index:02d}',
		)
		for index in range(88)
	]
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: AppConfig(providers=providers))
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: accounts)
	requests = []

	def handler(request):
		requests.append((request.method, request.headers['new-api-user']))
		assert request.url.host == 'plain.invalid'
		if request.method == 'POST':
			return httpx.Response(200, json={'success': True})
		return httpx.Response(200, json={'success': True, 'data': {'quota': 500000}})

	monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', httpx.MockTransport(handler).handle_request)
	visited = []
	original_account = checkin.check_in_account
	original_main = checkin.run_main
	original_auth = checkin.SafeLineAuth
	original_launch = checkin.launch_async

	async def observe(account, index, app_config, **kwargs):
		result = await original_account(account, index, app_config, **kwargs)
		visited.append((index, kwargs['provider_account_index'], result))
		return result

	monkeypatch.setattr(checkin, 'check_in_account', observe)
	main_calls = []

	def run_original_main():
		main_calls.append(True)
		original_main()

	monkeypatch.setattr(checkin, 'run_main', run_original_main)
	with pytest.raises(SystemExit) as result:
		entrypoint().main()

	assert result.value.code == 1
	assert main_calls == [True]
	assert len(visited) == 88
	assert [index for index, _, _ in visited] == list(range(88))
	assert [provider_index for _, provider_index, _ in visited] == [index // 2 for index in range(88)]
	assert [outcome[0] for _, _, outcome in visited] == [index % 2 == 0 for index in range(88)]
	assert all(outcome[2]['check_in_phase'] == 'not_submitted' for index, _, outcome in visited if index % 2)
	assert len(requests) == 132
	assert checkin.SafeLineAuth is original_auth and checkin.launch_async is original_launch
	offline_run.assert_called_once()
	assert '签到结果：44/88 成功' in offline_run.call_args.args[1]


@pytest.mark.parametrize('already', [False, True])
def test_bearer_confirmed_success_survives_blocked_later_balance(monkeypatch, offline_run, already):
	provider = ProviderConfig(
		name='bearer',
		domain='https://bearer.invalid',
		api_style='sub2api',
		use_proxy=False,
		user_info_path='/profile',
		sign_in_path='/daily',
		check_in_status_path='/daily',
	)
	account = AccountConfig(cookies=None, provider='bearer', name='bearer', access_token='fake-pat', api_user='8')
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: AppConfig(providers={'bearer': provider}))
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: [account])
	monkeypatch.setattr(checkin, 'create_xiaobai_token_state', lambda *args: None)
	requests = []
	profiles = []

	def handler(request):
		requests.append((request.method, request.url.path))
		if request.url.path == '/profile':
			profiles.append(True)
			if len(profiles) > 1:
				return httpx.Response(200, text='<html>/_guard/auto.js</html>')
			return httpx.Response(200, json={'code': 0, 'data': {'id': 8, 'balance': 1.0}})
		if request.method == 'POST':
			return httpx.Response(200, json={'success': True})
		return httpx.Response(200, json={'code': 0, 'data': {'checked_in_today': already}})

	monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', httpx.MockTransport(handler).handle_request)
	outcomes = []
	original_account = checkin.check_in_account

	async def observe(*args, **kwargs):
		result = await original_account(*args, **kwargs)
		outcomes.append(result)
		return result

	monkeypatch.setattr(checkin, 'check_in_account', observe)
	with pytest.raises(SystemExit) as result:
		entrypoint().main()
	assert result.value.code == 0
	success, before, after = outcomes[0]
	assert success is True and before['quota'] == 1.0
	assert after['check_in_phase'] == ('already_confirmed' if already else 'provider_success')
	assert after['check_in_submitted'] is (not already)
	assert 'quota' not in after and after['balance_read_error'] == 'ChallengeBlocked'
	assert sum(method == 'POST' for method, _ in requests) == int(not already)
	assert '签到结果：1/1 成功' in offline_run.call_args.args[1]
	assert '签到 +$' not in offline_run.call_args.args[1]


@pytest.mark.parametrize('provider_name', ['livelove', 'mtnapi'])
@pytest.mark.parametrize('confirmed', [False, True])
def test_special_bearer_requires_status_confirmation_before_preserving_success(
	monkeypatch, offline_run, provider_name, confirmed
):
	provider = ProviderConfig(
		name=provider_name,
		domain='https://special.invalid',
		api_style='sub2api',
		use_proxy=False,
		user_info_path='/profile',
		sign_in_path='/daily',
		check_in_status_path='/daily',
	)
	account = AccountConfig(cookies=None, provider=provider_name, name=provider_name, access_token='fake-pat')
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: AppConfig(providers={provider_name: provider}))
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: [account])
	monkeypatch.setattr(checkin, 'create_xiaobai_token_state', lambda *args: None)
	requests = []
	status_calls = []
	profiles = []

	def handler(request):
		requests.append((request.method, request.url.path))
		if request.url.path == '/profile':
			profiles.append(True)
			if len(profiles) > 1:
				return httpx.Response(200, text='<html>/_guard/auto.js</html>')
			return httpx.Response(200, json={'code': 0, 'data': {'balance': 1.0}})
		if request.method == 'POST':
			return httpx.Response(200, json={'success': True})
		status_calls.append(True)
		if len(status_calls) > 1 and not confirmed:
			return httpx.Response(200, text='<html>/_guard/auto.js</html>')
		checked = len(status_calls) > 1 and confirmed
		status = (
			{'checked_in': checked, 'enabled': True, 'eligible': True}
			if provider_name == 'livelove'
			else {'claimed': checked}
		)
		return httpx.Response(200, json={'code': 0, 'data': status})

	monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', httpx.MockTransport(handler).handle_request)
	outcomes = []
	original_account = checkin.check_in_account

	async def observe(*args, **kwargs):
		outcome = await original_account(*args, **kwargs)
		outcomes.append(outcome)
		return outcome

	monkeypatch.setattr(checkin, 'check_in_account', observe)
	with pytest.raises(SystemExit) as result:
		entrypoint().main()
	assert result.value.code == (0 if confirmed else 1)
	assert outcomes[0][0] is confirmed
	assert outcomes[0][2]['check_in_phase'] == ('provider_success' if confirmed else 'submitted_unconfirmed')
	assert sum(method == 'POST' for method, _ in requests) == 1


@pytest.mark.parametrize('case', ['status_reversal', 'exchange_rejected', 'livelove_minimal_confirmation'])
def test_blocked_balance_preserves_exact_original_business_decision(monkeypatch, offline_run, case):
	name = 'livelove' if case == 'livelove_minimal_confirmation' else 'sidrune'
	provider = ProviderConfig(
		name=name,
		domain='https://phase.invalid',
		api_style='sub2api',
		use_proxy=False,
		user_info_path='/profile',
		sign_in_path='/daily',
		check_in_status_path='/daily',
	)
	account = AccountConfig(cookies=None, provider=name, name=name, access_token='fake-pat')
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: AppConfig(providers={name: provider}))
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: [account])
	monkeypatch.setattr(checkin, 'create_xiaobai_token_state', lambda *args: None)
	requests = []
	profiles = []
	statuses = []

	def handler(request):
		requests.append((request.method, request.url.path))
		if request.url.path == '/profile':
			profiles.append(True)
			if len(profiles) > 1:
				return httpx.Response(200, text='<html>/_guard/auto.js</html>')
			return httpx.Response(200, json={'code': 0, 'data': {'balance': 1.0}})
		if request.url.path == checkin.SIDRUNE_WELFARE_EXCHANGE_PATH:
			return httpx.Response(400, json={'success': False})
		if request.method == 'POST':
			return httpx.Response(200, json={'success': True})
		statuses.append(True)
		data: dict[str, object]
		if name == 'livelove':
			data = (
				{'checked_in': False, 'enabled': True, 'eligible': True} if len(statuses) == 1 else {'checked_in': True}
			)
		else:
			checked = len(statuses) == 1 if case == 'status_reversal' else len(statuses) > 1
			data = {'checked_in_today': checked, 'exchange_unlocked': case == 'exchange_rejected', 'balance': 1.0}
		return httpx.Response(200, json={'code': 0, 'data': data})

	monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', httpx.MockTransport(handler).handle_request)
	outcomes = []
	original_account = checkin.check_in_account

	async def observe(*args, **kwargs):
		outcome = await original_account(*args, **kwargs)
		outcomes.append(outcome)
		return outcome

	monkeypatch.setattr(checkin, 'check_in_account', observe)
	with pytest.raises(SystemExit) as result:
		entrypoint().main()
	expected_success = case == 'livelove_minimal_confirmation'
	assert outcomes[0][0] is expected_success
	assert result.value.code == (0 if expected_success else 1)
	if not expected_success:
		message = offline_run.call_args.args[1]
		assert ('not confirmed' if case == 'status_reversal' else 'exchange failed - HTTP 400') in message
	assert sum(method == 'POST' and path == '/daily' for method, path in requests) == int(case != 'status_reversal')
