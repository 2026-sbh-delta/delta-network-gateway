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
ACTIONS={'catalog','context','start','status','cancel','inspect','retry_ready','resume_ready','observation_ready','absence'}

def context(store, identifier):
    build=store.run(UUID(identifier),'image_build')
    service=store.service(UUID(str(build['service_id'])))
    if service['repository_url']!=REPO or service.get('build_context')!='backend':
        raise ValueError('Unsupported registered source')
    registered=RegisteredBuild.from_snapshot(build,service=service)
    return {'service':{k:service[k] for k in ('id','name','repository_url')},
        'build':{k:build[k] for k in ('id','kind','status','service_id','resolved_commit_sha')} | {'result':{'image':registered.image}}}

def execute(request):
    from delta_platform.config import Settings
    from delta_platform.core.database import engine_for
    from delta_platform.core.store import Store
    action=request.get('action')
    if action not in ACTIONS or set(request)-{'action','build_id','id','key','binding'}:
        raise ValueError('Unsupported bridge request')
    settings=Settings();engine=engine_for(settings);store=Store(engine)
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
        native=NativeOnpremJob(store,settings,service_id=value['service']['id'],build_id=value['build']['id'])
        if action=='start':
            binding=request['binding']
            if set(binding)!={'repo','commit','image','sast_run_id','sast_evidence_sha256'}:raise ValueError()
            return {'id':native.start(request['key'],**binding)}
        identifier=str(UUID(request['id']))
        job=native.get(identifier)
        # Matching service alone is insufficient: retain the exact selected build.
        import sqlalchemy as sa
        from delta_platform.core.schema import runs
        with engine.connect() as conn:
            source=conn.scalar(sa.select(runs.c.input_snapshot).where(runs.c.id==UUID(identifier)))
        if not source or source.get('build_id')!=request['build_id']:raise ValueError('Wrong build ownership')
        if action=='status':return job
        if action=='inspect':return native.inspect(identifier)
        if action=='cancel':native.cancel(identifier);return {'cancel_requested':True}
        if action=='absence':
            from delta_platform.adapters.onprem_sandbox import OnpremSandboxRunner
            runner=OnpremSandboxRunner(settings);name=runner.config.name(identifier)
            return {'pod_absent':runner.provider.call('GET','pods',name,missing=True) is None,
                'secret_absent':runner.provider.call('GET','secrets',name,missing=True) is None}
        return getattr(native,action)(identifier,**request['binding'])
    finally:engine.dispose()

def main(request):
    try:out={'ok':True,'value':execute(request)}
    except Exception as error:
        out={'ok':False,'type':type(error).__name__,'code':'native_request_rejected'}
    raw=json.dumps(scrub(out),default=str,separators=(',',':')).encode()
    if len(raw)>60000:raw=json.dumps({'ok':False,'type':'ValueError','code':'native_result_too_large'}).encode()
    print(base64.b64encode(gzip.compress(raw)).decode())
