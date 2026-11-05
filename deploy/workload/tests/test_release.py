import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch


BASE = Path(__file__).resolve().parents[3]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load('installer', BASE / 'terraform/workload/modules/sample-app/templates/install-release.py')
operator = load('operator_release', BASE / 'deploy/workload/release.py')


class ReleaseTests(unittest.TestCase):
    def manifest(self, service='backend'):
        commit, digest = 'a' * 40, 'b' * 64
        return {'schema': 1, 'service': service, 'commit': commit, 'sha256': digest,
                'key': 'releases/{}/{}/{}.tar.gz'.format(service, commit, digest), 'api_host': '10.10.1.8'}

    def test_manifest_restricts_service_destination_and_frontend_target(self):
        manifest = self.manifest('frontend')
        installer.validate_manifest(manifest, 'frontend')
        operator.validate_artifact_manifest(manifest)
        for field, value in [('key', 'unrelated/resource'), ('commit', '../escape'), ('service', 'database')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                operator.validate_artifact_manifest({**manifest, field: value})
        for field, value in [('api_host', '8.8.8.8'), ('sha256', 'wrong')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                installer.validate_manifest({**manifest, field: value}, 'frontend')

    def archive(self, folder, name, *, symlink=False):
        path = folder / 'test.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            member = tarfile.TarInfo(name)
            if symlink:
                member.type = tarfile.SYMTYPE
                member.linkname = '/etc/passwd'
                archive.addfile(member)
            else:
                content = b'export const ok = true;'
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        return path

    def test_rejects_traversal_links_and_hidden_credentials_before_extraction(self):
        for name, symlink in [('../escape', False), ('/etc/escape', False), ('.env', False), ('lib/link', True)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                destination = folder / 'files'
                destination.mkdir()
                archive = self.archive(folder, name, symlink=symlink)
                with self.assertRaises(ValueError):
                    installer.extract_archive(archive, destination)
                self.assertEqual(list(destination.iterdir()), [])

    def test_valid_artifact_is_readable_and_has_no_execute_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            destination = folder / 'files'
            destination.mkdir()
            installer.extract_archive(self.archive(folder, 'server.mjs'), destination)
            self.assertEqual((destination / 'server.mjs').stat().st_mode & 0o777, 0o644)

    def release(self, root, name):
        target = root / 'releases' / name
        target.mkdir(parents=True)
        (target / 'release.json').write_text(json.dumps({'commit': name}))
        return target

    def test_failed_upgrade_restores_previous_symlink_and_rechecks_health(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            old = self.release(root, 'old')
            new = self.release(root, 'new')
            (root / 'current').symlink_to(old)
            with patch.object(installer, 'ROOT', root), patch.object(installer, 'restart_and_check', side_effect=[RuntimeError('unhealthy'), None]) as check:
                with self.assertRaises(RuntimeError):
                    installer.activate(new, '8080')
                self.assertEqual((root / 'current').resolve(), old)
                self.assertEqual([call.args[1] for call in check.call_args_list], ['new', 'old'])

    def test_successful_upgrade_records_previous_and_supports_reverse_activation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            old = self.release(root, 'old')
            new = self.release(root, 'new')
            (root / 'current').symlink_to(old)
            with patch.object(installer, 'ROOT', root), patch.object(installer, 'restart_and_check'):
                installer.activate(new, '8080')
                self.assertEqual((root / 'current').resolve(), new)
                self.assertEqual((root / 'previous').resolve(), old)
                installer.activate((root / 'previous').resolve(), '8080')
                self.assertEqual((root / 'current').resolve(), old)
                self.assertEqual((root / 'previous').resolve(), new)

    def test_failed_first_release_stops_service_and_removes_current_link(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            new = self.release(root, 'new')
            with patch.object(installer, 'ROOT', root), patch.object(installer, 'restart_and_check', side_effect=RuntimeError('unhealthy')), patch.object(installer.subprocess, 'run') as run:
                with self.assertRaises(RuntimeError):
                    installer.activate(new, '8080')
                self.assertFalse((root / 'current').is_symlink())
                self.assertEqual(run.call_args.args[0], ['systemctl', 'stop', 'delta-sample'])


if __name__ == '__main__':
    unittest.main()
