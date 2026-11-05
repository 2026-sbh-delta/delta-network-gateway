import importlib.util,io,json,tarfile,tempfile,unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location('compose_host',Path(__file__).with_name('compose-host.py'))
host=importlib.util.module_from_spec(spec);spec.loader.exec_module(host)

class ComposeBoundaryTests(unittest.TestCase):
    def test_only_frontend_is_published_and_secret_is_backend_only(self):
        images={role:{'tag':role+':pinned','version':'test'}for role in ['frontend','backend','proxy']}
        cfg={'database':{'database':'delta_notes','user':'delta_app','connection_name':'project:region:database'}}
        d=host.configuration(cfg,images);services=d['services']
        self.assertEqual([role for role,s in services.items()if s.get('ports')],['frontend'])
        self.assertEqual([role for role,s in services.items()if s.get('secrets')],['backend'])
        self.assertEqual(services['proxy']['network_mode'],'service:backend')
        self.assertEqual(services['backend']['environment']['PGHOST'],'127.0.0.1')
        self.assertNotIn('PGPASSWORD',json.dumps(d))
        for s in services.values():
            self.assertTrue(s['read_only']);self.assertEqual(s['user'],'10001:10001')
            self.assertNotIn('privileged',s);self.assertEqual(s['cap_drop'],['ALL'])

    def test_offline_archives_reject_escape_link_and_device(self):
        for name,kind in [('../escape',tarfile.REGTYPE),('/absolute',tarfile.REGTYPE),('link',tarfile.SYMTYPE),('device',tarfile.CHRTYPE)]:
            with self.subTest(name=name),tempfile.TemporaryDirectory()as directory:
                p=Path(directory);archive=p/'input.tar';target=p/'target';target.mkdir()
                with tarfile.open(archive,'w')as t:
                    m=tarfile.TarInfo(name);m.type=kind;m.size=1 if kind==tarfile.REGTYPE else 0
                    t.addfile(m,io.BytesIO(b'x')if m.size else None)
                with self.assertRaises(ValueError):host.extract(archive,target)

if __name__=='__main__':unittest.main()
