"""Private durable queue; platform PostgreSQL is read-only except its advisory lock."""
import hashlib,json,os,secrets,sqlite3,time
from contextlib import contextmanager
from datetime import UTC,datetime
from pathlib import Path
from uuid import UUID,uuid4

ROOT=Path('/var/lib/delta-platform/tmp')

def settings():
    from argon2 import PasswordHasher
    from delta_platform.config import Settings
    return Settings(login_id='independent-onprem-executor',password_hash=PasswordHasher().hash(secrets.token_urlsafe(32)),public_origin='http://127.0.0.1',local_mode=True,_env_file=None)

def catalog(settings):
    import sqlalchemy as sa
    from delta_platform.core.database import engine_for
    from delta_platform.core.store import Store
    engine=engine_for(settings)
    @sa.event.listens_for(engine,'connect')
    def readonly(connection,_):
        with connection.cursor()as cursor:cursor.execute('SET default_transaction_read_only = on')
        connection.commit()
    return Store(engine)

class IndependentStore:
    def __init__(self,catalog,root=ROOT):
        self.catalog,self.engine=catalog,catalog.engine
        root.mkdir(mode=0o700,parents=True,exist_ok=True)
        if root.is_symlink():raise ValueError('Unsafe private queue')
        self.path=root/'onprem-queue.sqlite'
        if self.path.is_symlink():raise ValueError('Unsafe private queue')
        with self.db()as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,service_id TEXT,kind TEXT,key TEXT,created REAL,status TEXT,lease_until REAL,token TEXT,attempt INTEGER DEFAULT 0,data TEXT,UNIQUE(service_id,kind,key));
CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT,data TEXT);''')
        os.chmod(self.path,0o600)
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
        try:
            db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()
    def service(self,*args):return self.catalog.service(*args)
    def list_services(self,*args):return self.catalog.list_services(*args)
    def list_runs(self,*args,**kwargs):return self.catalog.list_runs(*args,**kwargs)
    def build_source(self,identifier):
        import sqlalchemy as sa
        from delta_platform.core.schema import runs
        with self.engine.connect()as conn:
            return conn.scalar(sa.select(runs.c.input_snapshot).where(runs.c.id==UUID(str(identifier)),runs.c.kind=='image_build'))
    def run(self,identifier,kind=None):
        if kind=='image_build':return self.catalog.run(UUID(str(identifier)),kind)
        with self.db()as db:row=db.execute('SELECT * FROM jobs WHERE id=?',(str(identifier),)).fetchone()
        if row is None or kind and row['kind']!=kind:raise ValueError('Private run not found')
        return json.loads(row['data'])
    def native_source(self,identifier):
        with self.db()as db:row=db.execute('SELECT * FROM jobs WHERE id=?',(str(identifier),)).fetchone()
        if row is None:raise ValueError('Private run not found')
        value=json.loads(row['data'])
        return {'input_snapshot':value['input_snapshot'],'request_key':row['key'],'resolved_commit_sha':value['resolved_commit_sha']}
    def native_submission(self,identifier):return self.run(identifier).get('submission')
    def _event(self,db,rid,event_type,message,step=None):
        db.execute('INSERT INTO events(run_id,data) VALUES(?,?)',(str(rid),json.dumps({'event_type':event_type,'message':message,'step':step})))
    def enqueue(self,service_id,kind,key,source,commit):
        from delta_platform.core.errors import Problem
        if not isinstance(key,str)or not 1<=len(key)<=128:raise ValueError('Invalid request key')
        signature=hashlib.sha256(json.dumps(source,sort_keys=True).encode()).hexdigest()
        with self.db()as db:
            row=db.execute('SELECT * FROM jobs WHERE service_id=? AND kind=? AND key=?',(str(service_id),kind,key)).fetchone()
            if row:
                value=json.loads(row['data'])
                if value['request_hash']!=signature or value['resolved_commit_sha']!=commit:raise Problem('idempotency_conflict','동일 요청 키의 검사 바인딩이 다릅니다.',409)
                return value,False
            rid=str(uuid4());created=datetime.now(UTC).isoformat()
            value={'id':rid,'service_id':str(service_id),'kind':kind,'status':'queued','current_step':None,'resolved_commit_sha':commit,
                'input_snapshot':source,'request_hash':signature,'external_id':None,'submission':None,'result':None,'error':None,
                'created_at':created,'started_at':None,'finished_at':None,'attempt':0,'cancel_requested':False,'execution_owner':'orchestrator-private'}
            db.execute('INSERT INTO jobs(id,service_id,kind,key,created,status,data)VALUES(?,?,?,?,?,?,?)',(rid,str(service_id),kind,key,time.time(),'queued',json.dumps(value)))
            self._event(db,rid,'queued','독립 오케스트레이터 실행 큐에 접수했습니다.')
            return value,True
    def enqueue_security(self,service_id,build_id,key,target):
        build=self.run(build_id,'image_build');source=self.build_source(build_id)
        if str(build['service_id'])!=str(service_id) or build['status']!='succeeded':raise ValueError('Successful bound build required')
        result=build['result']
        return self.enqueue(service_id,'security_check',key,{'build_id':str(build_id),'image':result['image'],'image_digest':result['image_digest'],
            'repository_url':source['repository_url'],'security_target':target},build['resolved_commit_sha'])
    def enqueue_sandbox(self,service_id,build_id,key,target,*,backend_build_id=None,security_check_id=None,dast_target=None):
        build=self.run(build_id,'image_build');source=self.build_source(build_id);result=build['result']
        if str(build['service_id'])!=str(service_id)or build['status']!='succeeded':raise ValueError('Bound build required')
        snapshot={'build_id':str(build_id),'image':result['image'],'image_digest':result['image_digest'],'container_port':source['container_port'],
            'healthcheck_path':source['healthcheck_path'],'sandbox_target':target,'execution_cloud':'onprem'}
        if backend_build_id:
            backend=self.run(backend_build_id,'image_build');other=self.service(UUID(str(backend['service_id'])));service=self.service(service_id)
            original=self.build_source(backend_build_id)
            if (not service.get('project_id')or service['project_id']!=other.get('project_id')or backend['status']!='succeeded'
                    or build['resolved_commit_sha']!=backend['resolved_commit_sha']or original['repository_url']!=source['repository_url']
                    or original['container_port']!=8080 or backend['result'].get('platform')!='linux/amd64'):raise ValueError('Backend binding differs')
            snapshot.update(project_id=str(service['project_id']),backend={'build_id':str(backend_build_id),'service_id':str(backend['service_id']),
                'image':backend['result']['image'],'image_digest':backend['result']['image_digest'],'container_port':8080,'healthcheck_path':original['healthcheck_path']})
        if security_check_id:
            security=self.run(security_check_id,'security_check')
            if (security['status']!='succeeded'or security['input_snapshot']['build_id']!=str(build_id)
                    or security['result']['commit_sha']!=build['resolved_commit_sha']):raise ValueError('SAST binding differs')
            snapshot.update(security_check_id=str(security_check_id),sast_evidence_sha256=security['result']['evidence_sha256'],
                security_target=security['input_snapshot']['security_target'],repository_url=source['repository_url'],dast_target=dast_target)
        return self.enqueue(service_id,'dast_check'if security_check_id else'sandbox_check',key,snapshot,build['resolved_commit_sha'])
    def claim(self):
        with self.db()as db:
            row=db.execute("SELECT * FROM jobs WHERE status='queued' OR(status='running'AND lease_until<?) ORDER BY created LIMIT 1",(time.time(),)).fetchone()
            if not row:return None
            value=json.loads(row['data']);token=uuid4().hex;attempt=row['attempt']+1
            value.update(status='running',attempt=attempt,started_at=value.get('started_at')or datetime.now(UTC).isoformat())
            db.execute("UPDATE jobs SET status='running',token=?,lease_until=?,attempt=?,data=? WHERE id=?",(token,time.time()+90,attempt,json.dumps(value),row['id']))
            self._event(db,row['id'],'started','독립 실행기가 작업을 시작하거나 복구합니다.')
        return {**value,'id':UUID(value['id']),'service_id':UUID(value['service_id']),'lease_token':token,'created_at':datetime.fromisoformat(value['created_at'])}
    def _owned(self,db,rid,token):
        from delta_platform.core.errors import LeaseLost
        row=db.execute('SELECT * FROM jobs WHERE id=?',(str(rid),)).fetchone()
        if not row or row['status']!='running'or row['token']!=str(token)or row['lease_until']<time.time():raise LeaseLost()
        return json.loads(row['data'])
    def heartbeat(self,rid,token):
        with self.db()as db:
            self._owned(db,rid,token);db.execute('UPDATE jobs SET lease_until=? WHERE id=?',(time.time()+90,str(rid)))
    def progress(self,rid,token,step,message,sha=None):
        with self.db()as db:
            value=self._owned(db,rid,token);value['current_step']=step
            if sha and sha!=value['resolved_commit_sha']:raise ValueError('Commit changed')
            db.execute('UPDATE jobs SET data=? WHERE id=?',(json.dumps(value),str(rid)));self._event(db,rid,'progress',message,step)
    def attach_sandbox(self,rid,token,resource):
        with self.db()as db:
            value=self._owned(db,rid,token)
            if value['external_id']not in(None,resource):raise ValueError('Resource changed')
            value['external_id']=resource;db.execute('UPDATE jobs SET data=? WHERE id=?',(json.dumps(value),str(rid)))
    def sandbox_checkpoint(self,rid,token,outcome):
        with self.db()as db:
            value=self._owned(db,rid,token)
            if value['submission']:return value['submission']
            value['submission']=outcome;db.execute('UPDATE jobs SET data=? WHERE id=?',(json.dumps(value),str(rid)));return outcome
    def sandbox_control(self,rid,token):
        with self.db()as db:return self._owned(db,rid,token)['cancel_requested']
    def sandbox_lock(self):return self.catalog.sandbox_lock()
    def cancel_sandbox(self,rid,kind='sandbox_check'):
        with self.db()as db:
            row=db.execute('SELECT * FROM jobs WHERE id=? AND kind=?',(str(rid),kind)).fetchone()
            if not row:raise ValueError('Private run not found')
            value=json.loads(row['data'])
            if row['status']not in ('queued','running'):return value
            value['cancel_requested']=True
            if row['status']=='queued'and not value['external_id']:
                value.update(status='failed',finished_at=datetime.now(UTC).isoformat(),error={'code':'sandbox_cancelled','message':'실행 전에 취소했습니다.'})
            db.execute('UPDATE jobs SET data=?,status=? WHERE id=?',(json.dumps(value),value['status'],str(rid)));self._event(db,rid,'cancel_requested','독립 실행 중단을 요청했습니다.')
        return value
    def finish(self,rid,token,*,result=None,error=None,retry=False):
        with self.db()as db:
            value=self._owned(db,rid,token);value.update(result=result,error=error,finished_at=datetime.now(UTC).isoformat(),status='failed'if error else'succeeded')
            if retry and value['attempt']<3:value.update(status='queued',finished_at=None)
            db.execute('UPDATE jobs SET status=?,token=NULL,lease_until=NULL,data=? WHERE id=?',(value['status'],json.dumps(value),str(rid)))
            self._event(db,rid,value['status'],'독립 검사 실행을 완료했습니다. 보안 승인과는 별개입니다.',value['current_step'])
    def run_events(self,rid,after,limit):
        with self.db()as db:rows=db.execute('SELECT * FROM events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?',(str(rid),after,min(limit,40))).fetchall()
        return [{'sequence':row['sequence'],**json.loads(row['data'])}for row in rows]
