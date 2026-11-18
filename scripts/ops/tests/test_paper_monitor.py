"""Monitor evidence must not overstate natural observations or quiet failures."""
import copy
import io
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_theme_evidence_failure_is_reported_even_when_market_is_closed(self):
        value = payload()
        value['session'] = {'can_match': False, 'state': 'closed'}
        value['theme_receipts'] = [{'received_at': '2026-11-17T10:00:01+08:00',
                                    'evidence_status': 'invalid'}]
        report = self.report(value)
        self.assertIn('theme_reference_evidence_unverified', report['issues'])
        self.assertIn('theme_reference_received_at_in_future', report['issues'])

    def test_verified_theme_observation_does_not_complete_historical_backlog(self):
        value = payload()
        value['theme_receipts'] = [{'received_at': '2026-11-17T09:00:00+08:00',
                                    'evidence_status': 'verified', 'board_code': 'BK0001'}]
        report = self.report(value)
        self.assertEqual(report['issues'], [])
        self.assertEqual(report['theme_receipts'], value['theme_receipts'])
        self.assertFalse(report['completed_backlog'])

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

    def test_theme_job_failure_is_not_hidden_by_valid_old_archives(self):
        value = payload()
        value['theme_sync_job'] = {'LoadState': 'loaded', 'ActiveState': 'failed', 'Result': 'exit-code'}
        value['theme_receipts'] = [{'board_code': 'BK0800', 'received_at': '2026-11-16T20:10:00+08:00',
                                   'evidence_status': 'verified'}]
        report = self.report(value)
        self.assertIn('theme_sync_job_failed', report['issues'])
        self.assertEqual(report['theme_sync_job'], value['theme_sync_job'])
        self.assertEqual(report['status'], 'attention')

    def test_unavailable_theme_job_health_is_pending_not_assumed_passed(self):
        value = payload()
        value['theme_sync_job'] = {'LoadState': 'monitor_unavailable'}
        report = self.report(value)
        self.assertIn('theme_sync_job_health_unavailable', report['pending_acceptance'])
        self.assertFalse(report['completed_backlog'])

    def test_successful_theme_job_does_not_complete_historical_acceptance(self):
        value = payload()
        value['theme_sync_job'] = {'LoadState': 'loaded', 'ActiveState': 'inactive', 'Result': 'success'}
        report = self.report(value)
        self.assertNotIn('theme_sync_job_failed', report['issues'])
        self.assertFalse(report['completed_backlog'])
        self.assertFalse(report['natural_multiday_complete'])

    def test_pre_window_skip_is_not_a_successful_collection(self):
        value = payload()
        value['theme_sync_job'] = {'LoadState': 'loaded', 'Result': 'success'}
        value['theme_sync_status'] = {'status': 'skipped_before_window'}
        report = self.report(value)
        self.assertIn('theme_sync_first_window_not_completed', report['pending_acceptance'])
        self.assertEqual(report['issues'], [])

    def test_theme_job_partial_or_invalid_state_survives_systemd_success(self):
        for status in ('partial', 'failed', 'invalid_or_unavailable'):
            value = payload()
            value['theme_sync_job'] = {'LoadState': 'loaded', 'Result': 'success'}
            value['theme_sync_status'] = {'status': status}
            self.assertIn('theme_sync_job_state_' + status, self.report(value)['issues'])

    def test_stale_and_future_theme_job_completion_times_are_not_trusted(self):
        for time, expected in [('2026-11-15T20:11:00+08:00', 'theme_sync_job_completion_stale'),
                               ('2026-11-17T10:00:01+08:00', 'theme_sync_job_time_in_future'),
                               ('invalid', 'theme_sync_job_time_invalid')]:
            value = payload()
            value['theme_sync_job'] = {'LoadState': 'loaded', 'Result': 'success'}
            value['theme_sync_status'] = {'status': 'completed', 'completed_at': time}
            self.assertIn(expected, self.report(value)['issues'])

    def test_stuck_running_theme_job_is_not_silently_healthy(self):
        value = payload()
        value['theme_sync_job'] = {'LoadState': 'loaded', 'Result': 'success'}
        value['theme_sync_status'] = {'status': 'running', 'started_at': '2026-11-17T09:54:59+08:00'}
        self.assertIn('theme_sync_job_running_stale', self.report(value)['issues'])
        value['theme_sync_status']['started_at'] = '2026-11-17T09:59:00+08:00'
        self.assertNotIn('theme_sync_job_running_stale', self.report(value)['issues'])

    def test_theme_job_state_reader_uses_a_strict_output_allowlist(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            self.assertEqual(monitor.theme_sync_status(state), {'status': 'invalid_or_unavailable'})
            path = state / 'theme-sync-state.json'
            scope = {'schema_version': 1, 'project': 'paper-test', 'scheduler_id': 'a' * 32,
                     'config_version': 'eastmoney-configured-themes-20261118-v1'}
            (state / 'deployment.json').write_text(json.dumps({'project': 'paper-test'}))
            marker = state / '.theme-scheduler-initialized'
            marker.write_text(json.dumps(scope))
            path.write_text(json.dumps({**scope, 'mode': 'paper', 'attempts': {}, 'approvals_created': 0,
                'status': 'completed', 'window_date': '2026-11-17',
                'completed_at': '2026-11-17T20:11:00+08:00', 'unexpected_secret': 'do-not-expose'}))
            result = monitor.theme_sync_status(state)
            self.assertEqual(result['window_date'], '2026-11-17')
            self.assertNotIn('unexpected_secret', result)
            self.assertNotIn('do-not-expose', json.dumps(result))
            marker.unlink()
            self.assertEqual(monitor.theme_sync_status(state), {'status': 'invalid_or_unavailable'})
            marker.write_text(json.dumps(scope))
            for bad in ('[]', '{bad', '"do-not-expose"', '{}'):
                path.write_text(bad)
                self.assertEqual(monitor.theme_sync_status(state), {'status': 'invalid_or_unavailable'})

    def test_theme_job_state_reader_rejects_symlink_and_oversize(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            target = state / 'target.json'
            target.write_text('{}')
            path = state / 'theme-sync-state.json'
            path.symlink_to(target)
            self.assertEqual(monitor.theme_sync_status(state), {'status': 'invalid_or_unavailable'})
            target.unlink()
            self.assertEqual(monitor.theme_sync_status(state), {'status': 'invalid_or_unavailable'})
            path.unlink()
            path.write_text(' ' * 65537)
            self.assertEqual(monitor.theme_sync_status(state), {'status': 'invalid_or_unavailable'})

    def test_systemd_unavailable_does_not_disappear_from_monitor(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            (state / 'deployment.json').write_text(json.dumps({'project': 'paper-test'}))
            with patch.object(monitor.paper_stack, 'compose', return_value=['stub']), \
                    patch.object(monitor.subprocess, 'check_output', side_effect=[json.dumps(payload()), '[]']), \
                    patch.object(monitor.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='')):
                result = monitor.collect(state)
            for key in ('theme_sync_job', 'reference_sync_job'):
                self.assertEqual(result[key], {'LoadState': 'monitor_unavailable'})

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


    def research_payload(self, name='work', status='idle', stamp='2026-11-17T09:59:00+08:00'):
        value = payload()
        value['research_' + name + '_job'] = {'LoadState': 'loaded', 'Result': 'success'}
        value['research_job_status'] = {'status': 'observed', 'updated_at': stamp, 'jobs': {
            name: {'job': name, 'status': status, 'started_at': stamp, 'updated_at': stamp, 'execution_allowed': False}}}
        return value

    def test_uninstalled_research_modules_keep_existing_monitor_behavior(self):
        report = self.report()
        self.assertEqual(report['issues'], [])
        self.assertFalse(any('research' in item or 'news_' in item for item in report['pending_acceptance']))
        self.assertEqual(report['research_runs'], [])

    def test_three_research_systemd_failures_are_not_hidden_by_latest_data(self):
        for name in ('news', 'work', 'request'):
            value = self.research_payload(name)
            value['research_' + name + '_job']['Result'] = 'exit-code'
            self.assertIn('research_' + name + '_job_failed', self.report(value)['issues'])

    def test_request_acknowledgement_is_not_a_pending_run(self):
        value = self.research_payload(name='request', status='queued')
        value['research_schema_present'] = True
        stamp = value['observed_at']
        value['research_runs'] = [{
            'id': 'a' * 32, 'account_ref': 'hashed-account', 'status': 'completed',
            'created_at': stamp, 'started_at': stamp, 'completed_at': stamp,
            'execution_allowed': False, 'approval_status': 'not_submitted',
        }]
        report = self.report(value)
        self.assertNotIn('research_request_job_queued', report['pending_acceptance'])
        value['research_runs'][0].update(status='queued', started_at=None, completed_at=None)
        self.assertIn('research_queued:hashed-account', self.report(value)['pending_acceptance'])

    def test_host_summary_uses_its_observation_clock(self):
        value = self.research_payload(name='work', stamp='2026-11-17T10:00:01+08:00')
        value['jobs_observed_at'] = '2026-11-17T10:00:02+08:00'
        self.assertNotIn('research_work_job_time_invalid_or_future', self.report(value)['issues'])

    def test_failed_and_unknown_research_summaries_raise_attention(self):
        for status in ('failed', 'unknown'):
            report = self.report(self.research_payload(status=status))
            self.assertIn('research_work_job_state_' + status, report['issues'])
            self.assertEqual(report['status'], 'attention')

    def test_research_model_configuration_and_missing_evidence_are_pending_not_success(self):
        for status in ('needs_model_configuration', 'no_evidence', 'waiting_for_excerpt'):
            report = self.report(self.research_payload(status=status))
            self.assertEqual(report['issues'], [])
            self.assertIn('research_work_job_' + status, report['pending_acceptance'])
            self.assertIn('research_results_not_approved_for_strategy_execution', report['pending_acceptance'])
            self.assertFalse(report['completed_backlog'])

    def test_each_job_uses_its_own_last_result_and_staleness_budget(self):
        value = self.research_payload('work', stamp='2026-11-17T09:59:00+08:00')
        value['research_news_job'] = {'LoadState': 'loaded', 'Result': 'success'}
        value['research_job_status']['jobs']['news'] = {'job': 'news', 'status': 'ingested',
            'execution_allowed': False, 'started_at': '2026-11-17T08:00:00+08:00', 'updated_at': '2026-11-17T08:00:00+08:00'}
        report = self.report(value)
        self.assertIn('research_news_job_status_stale', report['issues'])
        self.assertNotIn('research_work_job_status_stale', report['issues'])
        self.assertIn('research_work_job_status_stale', self.report(self.research_payload(stamp='2026-11-17T09:49:59+08:00'))['issues'])
        self.assertIn('research_request_job_status_stale', self.report(self.research_payload('request', stamp='2026-11-15T08:30:00+08:00'))['issues'])

    def test_news_planned_overnight_pause_does_not_look_stale(self):
        value = self.research_payload('news', 'ingested', stamp='2026-11-16T22:30:00+08:00')
        value['observed_at'] = '2026-11-17T06:30:00+08:00'
        self.assertNotIn('research_news_job_status_stale', self.report(value)['issues'])
        value['observed_at'] = '2026-11-17T07:31:00+08:00'
        self.assertIn('research_news_job_status_stale', self.report(value)['issues'])

    def test_running_future_and_invalid_summary_times_fail_closed(self):
        value = self.research_payload(status='started', stamp='2026-11-17T09:54:00+08:00')
        self.assertIn('research_work_job_running_stale', self.report(value)['issues'])
        for stamp in ('2026-11-17T10:00:01+08:00', 'invalid'):
            value = self.research_payload(stamp=stamp)
            self.assertIn('research_work_job_time_invalid_or_future', self.report(value)['issues'])

    def test_missing_installed_summary_is_pending_and_malformed_summary_is_attention(self):
        value = self.research_payload()
        value['research_job_status'] = {'status': 'not_observed', 'jobs': {}}
        self.assertIn('research_work_job_not_observed', self.report(value)['pending_acceptance'])
        value['research_job_status'] = {'status': 'invalid_or_unavailable', 'jobs': {}}
        self.assertIn('research_job_status_invalid_or_unavailable', self.report(value)['issues'])

    def test_uncertain_review_card_and_invalid_template_are_visible(self):
        value = self.research_payload()
        work = value['research_job_status']['jobs']['work']
        work['proposal'] = {'status': 'already_proposed', 'notice': 'unknown', 'version_id': 'a' * 32}
        report = self.report(value)
        self.assertIn('research_proposal_notice_unknown', report['issues'])
        self.assertNotIn('research_work_job_state_unknown', report['issues'])
        work['proposal']['notice'] = 'unconfigured'
        report = self.report(value)
        self.assertIn('research_proposal_notice_unconfigured', report['pending_acceptance'])
        work['proposal'] = {'status': 'invalid_template'}
        self.assertIn('research_proposal_invalid_template', self.report(value)['issues'])
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            summary = value['research_job_status']
            data = {'schema_version': 1, 'updated_at': summary['updated_at'], 'jobs': summary['jobs']}
            (state / 'research-job-status.json').write_text(json.dumps(data))
            self.assertEqual(monitor.research_job_status(state)['jobs']['work']['proposal'], work['proposal'])

    def test_latest_research_per_account_survives_idle_worker_summary(self):
        value = self.research_payload()
        value['research_schema_present'] = True
        value['research_runs'] = [
            {'account_ref': 'first', 'status': 'failed', 'created_at': '2026-11-17T09:00:00+08:00', 'execution_allowed': False, 'approval_status': 'not_submitted'},
            {'account_ref': 'second', 'status': 'no_evidence', 'created_at': '2026-11-17T09:00:00+08:00', 'execution_allowed': False, 'approval_status': 'not_submitted'}]
        report = self.report(value)
        self.assertIn('research_latest_run_failed:first', report['issues'])
        self.assertIn('research_no_evidence:second', report['pending_acceptance'])
        value['research_runs'][0].update(status='started', execution_allowed=True)
        report = self.report(value)
        self.assertIn('research_latest_run_stale:first', report['issues'])
        self.assertIn('research_safety_changed:first', report['issues'])

    def test_news_evidence_failure_missing_and_module_unavailable_are_explicit(self):
        value = payload()
        value['news_schema_present'] = True
        value['news_observation'] = {'status': 'invalid_or_unavailable'}
        self.assertIn('news_evidence_invalid_or_unavailable', self.report(value)['issues'])
        for status in ('not_observed', 'module_unavailable'):
            value['news_observation'] = {'status': status}
            self.assertIn('news_evidence_' + status, self.report(value)['pending_acceptance'])
        value['news_observation'] = {'status': 'verified', 'received_at': '2026-11-17T10:00:01+08:00'}
        self.assertIn('news_evidence_received_at_in_future', self.report(value)['issues'])

    def test_research_summary_reader_rejects_secret_fields_bad_schema_symlink_and_oversize(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            path = state / 'research-job-status.json'
            self.assertEqual(monitor.research_job_status(state), {'status': 'not_observed', 'jobs': {}})
            stamp = '2026-11-17T09:59:00+08:00'
            data = {'schema_version': 1, 'updated_at': stamp, 'jobs': {'work': {
                'job': 'work', 'status': 'failed', 'started_at': stamp, 'updated_at': stamp,
                'execution_allowed': False, 'error': 'model_request_failed'}}}
            path.write_text(json.dumps(data))
            self.assertEqual(monitor.research_job_status(state)['jobs']['work']['error'], 'model_request_failed')
            data['jobs']['work']['raw_response'] = 'do-not-expose'
            path.write_text(json.dumps(data))
            result = monitor.research_job_status(state)
            self.assertEqual(result['status'], 'invalid_or_unavailable')
            self.assertNotIn('do-not-expose', json.dumps(result))
            for content in ('[]', '{bad', ' ' * 16385):
                path.write_text(content)
                self.assertEqual(monitor.research_job_status(state)['status'], 'invalid_or_unavailable')
            path.unlink()
            target = state / 'target.json'
            target.write_text('{}')
            path.symlink_to(target)
            self.assertEqual(monitor.research_job_status(state)['status'], 'invalid_or_unavailable')

    def test_collect_checks_exact_research_service_names_without_starting_jobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            (state / 'deployment.json').write_text(json.dumps({'project': 'paper-test'}))
            with patch.object(monitor.paper_stack, 'compose', return_value=['stub']), \
                    patch.object(monitor.subprocess, 'check_output', side_effect=[json.dumps(payload()), '[]']), \
                    patch.object(monitor.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='LoadState=not-found')) as call:
                result = monitor.collect(state)
            commands = [item.args[0] for item in call.call_args_list]
            for name in ('news', 'work', 'request'):
                self.assertIn(['systemctl', '--user', 'show', 'quantdinger-research-' + name + '-paper-test.service', '--property=LoadState,ActiveState,Result'], commands)
            self.assertNotIn('research_job_status', result)

    def test_collector_queries_latest_research_for_each_account_without_sensitive_columns(self):
        queries = []
        current = datetime.now(timezone.utc)
        heartbeat_at = current + timedelta(microseconds=1)
        heartbeat_read = False
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                stamp = heartbeat_at if heartbeat_read else current
                return stamp.astimezone(tz)
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def execute(self, query, params=None): self.query = query; queries.append((query, params))
            def fetchone(self):
                if 'AS news_present' in self.query: return {'news_present': True, 'research_present': True}
                if 'AS cash_ok' in self.query: return {'cash_ok': True, 'reservations_ok': True}
                if 'FROM qd_ashare_research_runs' in self.query: return {'id': 'a'*32, 'status': 'no_evidence', 'created_at': current, 'started_at': None, 'completed_at': current, 'execution_allowed': False, 'approval_status': 'not_submitted'}
                if 'to_regclass' in self.query: return {'present': False}
                return {'mismatches': 0}
            def fetchall(self):
                nonlocal heartbeat_read
                if 'FROM qd_ashare_process_health' in self.query:
                    heartbeat_read = True
                    return [{'process_name': 'managed_paper_worker', 'observed_at': heartbeat_at,
                             'details': {'status': 'running'}}]
                if 'FROM qd_ashare_web' in self.query: return [{'tenant_id': tenant, 'account_id': 'paper', 'quotes': [], 'worker': {}, 'bot_configured': False} for tenant in ('one', 'two')]
                return []
        connection = SimpleNamespace(cursor=lambda **_: Cursor(), close=lambda: None)
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def cursor(self, **kwargs): return connection.cursor(**kwargs)
            def close(self): pass
            def set_session(self, **kwargs): self.session = kwargs
        db = Connection()
        calls = []
        class NewsStore:
            def __init__(self, value): self.connection = value
            def list_items(self, **kwargs): calls.append(kwargs); return {'items': [], 'receipts': []}
        modules = {
            'datetime': SimpleNamespace(datetime=Clock, timezone=timezone),
            'psycopg2': SimpleNamespace(connect=lambda *_, **__: db, Error=RuntimeError),
            'psycopg2.extras': SimpleNamespace(RealDictCursor=object),
            'app.ashare.calendar_rules': SimpleNamespace(market_session=lambda _: {}, trading_day_status=lambda _: {}),
            'app.ashare.schema': SimpleNamespace(get_database_url=lambda: 'stub-private-dsn'),
            'app.ashare.news': SimpleNamespace(NewsStore=NewsStore, MAX_RECEIPTS=20)}
        output = io.StringIO()
        with patch.dict(sys.modules, modules), patch('shutil.disk_usage', return_value=SimpleNamespace(free=10**9)), redirect_stdout(output):
            exec(compile(monitor.COLLECTOR, '<monitor collector>', 'exec'), {})
        data = json.loads(output.getvalue())
        research_queries = [(query, params) for query, params in queries if 'FROM qd_ashare_research_runs' in query]
        self.assertEqual(len(research_queries), 2)
        self.assertEqual([params for _, params in research_queries], [('one', 'paper'), ('two', 'paper')])
        self.assertTrue(all('LIMIT 1' in query and 'raw_response' not in query and 'idempotency_key' not in query for query, _ in research_queries))
        self.assertEqual(db.session['readonly'], True)
        self.assertEqual(calls[0]['limit'], 1)
        self.assertEqual(len(data['research_runs']), 2)
        self.assertGreaterEqual(monitor.timestamp(data['observed_at']), monitor.timestamp(data['heartbeats'][0]['observed_at']))
        self.assertNotIn('stub-private-dsn', output.getvalue())
        self.assertNotIn('tenant_id', output.getvalue())


if __name__ == '__main__':
    unittest.main()
