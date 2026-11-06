"""Build only the committed user AI source into the existing private repository."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile

root=Path(__file__).resolve().parents[2]
repository='616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-cp-ai/api'
def call(args, **kwargs):
    return subprocess.run(args,check=True,**kwargs)

if __name__=='__main__':
    assert not subprocess.check_output(['git','status','--porcelain','--','services/delta-ai'],cwd=root)
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root).decode().strip()
    tag='ai-readiness-'+sha[:12]+'-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')
    endpoint=json.loads(subprocess.check_output(['docker','context','inspect']))[0]['Endpoints']['docker']['Host']
    with tempfile.TemporaryDirectory(prefix='delta-ai-registry-') as folder:
        plugins=Path(folder)/'cli-plugins';plugins.mkdir()
        plugin=Path.home()/'.docker/cli-plugins/docker-buildx'
        if plugin.is_file():(plugins/'docker-buildx').symlink_to(plugin.resolve())
        env={**os.environ,'DOCKER_CONFIG':folder,'DOCKER_HOST':endpoint}
        auth=subprocess.check_output(['aws','ecr','get-login-password','--profile','delta-finals','--region','ap-northeast-2'])
        call(['docker','login','--username','AWS','--password-stdin',repository.split('/')[0]],input=auth,env=env,stdout=subprocess.DEVNULL)
        del auth
        call(['docker','buildx','build','--platform','linux/arm64','--provenance=false','--sbom=false','--push','-t',repository+':'+tag,str(root/'services/delta-ai')],env=env)
    d=json.loads(subprocess.check_output(['aws','ecr','describe-images','--profile','delta-finals-ro','--region','ap-northeast-2','--repository-name','delta-cp-ai/api','--image-ids','imageTag='+tag,'--output','json']))
    result={'source_commit':sha,'tag':tag,'image':repository+'@'+d['imageDetails'][0]['imageDigest']}
    (root/'deploy/ai-readiness/image.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
