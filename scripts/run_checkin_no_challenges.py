#!/usr/bin/env python3
"""One full original run, failing closed instead of solving site challenges."""

import sys
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from pathlib import Path
from unittest.mock import patch

# Support the workflow's direct `python scripts/run_checkin_no_challenges.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_CURRENT: ContextVar[dict | None] = ContextVar('no_challenges_account', default=None)


class ChallengeBlocked(RuntimeError):
	"""A locally generated, credential-free failure at a challenge boundary."""


def deny(*args, **kwargs):
	state = _CURRENT.get()
	if state is not None:
		state['blocked'] = True
		if state['submitted']:
			raise ChallengeBlocked('网站验证已阻止；签到已提交但结果未确认 (submitted_unconfirmed)，未重复提交')
	raise ChallengeBlocked('网站验证已阻止；签到未提交 (not_submitted)，请手动完成')


@contextmanager
def no_challenges(runner):
	original_account = runner._run_account_checkin
	original_info = runner.get_user_info
	original_send = runner.httpx.Client.send
	original_parse_profile = runner.parse_sub2api_profile_response

	def parse_profile(*args, **kwargs):
		info = original_parse_profile(*args, **kwargs)
		state = _CURRENT.get()
		if state is not None and info.get('success') is True:
			state['info'] = info
		return info

	def send(client, request, *args, **kwargs):
		state = _CURRENT.get()
		if state is not None and state['blocked']:
			deny()
		if state is not None and request.method != 'GET' and str(request.url).split('?', 1)[0] == state['sign_in_url']:
			state['submitted'] = True
		response = original_send(client, request, *args, **kwargs)
		if state is not None:
			response.read()
			body = response.text.lower()
			if 'text/html' in response.headers.get('content-type', '').lower() or any(
				marker in body
				for marker in ('<html', '<!doctype html', '/_guard/auto.js', 'cf-chl-', 'challenge-platform')
			):
				# Let the original parser classify a later balance-only read as
				# unavailable, preserving its already-made business decision.
				if (
					request.method == 'GET'
					and str(request.url).split('?', 1)[0] == state['profile_url']
					and state['info']
				):
					state['blocked'] = True
					state['balance_only'] = True
					return response
				response.close()
				deny()
		return response

	def read_info(*args, **kwargs):
		info = original_info(*args, **kwargs)
		state = _CURRENT.get()
		if state is not None and info and info.get('success') is True:
			state['info'] = info
		return info

	async def account(*args, **kwargs):
		state = {
			'blocked': False,
			'balance_only': False,
			'info': None,
			'submitted': False,
			'profile_url': f'{args[2].domain}{args[2].user_info_path}'.split('?', 1)[0],
			'sign_in_url': f'{args[2].domain}{args[2].sign_in_path}'.split('?', 1)[0],
		}
		token = _CURRENT.set(state)
		try:
			try:
				result = await original_account(*args, **kwargs)
			except ChallengeBlocked:
				result = False, None, None
			if state['blocked']:
				success, before, after = result
				before = before or state['info']
				phase = 'submitted_unconfirmed' if state['submitted'] else 'not_submitted'
				if state['balance_only']:
					phase = (
						('provider_success' if state['submitted'] else 'already_confirmed')
						if success
						else 'provider_failure'
					)
					after = dict(after or {})
					after['balance_read_error'] = 'ChallengeBlocked'
					error = '后续余额读取被网站验证阻止；原接口最终判定保留，未重复提交'
				elif success:
					phase = 'provider_success' if state['submitted'] else 'already_confirmed'
					after = {'success': False, 'balance_read_error': 'ChallengeBlocked'}
					error = '原接口已确认成功；后续余额读取被网站验证阻止，未重复提交'
				elif state['submitted']:
					error = '网站验证已阻止；签到已提交但结果未确认 (submitted_unconfirmed)，未重复提交'
					after = runner.attach_check_in_error(None, error) or {}
				else:
					error = '网站验证已阻止；签到未提交 (not_submitted)，请手动完成'
					after = runner.attach_check_in_error(before or after, error) or {}
					success = False
				after.update(challenge_blocked=True, check_in_phase=phase, check_in_submitted=state['submitted'])
				print(f'[BLOCKED] {args[1]}: {error}')
				result = success, before, after
			return result
		finally:
			_CURRENT.reset(token)

	with ExitStack() as guards:
		guards.enter_context(patch.object(runner.httpx.Client, 'send', send))
		# Guard bound imports and lazy cloakbrowser imports; no browser is ever launched.
		for module in (
			runner,
			sys.modules['utils.browser'],
			sys.modules['cloakbrowser'],
			sys.modules['cloakbrowser.browser'],
		):
			for name in tuple(vars(module)):
				if name.startswith(('launch', 'solve_', 'reset_turnstile')) or name in {
					'get_waf_cookies_with_browser',
					'login_with_credentials',
					'navigate_login_page',
					'run_twinkle_check_in',
					'run_gorouter_check_in_in_page',
					'run_newapi_login_check_in_in_page',
					'run_check_in_in_page',
					'_check_in_in_authenticated_page',
					'request_in_page',
				}:
					guards.enter_context(patch.object(module, name, deny))
		for name, replacement in (
			('SafeLineAuth', lambda: None),
			('solve_safeline_guard', deny),
			('execute_captcha_check_in_result', deny),
			('get_user_info', read_info),
			('parse_sub2api_profile_response', parse_profile),
			('_run_account_checkin', account),
		):
			guards.enter_context(patch.object(runner, name, replacement))
		yield


def main():
	import checkin

	with no_challenges(checkin):
		checkin.run_main()


if __name__ == '__main__':
	main()
