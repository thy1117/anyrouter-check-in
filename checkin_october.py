"""Run the October 4 engine with current credentials and explicit site adapters.

The frozen source is kept separately so the rollback can be inspected against
852253d9befa060d2d14a941da110fa45fc22706 without rewriting current diagnostics.
"""

import os

import checkin as current
from legacy import checkin_20261004 as october
from utils.config import AccountConfig
from utils.notification_layout import group_account_report

BASELINE_COMMIT = '852253d9befa060d2d14a941da110fa45fc22706'
BASELINE_DATE = '2026-10-04'
OCTOBER_PROVIDERS = frozenset(
	{
		'anyrouter',
		'agentrouter',
		'futureppo',
		'twinkle',
		'42w',
		'kapibala',
		'qingjiu',
		'nianhua',
		'sheapi',
		'aiaiai',
		'guyscode',
		'xiaobai',
		'ciyuan',
		'yuecheng',
		'elysiver',
		'windhub',
		'gorouter',
		'justwoker',
		'tabitoken',
		'laomo',
		'nhh123',
		'llmpm',
		'xiaojimao',
		'superapi',
		'gemai',
		'motomoto',
		'ruachat',
	}
)

# Save the original function before installing this module's routing adapter.
_october_dispatch = october._run_account_checkin
_october_requests = october.run_check_in_requests


def usable_balance(info):
	"""Older reporting assumes both balance fields exist; unavailable is not zero."""
	if current.get_available_balance(info) is None:
		return None
	try:
		used = current._balance_amount(info.get('used_quota'))
	except ValueError:
		return None
	return {**info, 'quota': current.get_available_balance(info), 'used_quota': used}


async def dispatch_account(account, account_name, provider):
	"""Use October for legacy sites; retain required post-October API contracts."""
	if (
		provider.name not in OCTOBER_PROVIDERS
		or provider.name == 'twinkle'
		or provider.api_style == 'xiaobai'
		or provider.login_in_page
		or provider.checkin_turnstile
	):
		result = await current._run_account_checkin(account, account_name, provider)
	else:
		result = await _october_dispatch(account, account_name, provider)
	success, before, after = result
	# Preserve the original provider outcome even if a supplementary balance read
	# failed. Keep errors available to the historical notification builder.
	return success, usable_balance(before) or _without_balance(before), usable_balance(after) or _without_balance(after)


def _without_balance(info):
	if info is None:
		return None
	return {key: value for key, value in info.items() if key not in ('quota', 'used_quota')} | {'success': False}


def load_current_accounts():
	accounts = current.load_accounts_config()
	target = os.getenv('CHECKIN_TARGET_ACCOUNTS', '').strip()
	if accounts and target:
		accounts = current.filter_accounts_by_target(accounts, target)
	if isinstance(october.notify, GroupedNotifications):
		october.notify.accounts = accounts or []
	return accounts


class GroupedNotifications:
	def __init__(self, sender):
		self.sender = sender
		self.accounts: list[AccountConfig] = []

	def push_message(self, title, content, msg_type='text'):
		return self.sender.push_message(title, group_account_report(content, self.accounts), msg_type=msg_type)


class TargetedNotifications:
	def push_message(self, *args, **kwargs):
		print('[INFO] Manual targeted account run: external push notification skipped')
		return {}


def dispatch_http(
	all_cookies: dict,
	account: AccountConfig,
	account_name: str,
	provider_config,
	*,
	api_user_override: str | None = None,
	access_token_override: str | None = None,
	session_id_override: str | None = None,
	use_proxy: bool = False,
) -> tuple[bool, dict | None, dict | None]:
	"""Keep SheApi's current PAT handling within the October HTTP dispatch."""
	runner = current.run_check_in_requests if provider_config.name == 'sheapi' else _october_requests
	return runner(
		all_cookies,
		account,
		account_name,
		provider_config,
		api_user_override=api_user_override,
		access_token_override=access_token_override,
		session_id_override=session_id_override,
		use_proxy=use_proxy,
	)


def configure_engine():
	"""Install bounded compatibility changes into the frozen engine only."""
	october._run_account_checkin = dispatch_account
	october.load_accounts_config = load_current_accounts
	# These parsers reject absent/invalid balance data, instead of making it $0.
	october.newapi_quota_pair = current.newapi_quota_pair
	october.get_user_info = current.get_user_info
	october.parse_user_info_response = current.parse_user_info_response
	october.parse_sub2api_profile_response = current.parse_sub2api_profile_response
	october.format_check_in_notification = current.format_check_in_notification
	# Some sites added checked_in after October; an affirmative daily status
	# must still stop a duplicate submission when running the older engine.
	october.is_checked_in_status = current.is_checked_in_status
	# A retired SheApi PAT must stop before refresh/CAPTCHA submission. Other
	# October HTTP providers keep the original browser/WAF and HTTP sequence.
	october.run_check_in_requests = dispatch_http
	if os.getenv('CHECKIN_TARGET_ACCOUNTS', '').strip():
		# This intentionally installs a smaller notification interface for a
		# targeted run; it does not instantiate or configure a notification kit.
		setattr(october, 'notify', TargetedNotifications())
		october.save_balance_hash = lambda value: None
	else:
		setattr(october, 'notify', GroupedNotifications(current.notify))
		october.save_balance_hash = current.save_balance_hash


def run_main():
	print(f'[ROLLBACK] October engine: {BASELINE_DATE} ({BASELINE_COMMIT[:7]}); current account Secrets retained')
	configure_engine()
	october.run_main()


if __name__ == '__main__':
	run_main()
