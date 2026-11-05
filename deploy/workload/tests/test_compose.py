import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import compose as operator

spec = importlib.util.spec_from_file_location('compose_host', HERE / 'compose-host.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class ComposeTests(unittest.TestCase):
    def repository(self, directory):
        source = directory / 'source'
        source.mkdir()
        subprocess.run(['git', 'init', '-q', source], check=True)
        for role in ['frontend', 'backend']:
            folder = source / role
            folder.mkdir()
            for name in ['Dockerfile', '.dockerignore', 'package.json', 'server.mjs']:
                (folder / name).write_text('fixture')
            (folder / '.env').write_text('TEST_DUMMY_CREDENTIAL=excluded')
        subprocess.run(['git', '-C', source, 'add', '.'], check=True)
        subprocess.run(['git', '-C', source, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture'], check=True)
        return source

    def test_bundle_is_reproducible_and_excludes_tracked_env_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = self.repository(directory)
            first, artifact = operator.bundle(source, directory / 'one')
            second, _ = operator.bundle(source, directory / 'two')
            self.assertEqual(first, second)
            with tarfile.open(artifact) as archive:
                self.assertFalse(any(member.name.endswith('/.env') for member in archive.getmembers()))
                self.assertIn('backend/Dockerfile', archive.getnames())
                self.assertIn('tooling/compose-host.py', archive.getnames())
            (source / 'frontend/server.mjs').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'clean'):
                operator.bundle(source, directory / 'dirty')

    def test_bundle_member_boundary(self):
        for name in ['/etc/passwd', 'frontend/../../escape', '.env', 'backend/.env', 'backend/node_modules/pg/x', 'tooling/other.py']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                operator.validate_member(name)

    def test_failed_first_activation_returns_to_native(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            release = root / 'releases/new'
            release.mkdir(parents=True)
            calls = []
            def run(argv, **kwargs):
                calls.append(argv)
                if argv == ['systemctl', 'restart', 'delta-compose']:
                    raise RuntimeError('unhealthy')
            with patch.object(host, 'ROOT', root), patch.object(host, 'prepare'), patch.object(host, 'run', side_effect=run), patch.object(host, 'compose'), patch.object(host.subprocess, 'run'):
                with self.assertRaises(RuntimeError):
                    host.activate(release)
            self.assertFalse((root / 'current').exists())
            self.assertIn(['systemctl', 'enable', '--now', 'delta-sample'], calls)

    def test_failed_upgrade_reinstates_previous_compose_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            old, new = root / 'old', root / 'new'
            old.mkdir(); new.mkdir()
            (root / 'current').symlink_to(old)
            restarts = []
            def run(argv, **kwargs):
                if argv == ['systemctl', 'restart', 'delta-compose']:
                    restarts.append((root / 'current').resolve())
                    if len(restarts) == 1:
                        raise RuntimeError('unhealthy')
            with patch.object(host, 'ROOT', root), patch.object(host, 'prepare'), patch.object(host, 'run', side_effect=run), patch.object(host, 'compose'), patch.object(host.subprocess, 'run'):
                with self.assertRaises(RuntimeError):
                    host.activate(new)
            self.assertEqual(restarts, [new, old])
            self.assertEqual((root / 'current').resolve(), old)

    def test_success_and_reverse_activation_keep_previous(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            old, new = root / 'old', root / 'new'
            for release in [old, new]:
                release.mkdir()
                (release / 'release.json').write_text(json.dumps({'release': release.name}))
            (root / 'current').symlink_to(old)
            with patch.object(host, 'ROOT', root), patch.object(host, 'prepare'), patch.object(host, 'run'), patch('builtins.print'):
                host.activate(new)
                self.assertEqual((root / 'previous').resolve(), old)
                host.activate((root / 'previous').resolve())
            self.assertEqual((root / 'current').resolve(), old)
            self.assertEqual((root / 'previous').resolve(), new)

    def test_compose_ps_supports_json_array_and_json_lines(self):
        rows = [{'Service': 'backend', 'Health': 'healthy'}, {'Service': 'frontend', 'Health': 'healthy'}]
        self.assertEqual(host.parse_ps(json.dumps(rows)), rows)
        self.assertEqual(host.parse_ps('\n'.join(json.dumps(row) for row in rows)), rows)

    def test_native_service_operations_cannot_target_a_compose_host(self):
        with patch.object(operator.native, 'inventory', return_value={'deployment_mode': 'compose'}), patch.object(operator.native, 'remote') as remote, patch.object(sys, 'argv', ['release.py', 'check', '--service', 'backend']):
            with self.assertRaisesRegex(ValueError, 'compose.py'):
                operator.native.main()
        remote.assert_not_called()

    def test_operator_database_initialization_remains_available_in_compose(self):
        info = {'deployment_mode': 'compose', 'service_hosts': {'backend': 'frontend'}}
        with patch.object(operator.native, 'inventory', return_value=info), patch.object(operator.native, 'initialize_database') as initialize, patch.object(sys, 'argv', ['release.py', 'init-db', '--sample-root', '.']):
            operator.native.main()
        self.assertEqual(initialize.call_args.args[1], info)


if __name__ == '__main__':
    unittest.main()
