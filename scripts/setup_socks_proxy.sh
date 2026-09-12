#!/usr/bin/env bash
# 校验一个现成的 SOCKS 代理，并把它作为按 Provider 启用的签到代理。
# CHECKIN_SOCKS_PROXY_URL 应存放在 GitHub Environment Secret 中，格式示例：
# socks5h://username:password@example.com:1080

set -euo pipefail

PROXY_URL="${CHECKIN_SOCKS_PROXY_URL:-}"
PROXY_TEST_URL="${PROXY_TEST_URL:-https://www.google.com/generate_204}"
PROXY_REQUIRED="${PROXY_REQUIRED:-false}"

if [[ -z "${PROXY_URL}" ]]; then
	echo '[WARN] CHECKIN_SOCKS_PROXY_URL is not configured; proxy-required providers cannot use the residential proxy'
	if [[ "${PROXY_REQUIRED}" == 'true' ]]; then
		exit 1
	fi
	exit 0
fi

case "${PROXY_URL}" in
	socks5://*|socks5h://*) ;;
	*)
		echo '[FAILED] CHECKIN_SOCKS_PROXY_URL must start with socks5:// or socks5h://'
		exit 1
		;;
esac

if ! curl --fail --silent --show-error \
	--proxy "${PROXY_URL}" \
	--connect-timeout 15 \
	--max-time 30 \
	"${PROXY_TEST_URL}" \
	--output /dev/null; then
	echo "[FAILED] Residential SOCKS proxy health check failed for ${PROXY_TEST_URL}"
	if [[ "${PROXY_REQUIRED}" == 'true' ]]; then
		exit 1
	fi
	exit 0
fi

echo '[SUCCESS] Residential SOCKS proxy is ready'
echo '[INFO] Proxy is scoped to CHECKIN_PROXY_URL (only providers with use_proxy=true)'
if [[ -n "${GITHUB_ENV:-}" ]]; then
	echo "CHECKIN_PROXY_URL=${PROXY_URL}" >> "${GITHUB_ENV}"
else
	echo '[WARN] GITHUB_ENV is unavailable; CHECKIN_PROXY_URL was not exported'
fi
