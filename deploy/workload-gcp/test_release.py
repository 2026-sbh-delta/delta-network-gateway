import importlib.util,io,json,tarfile,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('installer',ROOT/'terraform/workload-gcp/templates/install.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

class ArchiveTests(unittest.TestCase):
    def test_reject_traversal_and_symlinks(self):
        for name,link in [('../escape',False),('/absolute',False),('runtime/node',True),('.env',False)]:
            with self.subTest(name=name),tempfile.TemporaryDirectory()as directory:
                base=Path(directory);archive=base/'bundle.tar.gz';target=base/'target';target.mkdir()
                with tarfile.open(archive,'w:gz')as tar:
                    entry=tarfile.TarInfo(name);entry.size=1
                    if link:entry.type=tarfile.SYMTYPE;entry.linkname='/etc/passwd'
                    tar.addfile(entry,io.BytesIO(b'x')if not link else None)
                with self.assertRaises(ValueError):installer.extract(archive,target)

    def test_reject_role_or_architecture_mismatch(self):
        m={'schema':1,'service':'backend','commit':'a'*40,'sha256':'b'*64,'architecture':'x86_64','key':'releases/backend/'+'a'*40+'/'+'b'*64+'.tar.gz'}
        installer.validate(m,'backend')
        with self.assertRaises(ValueError):installer.validate(m,'frontend')
        with self.assertRaises(ValueError):installer.validate({**m,'architecture':'arm64'},'backend')

    def test_bootstrap_template_is_valid_python_after_render(self):
        import ast,base64
        source=(ROOT/'terraform/workload-gcp/templates/bootstrap.py.tftpl').read_text()
        cfg={'ROLE':'frontend','PORT':3000,'CONNECTION':'term2-delta:asia-northeast3:test'}
        replacements={'config':base64.b64encode(json.dumps(cfg).encode()).decode(),'runtime':'','installer':'','role':'frontend'}
        for key,value in replacements.items():source=source.replace('${'+key+'}',value)
        ast.parse(source)

if __name__=='__main__':unittest.main()
