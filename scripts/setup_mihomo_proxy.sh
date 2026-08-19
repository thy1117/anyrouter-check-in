#!/usr/bin/env bash
# 通过 mihomo 拉取订阅、启动本地代理并探测可用节点。
# 环境变量:
#   PROXY_SUBSCRIPTION_URL  Clash/Mihomo 订阅链接
#   PROXY_NODES             换行分隔的 vless:// 或 vmess:// 节点（私有 Secret）
#   PROXY_TEST_URL          探测目标，默认 https://www.google.com/generate_204
#   PROXY_REQUIRED          true 时探测失败则退出 1
#   PROXY_PORT              本地 mixed-port，默认 7890
#   PROXY_NODE_NAME         可选；固定使用指定节点，避免 url-test 自动选到被目标站拦截的出口

set -euo pipefail

if [[ -z "${PROXY_SUBSCRIPTION_URL:-}" && -z "${PROXY_NODES:-}" ]]; then
	echo "[INFO] No proxy subscription or node list configured, skip proxy setup"
	exit 0
fi

PROXY_DIR="${RUNNER_TEMP:-/tmp}/checkin-proxy"
PROXY_PORT="${PROXY_PORT:-7890}"
PROXY_TEST_URL="${PROXY_TEST_URL:-https://www.google.com/generate_204}"
MIHOMO_VERSION="${MIHOMO_VERSION:-v1.19.0}"
PROXY_REQUIRED="${PROXY_REQUIRED:-false}"
PROXY_NODE_NAME="${PROXY_NODE_NAME:-}"
CONTROLLER_PORT="${PROXY_CONTROLLER_PORT:-9090}"

mkdir -p "${PROXY_DIR}"
cd "${PROXY_DIR}"

echo "[INFO] Downloading mihomo ${MIHOMO_VERSION}..."
ARCHIVE="mihomo-linux-amd64-${MIHOMO_VERSION}.gz"
if ! curl --retry 3 --retry-delay 5 --retry-all-errors -fsSL -o "${ARCHIVE}" \
	"https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VERSION}/${ARCHIVE}"; then
	echo "[WARN] Failed to download mihomo ${MIHOMO_VERSION}, skip proxy setup"
	if [[ "${PROXY_REQUIRED}" == "true" ]]; then
		exit 1
	fi
	exit 0
fi
gunzip -f "${ARCHIVE}"
chmod +x "mihomo-linux-amd64-${MIHOMO_VERSION}"
MIHOMO_BIN="${PROXY_DIR}/mihomo-linux-amd64-${MIHOMO_VERSION}"

if [[ -n "${PROXY_NODES:-}" ]]; then
	echo "[INFO] Converting private proxy nodes to a local Mihomo provider..."
	PROXY_NODES="${PROXY_NODES}" python3 - > subscription.yaml <<'PY'
import base64
import json
import os
from urllib.parse import parse_qs, unquote, urlparse


def quote(value):
	return json.dumps(str(value), ensure_ascii=False)


def decode_vmess(value):
	payload = value.split('://', 1)[1].strip()
	payload += '=' * (-len(payload) % 4)
	return json.loads(base64.urlsafe_b64decode(payload).decode('utf-8'))


def vless_node(url, index):
	p = urlparse(url)
	query = parse_qs(p.query)
	name = unquote(p.fragment) or f'vless-{index}'
	node = {
		'name': name,
		'type': 'vless',
		'server': p.hostname,
		'port': p.port or 443,
		'uuid': unquote(p.username or ''),
		'udp': True,
		'network': query.get('type', ['tcp'])[0],
		'tls': query.get('security', [''])[0] in {'tls', 'reality'},
	}
	if query.get('flow', [''])[0]:
		node['flow'] = query['flow'][0]
	if query.get('sni', [''])[0]:
		node['servername'] = query['sni'][0]
	if query.get('fp', [''])[0]:
		node['client-fingerprint'] = query['fp'][0]
	if query.get('security', [''])[0] == 'reality':
		node['reality-opts'] = {
			'public-key': query.get('pbk', [''])[0],
			'short-id': query.get('sid', [''])[0],
		}
	return node


def vmess_node(url, index):
	payload = decode_vmess(url)
	name = payload.get('ps') or f'vmess-{index}'
	node = {
		'name': name,
		'type': 'vmess',
		'server': payload['add'],
		'port': int(payload.get('port', 443)),
		'uuid': payload['id'],
		'alterId': int(payload.get('aid', 0)),
		'cipher': payload.get('scy', 'auto') or 'auto',
		'udp': True,
		'tls': payload.get('tls') == 'tls',
		'network': payload.get('net', 'ws'),
	}
	if payload.get('sni'):
		node['servername'] = payload['sni']
	if payload.get('net') == 'ws':
		node['ws-opts'] = {
			'path': payload.get('path', '/') or '/',
			'headers': {'Host': payload.get('host', '')},
		}
	return node


nodes = []
for index, raw in enumerate(os.environ['PROXY_NODES'].splitlines(), 1):
	raw = raw.strip()
	if not raw:
		continue
	if raw.startswith('vless://'):
		nodes.append(vless_node(raw, index))
	elif raw.startswith('vmess://'):
		nodes.append(vmess_node(raw, index))
	else:
		raise SystemExit(f'unsupported proxy node scheme at line {index}')

if not nodes:
	raise SystemExit('PROXY_NODES contains no usable nodes')

print('proxies:')
for node in nodes:
	print(f'  - name: {quote(node.pop("name"))}')
	for key, value in node.items():
		if isinstance(value, dict):
			print(f'    {key}:')
			for subkey, subvalue in value.items():
				if isinstance(subvalue, dict):
					print(f'      {subkey}:')
					for leafkey, leafvalue in subvalue.items():
						print(f'        {leafkey}: {quote(leafvalue)}')
				else:
					print(f'      {subkey}: {quote(subvalue) if isinstance(subvalue, str) else str(subvalue).lower()}')
		elif isinstance(value, str):
			print(f'    {key}: {quote(value)}')
		else:
			print(f'    {key}: {str(value).lower()}')
PY
	PROXY_PROVIDER_BLOCK='type: file
    path: ./subscription.yaml'
else
	PROXY_PROVIDER_BLOCK="type: http
    url: \"${PROXY_SUBSCRIPTION_URL}\"
    interval: 3600
    path: ./subscription.yaml"
fi

cat > config.yaml <<EOF
mixed-port: ${PROXY_PORT}
external-controller: 127.0.0.1:${CONTROLLER_PORT}
allow-lan: false
ipv6: false
mode: rule
log-level: warning
unified-delay: true

proxy-providers:
  subscription:
    ${PROXY_PROVIDER_BLOCK}
    health-check:
      enable: true
      interval: 300
      url: https://www.gstatic.com/generate_204

proxy-groups:
  - name: CHECKIN
    type: select
    use:
      - subscription

rules:
  - MATCH,CHECKIN
EOF

echo "[INFO] Starting mihomo on 127.0.0.1:${PROXY_PORT}..."
nohup "${MIHOMO_BIN}" -d "${PROXY_DIR}" -f config.yaml > mihomo.log 2>&1 &
echo $! > mihomo.pid

if [[ -n "${PROXY_NODE_NAME}" ]]; then
	echo "[INFO] Pinning proxy group CHECKIN to node: ${PROXY_NODE_NAME}"
	SELECT_PAYLOAD="$(PROXY_NODE_NAME="${PROXY_NODE_NAME}" python3 - <<'PY_SELECT'
import json
import os
print(json.dumps({'name': os.environ['PROXY_NODE_NAME']}))
PY_SELECT
)"
	SELECTED=false
	for attempt in $(seq 1 30); do
		if curl -fsS -X PUT \
			-H 'Content-Type: application/json' \
			-d "${SELECT_PAYLOAD}" \
			"http://127.0.0.1:${CONTROLLER_PORT}/proxies/CHECKIN" -o /dev/null 2>/dev/null; then
			SELECTED=true
			break
		fi
		sleep 1
	done
	if [[ "${SELECTED}" != "true" ]]; then
		echo "[FAILED] Unable to select proxy node: ${PROXY_NODE_NAME}"
		tail -n 30 mihomo.log || true
		if [[ "${PROXY_REQUIRED}" == "true" ]]; then
			exit 1
		fi
	fi
fi

PROXY_URL="http://127.0.0.1:${PROXY_PORT}"
READY=false
for attempt in $(seq 1 45); do
	if curl -fsS -x "${PROXY_URL}" --max-time 20 "${PROXY_TEST_URL}" -o /dev/null 2>/dev/null; then
		READY=true
		break
	fi
	echo "[INFO] Waiting for proxy health check (${attempt}/45)..."
	sleep 2
done

if [[ "${READY}" != "true" ]]; then
	echo "[FAILED] Proxy health check failed for ${PROXY_TEST_URL}"
	tail -n 30 mihomo.log || true
	if [[ -f mihomo.pid ]]; then
		kill "$(cat mihomo.pid)" 2>/dev/null || true
	fi
	if [[ "${PROXY_REQUIRED}" == "true" ]]; then
		exit 1
	fi
	exit 0
fi

echo "[SUCCESS] Proxy is ready: ${PROXY_URL}"
echo "[INFO] Proxy is scoped to CHECKIN_PROXY_URL (browser/python only, not global HTTP_PROXY)"
if [[ -n "${GITHUB_ENV:-}" ]]; then
	echo "CHECKIN_PROXY_URL=${PROXY_URL}" >> "${GITHUB_ENV}"
fi
