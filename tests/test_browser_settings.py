import sys
from types import SimpleNamespace

import pytest

from utils.browser import launch_login_context, load_browser_login_settings


def test_browser_login_settings_records_profile_persistence(monkeypatch, tmp_path):
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))

	settings = load_browser_login_settings('Account 1', 'agentrouter', persist_profile=False)

	assert settings.persist_profile is False
	assert settings.profile_dir == tmp_path / 'agentrouter' / 'Account 1'


@pytest.mark.asyncio
async def test_launch_login_context_uses_persistent_context_when_enabled(monkeypatch, tmp_path):
	calls = {}
	context = SimpleNamespace()

	async def fake_launch_persistent_context_async(profile_dir, **kwargs):
		calls['profile_dir'] = profile_dir
		calls['kwargs'] = kwargs
		return context

	monkeypatch.setitem(
		sys.modules,
		'cloakbrowser',
		SimpleNamespace(launch_persistent_context_async=fake_launch_persistent_context_async),
	)

	settings = load_browser_login_settings('Account 1', 'anyrouter', persist_profile=True)
	settings = settings.__class__(
		headless=settings.headless,
		humanize=False,
		wait_timeout_ms=settings.wait_timeout_ms,
		profile_dir=tmp_path / 'profiles' / 'anyrouter' / 'Account 1',
		cloakbrowser_binary_path=settings.cloakbrowser_binary_path,
		persist_profile=settings.persist_profile,
	)

	result = await launch_login_context(settings)

	assert result is context
	assert calls['profile_dir'] == str(settings.profile_dir)
	assert 'args' not in calls['kwargs']


@pytest.mark.asyncio
async def test_vsllm_profile_uses_stable_fingerprint_per_account(monkeypatch, tmp_path):
	calls = []
	context = SimpleNamespace()

	async def fake_launch_persistent_context_async(profile_dir, **kwargs):
		calls.append({'profile_dir': profile_dir, 'kwargs': kwargs})
		return context

	monkeypatch.setitem(
		sys.modules,
		'cloakbrowser',
		SimpleNamespace(launch_persistent_context_async=fake_launch_persistent_context_async),
	)
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path / 'profiles'))

	first_account = load_browser_login_settings('Account 1', 'vsllm', persist_profile=True)
	second_account = load_browser_login_settings('Account 2', 'vsllm', persist_profile=True)

	await launch_login_context(first_account)
	await launch_login_context(first_account)
	await launch_login_context(second_account)

	fingerprints = []
	for call in calls:
		matching = [arg for arg in call['kwargs']['args'] if arg.startswith('--fingerprint=')]
		assert len(matching) == 1
		fingerprints.append(matching[0])

	assert fingerprints[0] == fingerprints[1]
	assert fingerprints[0] != fingerprints[2]


@pytest.mark.asyncio
async def test_launch_login_context_closes_browser_for_ephemeral_context(monkeypatch, tmp_path):
	class FakeContext:
		def __init__(self):
			self.closed = False

		async def close(self):
			self.closed = True

	class FakeBrowser:
		def __init__(self):
			self.context = FakeContext()
			self.closed = False
			self.context_kwargs = {}
			self.launch_kwargs = {}

		async def new_context(self, **kwargs):
			self.context_kwargs = kwargs
			return self.context

		async def close(self):
			self.closed = True

	browser = FakeBrowser()

	async def fake_launch_async(**kwargs):
		browser.launch_kwargs = kwargs
		return browser

	monkeypatch.setitem(
		sys.modules,
		'cloakbrowser',
		SimpleNamespace(launch_async=fake_launch_async),
	)

	settings = load_browser_login_settings('Account 1', 'agentrouter', persist_profile=False)
	settings = settings.__class__(
		headless=settings.headless,
		humanize=False,
		wait_timeout_ms=settings.wait_timeout_ms,
		profile_dir=tmp_path / 'profiles' / 'agentrouter' / 'Account 1',
		cloakbrowser_binary_path=settings.cloakbrowser_binary_path,
		persist_profile=settings.persist_profile,
	)

	context = await launch_login_context(settings)
	await context.close()

	assert context.closed is True
	assert browser.closed is True
	assert 'args' not in browser.launch_kwargs
	assert not settings.profile_dir.exists()
