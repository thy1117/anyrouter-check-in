import base64
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / 'scripts' / 'setup_mihomo_proxy.sh'


def _conversion_script() -> str:
	text = SCRIPT.read_text(encoding='utf-8')
	start_marker = "PROXY_NODES=\"${PROXY_NODES}\" python3 - > subscription.yaml <<'PY'\n"
	start = text.index(start_marker) + len(start_marker)
	end = text.index("\nPY\n", start)
	return text[start:end]


def test_socks_share_link_converts_to_mihomo_node():
	auth = base64.urlsafe_b64encode(b'user:password').decode().rstrip('=')
	env = os.environ | {'PROXY_NODES': f'socks://{auth}@proxy.example:9527#jiakuan'}
	result = subprocess.run(
		['python3', '-'],
		input=_conversion_script(),
		text=True,
		capture_output=True,
		env=env,
		check=True,
	)

	lines = result.stdout.splitlines()
	assert '  - name: "jiakuan"' in lines
	assert '    type: "socks5"' in lines
	assert '    server: "proxy.example"' in lines
	assert '    port: 9527' in lines
	assert '    username: "user"' in lines
	assert '    password: "password"' in lines
