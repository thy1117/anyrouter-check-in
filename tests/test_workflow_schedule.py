from pathlib import Path

WORKFLOW = Path(__file__).parent.parent / '.github' / 'workflows' / 'checkin.yml'


def test_checkin_workflow_only_runs_at_beijing_9_and_21():
	text = WORKFLOW.read_text(encoding='utf-8')

	assert "- cron: '0 1,13 * * *'" in text
	assert 'workflow_dispatch:' not in text
	assert 'NOTIFY_EVERY_RUN: true' in text


def test_removed_fate_secret_is_not_wired_into_workflow():
	text = WORKFLOW.read_text(encoding='utf-8')

	assert 'EXTRA_ACCOUNTS_24' not in text


def test_only_remaining_additional_twinkle_secret_is_wired_into_workflow():
	text = WORKFLOW.read_text(encoding='utf-8')

	assert 'EXTRA_ACCOUNTS_28: ${{ secrets.EXTRA_ACCOUNTS_28 }}' in text
	for removed_slot in (27, 29, 30, 31, 32):
		assert f'EXTRA_ACCOUNTS_{removed_slot}' not in text
