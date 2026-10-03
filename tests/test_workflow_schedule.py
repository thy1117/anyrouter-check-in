from pathlib import Path

WORKFLOW = Path(__file__).parent.parent / '.github' / 'workflows' / 'checkin.yml'


def test_checkin_workflow_runs_twice_daily_at_beijing_07_past():
	text = WORKFLOW.read_text(encoding='utf-8')

	assert "- cron: '7 1,13 * * *'" in text
	assert 'workflow_dispatch:' in text
	assert 'NOTIFY_EVERY_RUN: true' in text


def test_removed_fate_secret_is_not_wired_into_workflow():
	text = WORKFLOW.read_text(encoding='utf-8')

	assert 'EXTRA_ACCOUNTS_24' not in text


def test_removed_account_secrets_are_not_wired_into_workflow():
	text = WORKFLOW.read_text(encoding='utf-8')

	assert 'EXTRA_ACCOUNTS_3: ${{ secrets.EXTRA_ACCOUNTS_3 }}' in text
	assert 'EXTRA_ACCOUNTS_33: ${{ secrets.EXTRA_ACCOUNTS_33 }}' in text
	for removed_slot in (2, 15, 17, 18, 22, 25, 27, 28, 29, 30, 31, 32, 35):
		# 用完整赋值行比对，否则 EXTRA_ACCOUNTS_2 会被 EXTRA_ACCOUNTS_20 之类的前缀误判。
		assert f'EXTRA_ACCOUNTS_{removed_slot}: ' not in text


def test_aiaiai_secret_is_wired_into_workflow():
	text = WORKFLOW.read_text(encoding='utf-8')

	assert 'EXTRA_ACCOUNTS_11: ${{ secrets.EXTRA_ACCOUNTS_11 }}' in text
	assert 'EXTRA_ACCOUNTS_40: ${{ secrets.EXTRA_ACCOUNTS_40 }}' in text


def test_motomoto_secret_is_wired_into_workflow():
	text = WORKFLOW.read_text(encoding='utf-8')

	assert 'EXTRA_ACCOUNTS_36: ${{ secrets.EXTRA_ACCOUNTS_36 }}' in text


def test_gemai_secret_is_wired_into_workflow():
	text = WORKFLOW.read_text(encoding='utf-8')

	assert 'EXTRA_ACCOUNTS_34: ${{ secrets.EXTRA_ACCOUNTS_34 }}' in text


def test_ruachat_secret_is_wired_into_workflow():
	text = WORKFLOW.read_text(encoding='utf-8')

	assert 'EXTRA_ACCOUNTS_37: ${{ secrets.EXTRA_ACCOUNTS_37 }}' in text


def test_production_routing_is_unchanged():
	text = WORKFLOW.read_text(encoding='utf-8').split('\n  xiaobai-test:')[0]

	assert 'PROXY_SUBSCRIPTION_URL:' in text
	assert '${{ secrets.PROXY_NODES_BACKUP_ORACLE_SG }}' not in text
	assert '${{ secrets.PROXY_NODE_RAILWAY }}' not in text
	assert 'glados-proxy-assignments-' in text
	assert 'PROXY_NODE_NAME:' not in text
	assert 'run: bash scripts/setup_mihomo_proxy.sh' in text
	assert 'run: bash scripts/stop_mihomo_proxy.sh' in text
	assert 'PROXY_NODE_NAME: jiakuan' not in text


def test_sheapi_secrets_are_wired_without_removing_existing_secrets():
	text = WORKFLOW.read_text(encoding='utf-8')

	assert 'EXTRA_ACCOUNTS_10: ${{ secrets.EXTRA_ACCOUNTS_10 }}' in text
	assert 'EXTRA_ACCOUNTS_38: ${{ secrets.EXTRA_ACCOUNTS_38 }}' in text
	assert 'EXTRA_ACCOUNTS_39: ${{ secrets.EXTRA_ACCOUNTS_39 }}' in text
	assert 'EXTRA_ACCOUNTS_41: ${{ secrets.EXTRA_ACCOUNTS_41 }}' in text


def test_single_account_test_cannot_run_production_job():
	text = WORKFLOW.read_text(encoding='utf-8')
	assert (
		"github.event_name != 'workflow_dispatch' || !(inputs.xiaobai_test || inputs.xiaobai_config_only || inputs.sheapi_proxy_only)"
		in text
	)
	assert (
		"github.event_name == 'workflow_dispatch' && !inputs.sheapi_proxy_only && (inputs.xiaobai_test || inputs.xiaobai_config_only)"
		in text
	)
	test_job = text.split('\n  xiaobai-test:')[1]
	assert 'run: uv run python -m scripts.check_xiaobai' in test_job
	assert 'run checkin.py' not in test_job
	assert 'TELEGRAM_BOT_TOKEN' not in test_job
	assert 'ANYROUTER_ACCOUNTS' not in test_job
	assert 'PROXY_NODE_NAME: oracle-sg' in test_job
	assert 'XIAOBAI_STATUS_ONLY: ${{ inputs.xiaobai_status_only }}' in test_job


def test_token_state_uses_short_lived_token_and_ciphertext_only():
	text = WORKFLOW.read_text(encoding='utf-8')
	assert 'XIAOBAI_STATE_GITHUB_TOKEN: ${{ github.token }}' in text
	assert 'XIAOBAI_TOKEN_STATE_KEY: ${{ secrets.XIAOBAI_TOKEN_STATE_KEY }}' in text
	assert 'cancel-in-progress: false' in text
	assert 'path: .xiaobai-token-recovery/*.fernet' in text


def test_config_only_lookup_skips_proxy_and_reaches_safe_entrypoint():
	text = WORKFLOW.read_text(encoding='utf-8')
	test_job = text.split('\n  xiaobai-test:')[1]
	assert 'xiaobai_config_only:' in text
	assert 'XIAOBAI_CONFIG_ONLY: ${{ inputs.xiaobai_config_only }}' in test_job
	assert '配置单账号 Oracle SG 测试代理\n      if: ${{ !inputs.xiaobai_config_only }}' in test_job


def test_superapi_secret_60_is_wired_into_workflow():
	text = WORKFLOW.read_text(encoding='utf-8')

	assert 'EXTRA_ACCOUNTS_13: ${{ secrets.EXTRA_ACCOUNTS_13 }}' in text
	assert 'EXTRA_ACCOUNTS_46: ${{ secrets.EXTRA_ACCOUNTS_46 }}' in text
	assert 'EXTRA_ACCOUNTS_52: ${{ secrets.EXTRA_ACCOUNTS_52 }}' in text
	assert 'EXTRA_ACCOUNTS_60: ${{ secrets.EXTRA_ACCOUNTS_60 }}' in text


def test_sheapi_proxy_test_only_loads_subscription_and_requires_five_ips():
	text = WORKFLOW.read_text(encoding='utf-8')
	job = text.split('\n  sheapi-proxy-test:')[1]
	assert "github.event_name == 'workflow_dispatch' && inputs.sheapi_proxy_only" in job
	assert 'contents: read' in job
	assert 'secrets.PROXY_SUBSCRIPTION_URL' in job
	assert 'ACCOUNTS' not in job and 'TOKEN' not in job and 'checkin.py' not in job
	assert "assert len({state[account]['ip'] for account in accounts}) == 5" in job
	assert 'glados-proxy-assignments-' in job
