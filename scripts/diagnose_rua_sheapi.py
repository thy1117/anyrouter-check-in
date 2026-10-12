"""Scoped, read-only recovery evidence for the two failed accounts."""

import asyncio
import json

import httpx

import checkin
from utils.browser import USERNAME_SELECTORS, _is_email_form_visible, _open_email_login_form
from utils.config import AppConfig, load_accounts_config
from utils.proxy import active_proxy_node, get_proxy_server

TARGETS = ('rua-chat-account-1', 'SheApi-thy1119')


def public_shape(response):
	try:
		payload = response.json()
	except ValueError:
		return {'http': response.status_code, 'kind': response.headers.get('content-type', '')}
	if not isinstance(payload, dict):
		return {'http': response.status_code, 'kind': 'non-object'}
	data = payload.get('data')
	shape = {'http': response.status_code, 'success': payload.get('success'), 'code': payload.get('code')}
	if isinstance(data, dict):
		shape['keys'] = sorted(data)
		if isinstance(data.get('stats'), dict):
			shape['stats'] = {
				key: value
				for key, value in data['stats'].items()
				if key in ('checked_in_today', 'total_checkins', 'total_quota', 'month_quota')
			}
		for key in ('checked_in_today', 'checked_today', 'quota', 'gift_quota', 'used_quota'):
			if key in data and type(data[key]) in (bool, int, float):
				shape[key] = data[key]
	return shape


async def main():
	config = AppConfig.load_from_env()
	accounts = load_accounts_config() or []
	selected = {account.name: account for account in accounts if account.name in TARGETS}
	assert set(selected) == set(TARGETS), 'Missing exact recovery target; refusing partial or full run'
	print('SCOPED_TARGETS=' + json.dumps(list(selected)))
	for name in TARGETS:
		account = selected[name]
		provider = config.get_provider(account.provider)
		assert provider and account.provider in ('ruachat', 'sheapi')
		if account.provider == 'ruachat':
			# Inspect the same CI browser pipeline without printing field values.
			settings = checkin.load_browser_login_settings(
				name, account.provider, persist_profile=provider.persist_profile
			)
			context = await checkin.launch_login_context(settings, use_proxy=provider.use_proxy)
			try:
				page = await context.new_page()
				await checkin.prepare_browser_page(page)
				await checkin.navigate_login_page(page, provider.domain + provider.login_path, settings.wait_timeout_ms)
				print(
					'RUA_FORM='
					+ json.dumps(
						await page.evaluate("""() => {
					function all(selector, root=document) {
						let out=[...root.querySelectorAll(selector)];
						for(const e of root.querySelectorAll('*')) if(e.shadowRoot) out.push(...all(selector,e.shadowRoot));
						return out;
					}
					return {url:location.origin+location.pathname,title:document.title,
						fields:all('input').map(e=>({name:e.name,type:e.type,visible:e.getBoundingClientRect().width>0})),
						buttons:all('button').map(e=>e.innerText.slice(0,60)),
						body:(document.body.innerText||'').slice(-700)};
				}"""),
						ensure_ascii=False,
					)
				)
				print('RUA_PLAYWRIGHT_VISIBLE=' + str(await _is_email_form_visible(page)))
				for selector in USERNAME_SELECTORS:
					locators = page.locator(selector)
					print(
						'RUA_MATCH='
						+ json.dumps(
							{
								'selector': selector,
								'count': await locators.count(),
								'first_visible': await locators.first.is_visible(),
							}
						)
					)
				try:
					await _open_email_login_form(page, 5000)
					print('RUA_OPEN_FORM=success')
				except Exception as exc:
					print('RUA_OPEN_FORM=' + type(exc).__name__)
					await page.reload(wait_until='domcontentloaded', timeout=settings.wait_timeout_ms)
					try:
						await _open_email_login_form(page, 5000)
						print('RUA_RELOADED_FORM=success')
					except Exception as second:
						print('RUA_RELOADED_FORM=' + type(second).__name__)
			finally:
				await context.close()
			continue
		# SheApi: only GET the public status endpoint; never use credentials or POST.
		# The failed run's auth fields are intentionally not replayed in this probe.
		node = checkin.resolve_account_proxy_node(account, provider, 2)
		with active_proxy_node(node, account_name=name):
			for http2 in (True, False):
				with httpx.Client(proxy=get_proxy_server(), http2=http2, trust_env=False, timeout=20) as client:
					try:
						result = public_shape(client.get(provider.domain + '/api/status'))
					except httpx.HTTPError as exc:
						result = {'transport_error': type(exc).__name__}
					print('SHE_PUBLIC=' + json.dumps({'http2': http2, **result}))
					assert account.access_token and account.api_user is not None
					headers = {
						'Authorization': 'Bearer ' + account.access_token,
						provider.api_user_key: str(account.api_user),
						'X-Requested-With': 'XMLHttpRequest',
					}
					for path in (provider.user_info_path, provider.check_in_status_path or provider.sign_in_path):
						assert path
						try:
							result = public_shape(client.get(provider.domain + path, headers=headers))
						except httpx.HTTPError as exc:
							result = {'transport_error': type(exc).__name__}
						print('SHE_STATE=' + json.dumps({'http2': http2, 'path': path, **result}))


if __name__ == '__main__':
	asyncio.run(main())
