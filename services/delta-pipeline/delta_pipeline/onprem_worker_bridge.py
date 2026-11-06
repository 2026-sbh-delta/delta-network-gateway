"""Fixed SSM document entrypoint inside the existing trusted Worker. No shell/API credentials exported."""
import base64
import gzip
import json
from uuid import UUID

from delta_pipeline.onprem import NativeOnpremJob
from delta_pipeline.clients import ServiceUnavailable
from delta_pipeline.registered_build import RegisteredBuild
from delta_ai.diagnostic_contract import scrub

REPO='https://github.com/2026-sbh-delta/delta-sample-app'
ACTIONS={'catalog','context','start','status','cancel','inspect','retry_ready','resume_ready','observation_ready','absence','sast_start','sast_status','sast_report'}

def context(store, identifier):
    build=store.run(UUID(identifier),'image_build')
    service=store.service(UUID(str(build['service_id'])))
    if service['repository_url']!=REPO or service.get('build_context') not in ('backend','frontend'):
        raise ValueError('Unsupported registered source')
    registered=RegisteredBuild.from_snapshot(build,service=service)
    value={'service':{k:service[k] for k in ('id','name','repository_url')},
        'build':{k:build[k] for k in ('id','kind','status','service_id','resolved_commit_sha')} | {'result':{'image':registered.image}}}
    if service.get('build_context')=='frontend':
        for other in store.list_services(50,None).get('items',[]):
            if (other.get('repository_url')!=REPO or other.get('build_context')!='backend'
                    or not service.get('project_id') or other.get('project_id')!=service['project_id']):continue
            for candidate in store.list_runs(other['id'],10,None,kind='image_build').get('items',[]):
                if candidate.get('resolved_commit_sha')!=build['resolved_commit_sha']:continue
                try:RegisteredBuild.from_snapshot(candidate,service=other)
                except (ValueError,KeyError,ServiceUnavailable):continue
                value['backend_build_id']=str(candidate['id']);return value
        raise ValueError('Matching backend build required')
    return value

def execute(request):
    from delta_platform.config import Settings
    from delta_platform.core.database import engine_for
    from delta_platform.core.store import Store
    action=request.get('action')
    if action not in ACTIONS or set(request)-{'action','build_id','id','key','binding'}:
        raise ValueError('Unsupported bridge request')
    from delta_pipeline.onprem_independent_store import IndependentStore,catalog,settings as independent_settings
    settings=independent_settings();store=IndependentStore(catalog(settings));engine=store.engine
    try:
        if action=='catalog':
            result=[]
            services=store.list_services(50,None)
            for service in services.get('items',[]):
                if service.get('repository_url')!=REPO:continue
                for build in store.list_runs(service['id'],10,None,kind='image_build').get('items',[]):
                    try:result.append(context(store,str(build['id'])))
                    except (ValueError,KeyError,ServiceUnavailable):continue
                    if len(result)>=15:return result
            return result
        value=context(store,request['build_id'])
        if action=='context':return value
        from delta_pipeline.onprem_scanners import SAST_TARGET,DAST_TARGET,ScannerRpc
        if action=='sast_start':
            binding=request['binding']
            if binding!={'repo':REPO,'commit':value['build']['resolved_commit_sha']}:raise ValueError('Source mismatch')
            job,_=store.enqueue_security(UUID(str(value['service']['id'])),UUID(request['build_id']),request['key'],SAST_TARGET)
            return {'run_id':str(job['id']),'state':'running'}
        if action in ('sast_status','sast_report'):
            job=store.run(UUID(request['id']),'security_check')
            if (str(job['service_id'])!=str(value['service']['id']) or job['resolved_commit_sha']!=value['build']['resolved_commit_sha']):raise ValueError('Wrong SAST ownership')
            import sqlalchemy as sa
            from delta_platform.core.schema import runs
            source=store.native_source(request['id'])['input_snapshot']
            if source.get('build_id')!=request['build_id'] or source.get('security_target')!=SAST_TARGET:raise ValueError('Wrong SAST binding')
            if job['status']!='succeeded':
                if action=='sast_report':raise ValueError('SAST not complete')
                return {'run_id':request['id'],'repo':REPO,'commit':job['resolved_commit_sha'],'mode':'fast',
                    'state':'running' if job['status']in('queued','running')else 'error'}
            path='/v1/sast/scans/'+request['id']+('/sarif' if action=='sast_report' else '')
            result=ScannerRpc().request('sast','GET',path)
            if action=='sast_report':
                from delta_pipeline.clients import evidence_hash
                if evidence_hash(result)!=job['result']['evidence_sha256']:raise ValueError('SAST evidence changed')
            elif result.get('run_id')!=request['id'] or result.get('commit')!=job['resolved_commit_sha']:raise ValueError('SAST identity changed')
            return result
        if action=='start':
            binding=request['binding']
            if set(binding)!={'repo','commit','image','sast_run_id','sast_evidence_sha256'}:raise ValueError()
            native=NativeOnpremJob(store,settings,service_id=value['service']['id'],build_id=value['build']['id'],
                security_check_id=binding['sast_run_id'],backend_build_id=value.get('backend_build_id'),dast_target_override=DAST_TARGET)
            return {'id':native.start(request['key'],**binding)}
        identifier=str(UUID(request['id']))
        # Matching service alone is insufficient: retain the exact selected build.
        import sqlalchemy as sa
        from delta_platform.core.schema import runs
        row=store.native_source(identifier)
        source=row['input_snapshot']
        if not source or source.get('build_id')!=request['build_id']:raise ValueError('Wrong build ownership')
        native=NativeOnpremJob(store,settings,service_id=value['service']['id'],build_id=value['build']['id'],
            security_check_id=source.get('security_check_id'),backend_build_id=(source.get('backend')or{}).get('build_id'),dast_target_override=DAST_TARGET)
        job=native.get(identifier)
        if action=='status':return job
        if action=='inspect':
            result=native.inspect(identifier)
            try:
                from delta_pipeline.onprem_evidence import load
                record=load({'id':identifier,'input_snapshot':source,'resolved_commit_sha':row['resolved_commit_sha'],'result':job.get('result')})
                expected=(job.get('result')or{}).get('runtime_evidence_sha256')
                if expected and record['sha256']!=expected:raise ValueError('Evidence hash mismatch')
                result['preserved_logs']=record
            except Exception:result['limitations'].append('정리 전 영속 로그 근거를 검증하지 못했습니다.')
            return result
        if action=='cancel':native.cancel(identifier);return {'cancel_requested':True}
        if action=='absence':
            from delta_platform.adapters.onprem_sandbox import OnpremSandboxRunner
            runner=OnpremSandboxRunner(settings);name=runner.config.name(identifier)
            names=[name]+([name+'-be']if source.get('backend')else[])
            return {'pod_absent':all(runner.provider.call('GET','pods',n,missing=True) is None for n in names),
                'secret_absent':all(runner.provider.call('GET','secrets',n,missing=True) is None for n in names)}
        return getattr(native,action)(identifier,**request['binding'])
    finally:engine.dispose()

def main(request):
    try:out={'ok':True,'value':execute(request)}
    except Exception as error:
        out={'ok':False,'type':type(error).__name__,'code':'native_request_rejected'}
    raw=json.dumps(scrub(out),default=str,separators=(',',':')).encode()
    if len(raw)>60000:raw=json.dumps({'ok':False,'type':'ValueError','code':'native_result_too_large'}).encode()
    print(base64.b64encode(gzip.compress(raw)).decode())
