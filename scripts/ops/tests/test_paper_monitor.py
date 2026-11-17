"""Monitor evidence must not overstate natural observations or quiet failures."""
import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

OPS = Path(__file__).parents[1]
sys.path.insert(0, str(OPS))
spec = importlib.util.spec_from_file_location('paper_monitor', OPS / 'paper_monitor.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def payload():
    return {'observed_at': '2026-11-17T10:00:00+08:00',
            'session': {'can_match': True, 'state': 'continuous'},
            'containers': [{'Service': name, 'State': 'running', 'Health': 'healthy'} for name in ('api', 'db', 'web', 'worker')],
            'heartbeats': [{'process_name': 'managed_paper_worker', 'observed_at': '2026-11-17T09:59:59+08:00', 'details': {'status': 'running'}}],
            'accounts': [{'account_ref': 'hashed-account', 'state': 'healthy', 'ledger_reconciled': True,
                          'quotes': [{'symbol': 'sz.000001', 'exchange_time': '2026-11-17T09:59:59+08:00',
                                      'received_at': '2026-11-17T09:59:59+08:00', 'evidence_sha256': 'abc', 'raw_evidence_status': 'verified'}]}],
            'observations': [{'trading_day': '2026-11-17', 'wall_clock_polls': 3, 'controlled_time_polls': 0, 'is_trading_day': True}],
            'daily_reports': [], 'data_free_bytes': 10**9,
            'reference_receipts': [], 'safety_flags_disabled': {'LIVE_TRADING_ENABLED': True,
                'AGENT_LIVE_TRADING_ENABLED': True, 'AI_AUTO_PROMOTION_ENABLED': True}}


class MonitorTests(unittest.TestCase):
    def report(self, value=None):
        return monitor.assess(value or payload(), {'status': 'current', 'free_bytes': 10**9})

    def test_healthy_paper_is_not_complete_backlog_or_natural_validation(self):
        report = self.report()
        self.assertEqual(report['status'], 'operational')
        self.assertFalse(report['completed_backlog'])
        self.assertFalse(report['natural_multiday_complete'])
        self.assertIn('natural_sample_days_below_target', report['pending_acceptance'])

    def test_controlled_replays_never_count_as_natural_days(self):
        value = payload()
        value['observations'] = [{'trading_day': f'2026-11-{day:02d}', 'wall_clock_polls': 0, 'controlled_time_polls': 100} for day in range(1, 8)]
        self.assertEqual(self.report(value)['natural_sample_days'], [])

    def test_five_sampled_days_do_not_prove_intraday_coverage(self):
        value = payload()
        value['observations'] = [{'trading_day': f'2026-11-{day:02d}', 'wall_clock_polls': 1, 'is_trading_day': True} for day in range(9, 14)]
        report = self.report(value)
        self.assertNotIn('natural_sample_days_below_target', report['pending_acceptance'])
        self.assertFalse(report['natural_multiday_complete'])
        self.assertIn('intraday_gap_and_full_session_coverage_not_proven', report['pending_acceptance'])

    def test_weekend_and_unknown_calendar_samples_do_not_count(self):
        value = payload()
        value['observations'] = [{'trading_day': '2026-11-15', 'wall_clock_polls': 200, 'is_trading_day': False},
                                 {'trading_day': '2030-01-02', 'wall_clock_polls': 200}]
        self.assertEqual(self.report(value)['natural_sample_days'], [])

    def test_off_hours_old_quote_is_expected_not_live_failure(self):
        value = payload()
        value['session'] = {'state': 'closed', 'can_match': False}
        value['accounts'][0]['quotes'][0]['exchange_time'] = '2026-11-16T15:00:00+08:00'
        self.assertEqual(self.report(value)['issues'], [])

    def test_continuous_stale_and_future_quote_are_issues(self):
        for time in ('2026-11-17T09:59:00+08:00', '2026-11-17T10:00:01+08:00'):
            value = payload()
            value['accounts'][0]['quotes'][0]['exchange_time'] = time
            self.assertTrue(any('quote_stale' in issue for issue in self.report(value)['issues']))

    def test_environment_safety_not_ignored_when_services_are_healthy(self):
        value = payload()
        value['safety_flags_disabled']['AI_AUTO_PROMOTION_ENABLED'] = False
        self.assertIn('paper_safety_environment_changed', self.report(value)['issues'])

    def test_stale_worker_and_unhealthy_container(self):
        value = payload()
        value['containers'][0]['Health'] = 'unhealthy'
        value['heartbeats'][0]['observed_at'] = '2026-11-17T09:00:00+08:00'
        report = self.report(value)
        self.assertIn('service_not_healthy:api', report['issues'])
        self.assertIn('worker_heartbeat_stale_or_stopped', report['issues'])

    def test_ledger_mismatch_and_failed_report_are_issues(self):
        value = payload()
        value['accounts'][0]['ledger_reconciled'] = False
        value['daily_reports'] = [{'reconciliation': {'ok': False}}]
        self.assertIn('daily_report_reconciliation_failed', self.report(value)['issues'])
        self.assertIn('ledger_reconciliation_failed:hashed-account', self.report(value)['issues'])

    def test_quote_and_security_raw_hash_errors_are_detected_off_hours(self):
        value = payload()
        value['session'] = {'can_match': False, 'state': 'closed'}
        value['accounts'][0]['quotes'][0]['raw_evidence_status'] = 'hash_mismatch'
        value['reference_receipts'] = [{'received_at': '2026-11-17T09:59:59+08:00', 'raw_evidence_status': 'hash_mismatch'}]
        self.assertIn('security_reference_raw_evidence_unverified', self.report(value)['issues'])
        self.assertTrue(any('quote_raw_evidence_unverified' in issue for issue in self.report(value)['issues']))

    def test_capacity_and_future_reference_timestamp(self):
        value = payload()
        value['data_free_bytes'] = 1
        value['reference_receipts'] = [{'received_at': '2026-11-17T10:00:01+08:00', 'raw_evidence_status': 'verified'}]
        report = monitor.assess(value, {'status': 'current', 'free_bytes': 1})
        self.assertIn('data_volume_capacity_low', report['issues'])
        self.assertIn('backup_volume_capacity_low', report['issues'])
        self.assertIn('security_reference_received_at_in_future', report['issues'])

    def test_deduplicates_continuous_failure_signals(self):
        value = payload()
        value['accounts'][0]['quotes'] *= 2
        value['accounts'][0]['quotes'][0]['exchange_time'] = 'invalid'
        report = self.report(value)
        self.assertEqual(len(report['issues']), len(set(report['issues'])))

    def test_quiet_fingerprint_excludes_clock_ages_and_sample_counters(self):
        first = self.report()
        second = copy.deepcopy(first)
        second['observed_at'] = '2026-11-17T10:00:05+08:00'
        second['observations'][0]['wall_clock_polls'] += 1
        self.assertEqual(monitor.significant_fingerprint(first), monitor.significant_fingerprint(second))
        second['issues'] = ['new_actionable_failure']
        self.assertNotEqual(monitor.significant_fingerprint(first), monitor.significant_fingerprint(second))

    def test_report_cannot_write_inside_checkout_or_through_symlink(self):
        with self.assertRaises(ValueError):
            monitor.private_report(monitor.paper_stack.ROOT / 'accidental-report.json', {})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / 'preserve.json'
            target.write_text('preserve')
            link = root / 'alias.json'
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                monitor.private_report(link, {})
            self.assertEqual(target.read_text(), 'preserve')

    def test_reference_job_failure_is_not_hidden_by_healthy_runtime(self):
        value = payload()
        value['reference_sync_job'] = {'LoadState': 'loaded', 'ActiveState': 'failed', 'Result': 'exit-code'}
        report = self.report(value)
        self.assertIn('reference_sync_job_failed', report['issues'])
        self.assertEqual(report['status'], 'attention')

    def test_report_permissions_are_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'monitor.json'
            target.write_text('{}')
            target.chmod(0o644)
            monitor.private_report(target, {'status': 'operational'})
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(target.read_text())['status'], 'operational')

    def test_backlog_states_are_explicit_not_inferred_from_health(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'backlog.json'
            path.write_text(json.dumps({'items': [{'id': 'deployment', 'status': 'complete', 'acceptance': 'proof.json'},
                                                  {'id': 'natural_days', 'status': 'pending'}]}))
            report = self.report()
            fingerprint = monitor.significant_fingerprint(report)
            monitor.attach_backlog(report, path)
            self.assertEqual(report['pending_backlog_ids'], ['natural_days'])
            self.assertFalse(report['completed_backlog'])
            self.assertNotEqual(fingerprint, monitor.significant_fingerprint(report))


if __name__ == '__main__':
    unittest.main()
