"""Build only the native extension above the immutable existing platform image."""
import datetime,json,os,subprocess,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[2]
repository='616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform'
base=repository+'@sha256:fea4c097d3f09049c8164949d3f0d6680aeda00edeec97b8f8621375f2b2e1b1'
if subprocess.check_output(['git','status','--porcelain','--','services'],cwd=root):raise SystemExit('Commit exact sources before build')
sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
tag='native-'+sha[:12]+'-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')
endpoint=json.loads(subprocess.check_output(['docker','context','inspect']))[0]['Endpoints']['docker']['Host']
with tempfile.TemporaryDirectory(prefix='delta-native-registry-')as folder:
 plugins=Path(folder)/'cli-plugins';plugins.mkdir();(plugins/'docker-buildx').symlink_to((Path.home()/'.docker/cli-plugins/docker-buildx').resolve())
 env={**os.environ,'DOCKER_CONFIG':folder,'DOCKER_HOST':endpoint}
 password=subprocess.check_output(['aws','ecr','get-login-password','--profile','delta-finals','--region','ap-northeast-2'])
 subprocess.run(['docker','login','--username','AWS','--password-stdin',repository.split('/')[0]],input=password,env=env,stdout=subprocess.DEVNULL,check=True);del password
 subprocess.run(['docker','buildx','build','--platform','linux/arm64','--provenance=false','--sbom=false','--build-arg','BASE_IMAGE='+base,'-f',str(root/'deploy/onprem-runtime/Dockerfile.worker'),'-t',repository+':'+tag,'--push',str(root)],env=env,check=True)
raw=subprocess.check_output(['aws','ecr','describe-images','--repository-name','delta-platform','--image-ids','imageTag='+tag,'--profile','delta-finals','--region','ap-northeast-2','--output','json'])
digest=json.loads(raw)['imageDetails'][0]['imageDigest'];result={'source_commit':sha,'base_image':base,'image':repository+'@'+digest,'tag':tag}
(root/'deploy/onprem-runtime/image.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
