from datetime import datetime, timedelta, timezone

from utils.proxy import ProxyNodeAllocator


def test_same_provider_accounts_get_distinct_nodes_for_rolling_24_hours(tmp_path):
	now = datetime(2026, 9, 28, 1, 0, tzinfo=timezone.utc)
	state_file = tmp_path / 'proxy-assignments.json'
	allocator = ProxyNodeAllocator(['g1', 'g2', 'g3'], state_file=state_file, now=lambda: now)

	first = allocator.assign('sheapi', 'account-1')
	second = allocator.assign('sheapi', 'account-2')
	assert first != second

	reloaded = ProxyNodeAllocator(['g1', 'g2', 'g3'], state_file=state_file, now=lambda: now + timedelta(hours=23))
	assert reloaded.assign('sheapi', 'account-1') == first
	assert reloaded.assign('sheapi', 'account-3') not in {first, second}


def test_failed_node_is_not_reused_by_same_provider_within_24_hours(tmp_path):
	now = datetime(2026, 9, 28, 1, 0, tzinfo=timezone.utc)
	state_file = tmp_path / 'proxy-assignments.json'
	allocator = ProxyNodeAllocator(['g1', 'g2', 'g3'], state_file=state_file, now=lambda: now)

	failed = allocator.assign('sheapi', 'account-1')
	replacement = allocator.replace('sheapi', 'account-1', failed)
	other = allocator.assign('sheapi', 'account-2')

	assert replacement != failed
	assert other not in {failed, replacement}


def test_expired_reservations_can_be_reused_after_24_hours(tmp_path):
	now = datetime(2026, 9, 28, 1, 0, tzinfo=timezone.utc)
	state_file = tmp_path / 'proxy-assignments.json'
	allocator = ProxyNodeAllocator(['g1'], state_file=state_file, now=lambda: now)
	assert allocator.assign('sheapi', 'account-1') == 'g1'

	reloaded = ProxyNodeAllocator(['g1'], state_file=state_file, now=lambda: now + timedelta(hours=24, seconds=1))
	assert reloaded.assign('sheapi', 'account-2') == 'g1'
