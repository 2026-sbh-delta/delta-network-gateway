"""Run bound, redacted logs persisted before native cleanup. No cleanup dependency."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import UTC,datetime
from uuid import UUID

from delta_ai.diagnostic_contract import safe_text

ROOT=Path('/var/lib/delta-platform/tmp/onprem-evidence')

def encode(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()

def redacted(text):
    text=re.sub(r'([a-z][a-z0-9+.-]*://)[^\s/@]+:[^\s/@]+@',r'\1[REDACTED]@',text,flags=re.I)
    text=re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b','[EMAIL]',text)
    return safe_text(text)[:6000]

def read_logs(provider,run_id):
    import httpx
    name=provider.target.name(run_id)
    url=provider.target.api_url+'/api/v1/namespaces/'+provider.target.namespace+'/pods/'+name+'/log'
    with httpx.Client(verify=provider.tls,transport=provider.transport,timeout=3,trust_env=False,follow_redirects=False) as c:
        with c.stream('GET',url,params={'container':'app','tailLines':40,'limitBytes':6000,'timestamps':'true'},
            headers={'Authorization':'Bearer '+provider.token}) as response:
            response.raise_for_status();data=bytearray()
            for block in response.iter_bytes():
                data.extend(block[:6001-len(data)])
                if len(data)>6000:break
    return bytes(data[:6000]).decode('utf8',errors='replace'),len(data)>6000

def capture(claim,provider,*,root=ROOT,reader=read_logs):
    source=claim['input_snapshot'];identifier=str(UUID(str(claim['id'])))
    if root.is_symlink():raise ValueError('Unsafe evidence directory')
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=root/(identifier+'.json')
    if path.is_symlink():raise ValueError('Unsafe evidence file')
    if path.exists():return load(claim,root=root)
    targets=[('frontend' if source.get('backend') else 'app',provider,source['image'])]
    if source.get('backend'):
        from delta_platform.adapters.onprem_bundle import BackendRuntime,BackendTarget
        targets.append(('backend',BackendRuntime(BackendTarget.model_validate(provider.target.model_dump()),provider.token,
            transport=provider.transport),source['backend']['image']))
    value={'version':1,'native_run_id':identifier,'build_id':source['build_id'],
        'commit':claim['resolved_commit_sha'],'image':source['image'],'image_digest':source['image_digest'],
        'target':source['sandbox_target']['api_url'],'namespace':provider.target.namespace,
        'captured_at':datetime.now(UTC).isoformat(),'before_cleanup':True,'containers':[]}
    for component,p,img in targets:
        item={'component':component,'resource':p.target.resource(identifier),'image':img,'status':'unavailable',
            'tail_lines':40,'limit_bytes':6000,'untrusted_content':True}
        try:
            pod=p.call('GET','pods',p.target.name(identifier),missing=True)
            if pod is None:raise ValueError('Pod absent')
            p.owned(pod,identifier,claim['resolved_commit_sha'],img)
            uid=pod['metadata']['uid']
            verified=(claim.get('submission')or{}).get('result')or{}
            expected=(verified.get('components',{}).get(component,{})or verified).get('revision')
            if expected and uid!=expected:raise ValueError('Verified Pod replaced')
            text,truncated=reader(p,identifier)
            after=p.call('GET','pods',p.target.name(identifier))
            p.owned(after,identifier,claim['resolved_commit_sha'],img)
            if after['metadata']['uid']!=uid:raise ValueError('Pod replaced')
            item.update(uid=uid,status='available' if text else 'empty',text=redacted(text),truncated=truncated,
                coverage='bounded_tail_only')
        except Exception:
            item['limitation']='정리 전 로그의 권한·응답·Pod 소유권을 확인하지 못했습니다.'
        value['containers'].append(item)
    raw=encode(value)
    if len(raw)>30000:raise ValueError('Evidence too large')
    fd,name=tempfile.mkstemp(prefix='.capture-',dir=root)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'wb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
        # Exclusive publish: a concurrent/replayed capture cannot replace a saved UID/log.
        try:os.link(name,path)
        except FileExistsError:pass
        dirfd=os.open(root,os.O_RDONLY)
        try:os.fsync(dirfd)
        finally:os.close(dirfd)
    finally:Path(name).unlink(missing_ok=True)
    return load(claim,root=root)

def load(claim,*,root=ROOT):
    source=claim['input_snapshot'];identifier=str(UUID(str(claim['id'])))
    path=root/(identifier+'.json')
    if root.is_symlink() or path.is_symlink() or not path.is_file() or path.stat().st_size>30000:
        raise ValueError('Evidence unavailable')
    if path.stat().st_mode & 0o077:raise ValueError('Evidence permissions changed')
    raw=path.read_bytes();value=json.loads(raw)
    binding={'version':1,'native_run_id':identifier,'build_id':source['build_id'],
        'commit':claim['resolved_commit_sha'],'image':source['image'],'image_digest':source['image_digest'],
        'target':source['sandbox_target']['api_url'],'namespace':source['sandbox_target']['namespace'],'before_cleanup':True}
    if any(value.get(k)!=v for k,v in binding.items()):raise ValueError('Evidence binding changed')
    verified=claim.get('result')or(claim.get('submission')or{}).get('result')or{}
    for item in value['containers']:
        expected=(verified.get('components',{}).get(item['component'],{})or verified).get('revision')
        if expected and item.get('status')in('available','empty') and item.get('uid')!=expected:raise ValueError('Verified Pod UID changed')
    return {'sha256':hashlib.sha256(raw).hexdigest(),'record':value}

def runner(settings):
    from delta_platform.adapters.onprem_sandbox import K3s,OnpremSandboxRunner,OnpremTarget,configured_target
    from delta_pipeline.onprem_scanners import inspector,DAST_TARGET
    class RecordingK3s(K3s):
        def cleanup(self,run_id,commit,image,guard):
            try:
                guard()
                import sqlalchemy as sa
                from delta_platform.core.schema import runs
                if hasattr(self.store,'native_submission'):submission=self.store.native_submission(self.claim['id'])
                else:
                    with self.store.engine.connect()as conn:
                        submission=conn.scalar(sa.select(runs.c.submission).where(runs.c.id==self.claim['id']))
                record=capture({**self.claim,'submission':submission},self)
                self.progress('runtime_evidence','정리 전 제한 로그 근거 저장 SHA256 '+record['sha256'])
            except Exception:
                # Evidence must never prevent native cleanup or fabricate an observation.
                pass
            return super().cleanup(run_id,commit,image,guard)
    class RecordingRunner:
        def execute(self,claim,store,progress):
            target=OnpremTarget.model_validate(configured_target(settings))
            provider=RecordingK3s(target,settings.onprem_sandbox_token.get_secret_value())
            provider.claim,provider.progress,provider.store=claim,progress,store
            scan=inspector(settings) if claim['input_snapshot'].get('dast_target')==DAST_TARGET else None
            result=OnpremSandboxRunner(settings,provider=provider,inspector=scan).execute(claim,store,progress)
            try:result['runtime_evidence_sha256']=load(claim)['sha256']
            except Exception:result['runtime_evidence_gap']=True
            return result
    return RecordingRunner()
