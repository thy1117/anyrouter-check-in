import base64
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from utils.config import AccountConfig
from utils.xiaobai_token_state import TokenStateError, XiaobaiTokenState, create_xiaobai_token_state


class GitHubStore:
	def __init__(self):
		self.blobs = {}
		self.puts = []
		self.deny_writes = False
		self.deny_reads = False
		self.lose_ack = False
		self.always_503 = False

	def __call__(self, request):
		path = request.url.path
		assert request.url.host == 'api.github.com'
		assert request.headers['Authorization'] == 'Bearer repo-scoped-test-token'
		if request.method == 'GET':
			if self.deny_reads:
				return httpx.Response(403)
			if path not in self.blobs:
				return httpx.Response(404)
			sha, ciphertext = self.blobs[path]
			return httpx.Response(200, json={'sha': sha, 'content': base64.b64encode(ciphertext).decode()})
		assert request.method == 'PUT'
		body = json.loads(request.content)
		self.puts.append(body)
		assert body['branch'] == 'checkin-token-state'
		if self.deny_writes:
			return httpx.Response(403)
		if self.always_503:
			return httpx.Response(503)
		old = self.blobs.get(path)
		if (old[0] if old else None) != body.get('sha'):
			return httpx.Response(409)
		ciphertext = base64.b64decode(body['content'])
		sha = hashlib.sha256(ciphertext).hexdigest()
		self.blobs[path] = (sha, ciphertext)
		if self.lose_ack:
			self.lose_ack = False
			raise httpx.ReadTimeout('Response lost', request=request)
		return httpx.Response(200 if old else 201, json={'content': {'sha': sha}})


@pytest.fixture
def context(monkeypatch, tmp_path):
	monkeypatch.chdir(tmp_path)
	monkeypatch.setattr('utils.xiaobai_token_state.time.sleep', lambda *_: None)
	key = Fernet.generate_key().decode()
	api = GitHubStore()

	def factory(access='bootstrap-access', refresh='bootstrap-refresh', name='小白-1', use_key=None):
		client = httpx.Client(
			base_url='https://api.github.com',
			headers={'Authorization': 'Bearer repo-scoped-test-token'},
			transport=httpx.MockTransport(api),
		)
		return XiaobaiTokenState(
			AccountConfig(cookies=None, provider='xiaobai', name=name, access_token=access, refresh_token=refresh),
			'https://token.dialoguedui.com',
			use_key or key,
			'repo-scoped-test-token',
			'owner/repo',
			client=client,
		)

	return key, api, factory


def test_rotations_survive_next_run_with_old_secret(context):
	key, api, factory = context
	first = factory()
	assert first.load() == ('bootstrap-access', 'bootstrap-refresh')
	first.save('bootstrap-access', 'bootstrap-refresh', pending=True)
	first.save('new-access', 'new-refresh')
	second = factory()
	assert second.load() == ('new-access', 'new-refresh')
	assert not list(Path('.xiaobai-token-recovery').glob('*.fernet'))
	for _, ciphertext in api.blobs.values():
		assert b'new-access' not in ciphertext
		assert b'new-refresh' not in ciphertext
		assert json.loads(Fernet(key).decrypt(ciphertext))['tokens'] == ['new-access', 'new-refresh']


def test_manual_secret_update_supersedes_old_state(context):
	_, _, factory = context
	old = factory()
	old.load()
	old.save('old-session', 'old-session-refresh')
	updated = factory('fresh-secret-access', 'fresh-secret-refresh')
	assert updated.load() == ('fresh-secret-access', 'fresh-secret-refresh')


def test_pending_refresh_fails_closed_on_next_run(context):
	_, _, factory = context
	first = factory()
	first.load()
	first.save('bootstrap-access', 'bootstrap-refresh', pending=True)
	with pytest.raises(TokenStateError, match='interrupted'):
		factory().load()
	# Updating the account Secret is an explicit recovery action.
	assert factory('new-secret', 'new-secret-refresh').load() == ('new-secret', 'new-secret-refresh')


def test_same_key_does_not_mix_accounts(context):
	_, _, factory = context
	one, two = factory(name='one'), factory(name='two')
	one.load()
	one.save('one-access', 'one-refresh')
	assert two.load() == ('bootstrap-access', 'bootstrap-refresh')
	assert one.path != two.path


def test_wrong_encryption_key_does_not_fall_back_to_stale_secret(context):
	_, _, factory = context
	first = factory()
	first.load()
	first.save('new', 'new-refresh')
	with pytest.raises(TokenStateError, match='authenticate/decode'):
		factory(use_key=Fernet.generate_key().decode()).load()


def test_tampered_ciphertext_fails_closed(context):
	_, api, factory = context
	first = factory()
	first.load()
	first.save('new', 'new-refresh')
	sha, blob = api.blobs[first.path]
	api.blobs[first.path] = (sha, b'tampered-' + blob)
	with pytest.raises(TokenStateError, match='authenticate/decode'):
		factory().load()


def test_account_relocation_of_valid_ciphertext_is_rejected(context):
	_, api, factory = context
	one, two = factory(name='one'), factory(name='two')
	one.load()
	one.save('new', 'new-refresh')
	api.blobs[two.path] = api.blobs[one.path]
	with pytest.raises(TokenStateError, match='authenticate/decode'):
		two.load()


def test_read_failure_never_falls_back_to_stale_secret(context):
	_, api, factory = context
	api.deny_reads = True
	with pytest.raises(TokenStateError, match='HTTP 403'):
		factory().load()


def test_failed_save_keeps_only_encrypted_recovery_checkpoint(context):
	key, api, factory = context
	state = factory()
	state.load()
	api.deny_writes = True
	with pytest.raises(TokenStateError, match='HTTP 403'):
		state.save('new-access', 'new-refresh')
	checkpoint = next(Path('.xiaobai-token-recovery').glob('*.fernet'))
	assert checkpoint.stat().st_mode & 0o777 == 0o600
	assert b'new-access' not in checkpoint.read_bytes()
	assert json.loads(Fernet(key).decrypt(checkpoint.read_bytes()))['tokens'] == ['new-access', 'new-refresh']


def test_compare_and_swap_stops_concurrent_rotation(context):
	_, _, factory = context
	one, two = factory(), factory()
	one.load()
	two.load()
	one.save('bootstrap-access', 'bootstrap-refresh', pending=True)
	with pytest.raises(TokenStateError, match='Concurrent'):
		two.save('bootstrap-access', 'bootstrap-refresh', pending=True)


def test_lost_success_response_is_reconciled_without_second_rotation(context):
	_, api, factory = context
	state = factory()
	state.load()
	api.lose_ack = True
	state.save('new-access', 'new-refresh')
	assert len(api.puts) == 1
	assert factory().load() == ('new-access', 'new-refresh')


def test_save_transient_failures_are_bounded_and_keep_recovery(context):
	_, api, factory = context
	state = factory()
	state.load()
	api.always_503 = True
	with pytest.raises(TokenStateError, match='recovery checkpoint retained'):
		state.save('new-access', 'new-refresh')
	assert len(api.puts) == 3
	assert list(Path('.xiaobai-token-recovery').glob('*.fernet'))


def test_missing_or_partial_env_configuration(monkeypatch):
	monkeypatch.delenv('XIAOBAI_TOKEN_STATE_KEY', raising=False)
	monkeypatch.delenv('XIAOBAI_STATE_GITHUB_TOKEN', raising=False)
	acct = AccountConfig(cookies=None, provider='xiaobai')
	assert create_xiaobai_token_state(acct, 'https://example.com') is None
	monkeypatch.setenv('XIAOBAI_TOKEN_STATE_KEY', Fernet.generate_key().decode())
	with pytest.raises(TokenStateError, match='Both'):
		create_xiaobai_token_state(acct, 'https://example.com')
