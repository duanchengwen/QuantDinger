"""Destructive-operation guards run without a Docker daemon."""
import importlib.util
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

MODULE = Path(__file__).parents[1] / 'paper_stack.py'
spec = importlib.util.spec_from_file_location('paper_stack', MODULE)
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


class PaperStackTests(unittest.TestCase):
    def test_restore_waits_past_socket_only_initialization_server(self):
        probes = []

        def readiness(command, **kwargs):
            probes.append(command)
            # The image's temporary bootstrap server accepts Unix sockets only.
            tcp = '-h' in command and command[command.index('-h') + 1] == '127.0.0.1'
            return SimpleNamespace(returncode=1 if tcp and len(probes) == 1 else 0)

        with patch.object(ops.subprocess, 'run', side_effect=readiness), patch.object(ops.time, 'sleep'):
            ops.wait_database('isolated-fixture')
        self.assertEqual(len(probes), 2)

    def bundle(self, root, unsafe=False):
        root.mkdir()
        for name in ('database.dump', 'config.env', 'ledger.json'):
            (root / name).write_text('{}')
        with tarfile.open(root / 'settings.tar.gz', 'w:gz'):
            pass
        with tarfile.open(root / 'evidence.tar.gz', 'w:gz') as tar:
            info = tarfile.TarInfo('../escape' if unsafe else './evidence/raw.bin')
            info.size = 4
            tar.addfile(info, io.BytesIO(b'test'))
        (root / 'manifest.json').write_text(json.dumps({'files': {name: ops.sha256(root / name) for name in ops.ALLOWED_BACKUP_FILES}}))

    def test_valid_backup_checksums_and_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'backup'
            self.bundle(root)
            self.assertEqual(set(ops.verify_backup(root)['files']), ops.ALLOWED_BACKUP_FILES)

    def test_legacy_backup_without_settings_remains_restorable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'backup'
            self.bundle(root)
            manifest = json.loads((root / 'manifest.json').read_text())
            manifest['files'].pop('settings.tar.gz')
            (root / 'settings.tar.gz').unlink()
            (root / 'manifest.json').write_text(json.dumps(manifest))
            self.assertEqual(set(ops.verify_backup(root)['files']), ops.LEGACY_BACKUP_FILES)

    def test_settings_archive_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'backup'
            self.bundle(root)
            with tarfile.open(root / 'settings.tar.gz', 'w:gz') as archive:
                member = tarfile.TarInfo('../secret')
                member.size = 0
                archive.addfile(member, io.BytesIO())
            manifest = json.loads((root / 'manifest.json').read_text())
            manifest['files']['settings.tar.gz'] = ops.sha256(root / 'settings.tar.gz')
            (root / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                ops.verify_backup(root)

    def test_corrupted_dump_rejected_before_docker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'backup'
            self.bundle(root)
            (root / 'database.dump').write_bytes(b'corrupt')
            with patch.object(ops, 'run') as run, self.assertRaisesRegex(ValueError, 'checksum'):
                ops.restore_check(root, 'postgres:18')
            run.assert_not_called()

    def test_archive_traversal_rejected_before_docker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'backup'
            self.bundle(root, unsafe=True)
            with patch.object(ops, 'run') as run, self.assertRaisesRegex(ValueError, 'Unsafe'):
                ops.restore_check(root, 'postgres:18')
            run.assert_not_called()

    def test_restore_does_not_overwrite_existing_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.bundle(root / 'backup')
            target = root / 'existing'
            target.mkdir()
            (target / 'config.env').write_text('preserve')
            with patch.object(ops, 'run') as run, self.assertRaisesRegex(ValueError, 'NEW'):
                ops.restore(target, root / 'backup', SimpleNamespace())
            run.assert_not_called()
            self.assertEqual((target / 'config.env').read_text(), 'preserve')

    def test_backup_refuses_repo_path(self):
        with self.assertRaisesRegex(ValueError, 'outside'):
            ops.backup(Path('/unused'), ops.ROOT / 'backup-with-secrets')

    def test_init_refuses_existing_secrets(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(ValueError, 'already exists'):
            ops.init(Path(temporary), SimpleNamespace())

    def test_multiline_environment_rejected(self):
        with self.assertRaisesRegex(ValueError, 'line breaks'):
            ops.env_value('secret\nADMIN_USER=attacker')

    def test_backup_lock_rejects_overlap(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            with ops.operation_lock(state), self.assertRaisesRegex(RuntimeError, 'already running'), ops.operation_lock(state):
                self.fail('Concurrent backup must not run')

    def test_restore_rejects_existing_docker_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.bundle(root / 'backup')
            with patch.object(ops.subprocess, 'check_output', return_value='existing'), self.assertRaisesRegex(ValueError, 'already owns'):
                ops.restore(root / 'new', root / 'backup', SimpleNamespace(project='paper-existing'))
            self.assertFalse((root / 'new').exists())


    def test_init_rejects_existing_docker_project_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'new-state'
            with patch.object(ops.subprocess, 'check_output', return_value='existing'), self.assertRaisesRegex(ValueError, 'already owns'):
                ops.init(target, SimpleNamespace(project='paper-existing'))
            self.assertFalse(target.exists())

    def test_interrupted_backup_resumes_only_previously_running_services(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / 'state'
            state.mkdir()
            with patch.object(ops, 'compose', side_effect=lambda state, *args: list(args)), patch.object(ops.subprocess, 'check_output', return_value='api\n'), patch.object(ops, 'run') as run, patch.object(ops, 'pipe_to_file', side_effect=SystemExit(143)), self.assertRaises(SystemExit):
                ops.backup(state, root / 'backup')
            self.assertEqual(run.call_args_list[-1].args[0], ['start', 'api'])
            self.assertFalse((state / '.backup-paused.json').exists())

    def test_failed_recovery_retains_intent_for_stop_post_or_operator(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            intent = state / '.backup-paused.json'
            intent.write_text(json.dumps({'services': ['worker']}))
            with patch.object(ops, 'compose', side_effect=lambda state, *args: list(args)), patch.object(ops, 'run', side_effect=subprocess.CalledProcessError(1, ['docker'])), patch.object(ops.time, 'sleep'), self.assertRaisesRegex(RuntimeError, 'intent retained'):
                ops.resume_backup(state)
            self.assertTrue(intent.exists())
            with patch.object(ops, 'compose', side_effect=lambda state, *args: list(args)), patch.object(ops, 'run') as run:
                ops.resume_backup(state)
                ops.resume_backup(state)
            run.assert_called_once_with(['start', 'worker'])
            self.assertFalse(intent.exists())


    def test_timer_has_persistent_recovery_and_fixed_timezone(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / 'state'
            state.mkdir()
            (state / 'deployment.json').write_text(json.dumps({'project': 'timer-test'}))
            with patch.object(ops.Path, 'home', return_value=root), patch.object(ops, 'run'):
                ops.install_backup_timer(state, root / 'backups')
            units = root / '.config/systemd/user'
            service = (units / 'quantdinger-backup-timer-test.service').read_text()
            timer = (units / 'quantdinger-backup-timer-test.timer').read_text()
            self.assertIn('ExecStopPost=', service)
            self.assertIn('resume-backup', service)
            self.assertIn('Persistent=true', timer)
            self.assertIn('Asia/Shanghai', timer)
            self.assertNotIn('ADMIN_PASSWORD', service)
            self.assertNotIn('SECRET_KEY', service)
            with patch.object(ops.Path, 'home', return_value=root), patch.object(ops, 'run'), self.assertRaisesRegex(ValueError, 'already exists'):
                ops.install_backup_timer(state, root / 'backups')


if __name__ == '__main__':
    unittest.main()
