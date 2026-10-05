"""One-off four-account diagnostics; no credential rotation or notifications."""
import asyncio
import contextlib
import io
import json
import os
import re
from urllib.parse import urlsplit

import httpx

from checkin import get_waf_cookies_with_browser, is_checked_in_status, parse_cookies, unwrap_api_data
from utils.config import AppConfig, load_accounts_config
from utils.proxy import active_proxy_node, get_proxy_server

TARGETS = {'Twinkle-Dodo', 'Twinkle-112581647'}
NODES = {'Twinkle-Dodo': 'Fast-B1-1', 'Twinkle-112581647': 'Fast-B1-2'}
SAFE = {'success', 'code', 'message', 'error', 'reason', 'detail', 'eligible', 'enabled',
        'checked_in_today', 'checked_in', 'has_checked_in', 'signedToday', 'last_checkin_date',
        'last_checkin_at', 'can_checkin', 'balance', 'quota', 'status', 'requires_2fa'}
REDACTIONS = []


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if k in SAFE or k == 'data' and isinstance(v, dict)}
    if isinstance(value, (list, tuple)):
        return '[list omitted]'
    if isinstance(value, str):
        for secret in REDACTIONS:
            value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'eyJ[\w.-]{20,}|sk-[\w-]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[REDACTED]', value)
        return value[:500]
    return value


def inspect_response(account, route, phase, response):
    response.read()
    try:
        data = response.json()
        payload = clean(data)
    except ValueError:
        text = response.text.lower()
        payload = {'html_title': re.findall(r'<title[^>]*>(.*?)</title>', text)[:1],
                   'challenge': any(w in text for w in ['just a moment', 'cf-chl-', 'challenge-platform']),
                   'html': '<html' in text}
        data = {}
    print('DIAG ' + json.dumps({'account': account, 'route': route, 'phase': phase,
                               'http': response.status_code, 'server': response.headers.get('server'),
                               'payload': payload}, ensure_ascii=False), flush=True)
    return data


def headers_for(provider):
    return {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*', 'Content-Type': 'application/json',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Origin': provider.domain, 'Referer': provider.domain + '/dashboard'}


async def test_account(account, provider, route):
    name = account.name
    proxy = get_proxy_server() if route == 'proxy' else None
    if route == 'proxy' and not proxy:
        raise RuntimeError('Missing diagnostic proxy')
    host = urlsplit(provider.domain).hostname
    if host not in {'anyrouter.top', 'superapi.buzz', 'big-model.smart-agi.com'}:
        raise RuntimeError('Unexpected provider host; refusing credential dispatch')
    with httpx.Client(proxy=proxy, trust_env=False, http2=provider.http2, timeout=30,
                      headers=headers_for(provider), follow_redirects=False) as client:
        try:
            trace = client.get(provider.domain + '/cdn-cgi/trace')
            ip = re.search(r'^ip=(.+)$', trace.text, re.M)
            print('EXIT ' + json.dumps({'account': name, 'route': route, 'target_ip': ip.group(1) if ip else None}))
        except httpx.HTTPError as e:
            print('EXIT_ERROR', name, route, type(e).__name__)
        if account.provider == 'anyrouter':
            cookies = parse_cookies(account.cookies) if account.cookies else {}
            # Reuse the production WAF-cookie acquisition, but never rotate account tokens.
            with contextlib.redirect_stdout(io.StringIO()):
                waf = await get_waf_cookies_with_browser(name, provider.domain + provider.login_path, provider.waf_cookie_names or [], use_proxy=bool(proxy))
            client.cookies.update({**(waf or {}), **cookies})
            if account.api_user:
                client.headers[provider.api_user_key] = str(account.api_user)
            if account.access_token:
                client.headers['Authorization'] = 'Bearer ' + account.access_token
            inspect_response(name, route, 'authenticated_profile_only', client.get(provider.domain + provider.user_info_path))
            return
        if not account.has_login_credentials():
            print('BLOCKED', name, 'no password login; no refresh attempted')
            return
        login_body = ({'email': account.email, 'password': account.password} if account.provider == 'twinkle'
                      else {'username': account.get_login_identifier(), 'password': account.password})
        r = client.post(provider.domain + provider.login_api_path, json=login_body)
        data = inspect_response(name, route, 'login', r)
        d = unwrap_api_data(data)
        if r.status_code != 200 or not isinstance(d, dict) or d.get('requires_2fa'):
            return
        token = d.get('access_token')
        if not token:
            print('BLOCKED', name, route, 'login did not return access token')
            return
        REDACTIONS.append(token)
        client.headers['Authorization'] = 'Bearer ' + token
        user = d.get('user') or {}
        if provider.api_user_key and user.get('id') is not None:
            client.headers[provider.api_user_key] = str(user['id'])
        profile = client.get(provider.domain + provider.user_info_path)
        inspect_response(name, route, 'profile', profile)
        if profile.status_code != 200:
            return
        status_url = provider.domain + (provider.check_in_status_path or provider.sign_in_path)
        if account.provider == 'superapi':
            from datetime import datetime
            from zoneinfo import ZoneInfo
            status_url += '?month=' + datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m')
        status = client.get(status_url)
        payload = inspect_response(name, route, 'checkin_status', status)
        # Only replay the original failed Twinkle write once, on its original proxy.
        # Direct leg is read-only after login; never POST when state is unavailable.
        if account.provider == 'twinkle' and route == 'proxy' and status.status_code == 200:
            if not is_checked_in_status(unwrap_api_data(payload)):
                result = client.post(provider.domain + provider.sign_in_path)
                inspect_response(name, route, 'single_checkin_attempt', result)
                inspect_response(name, route, 'post_checkin_status', client.get(status_url))
        if account.provider == 'superapi':
            sid = (d.get('session') or {}).get('sid')
            if sid:
                client.headers['X-Auth-Session'] = sid
            inspect_response(name, route, 'logout_own_temporary_session', client.post(provider.domain + '/api/user/auth/logout'))


async def main():
    with contextlib.redirect_stdout(io.StringIO()):
        cfg = AppConfig.load_from_env()
        accounts = load_accounts_config() or []
    selected = {a.name: a for a in accounts if a.name in TARGETS}
    assert set(selected) == TARGETS and all(a.provider == 'twinkle' for a in selected.values()), 'Target account missing; no requests dispatched'
    for a in selected.values():
        REDACTIONS.extend(str(v) for v in [a.password, a.access_token, a.refresh_token, a.session_id, a.email] if v)
        if isinstance(a.cookies, dict):
            REDACTIONS.extend(str(v) for v in a.cookies.values() if v)
    print('SCOPE', sorted(selected))
    for name in sorted(selected):
        a = selected[name]
        p = cfg.get_provider(a.provider)
        assert p is not None
        print('CONFIG', json.dumps({'account': name, 'provider': a.provider, 'use_proxy': p.use_proxy,
                                     'node': a.proxy_node, 'has_cookie': bool(a.cookies),
                                     'has_access_token': bool(a.access_token), 'has_login': a.has_login_credentials()}))
        for route in ('proxy',):
            node = NODES.get(name, 'Fast-B1-1') if route == 'proxy' else None
            try:
                with active_proxy_node(node, account_name=name):
                    await test_account(a, p, route)
            except Exception as e:
                print('DIAG_ERROR', name, route, type(e).__name__, flush=True)
    print('DIAGNOSTIC_COMPLETE; production config and secrets unchanged')


if __name__ == '__main__':
    asyncio.run(main())
