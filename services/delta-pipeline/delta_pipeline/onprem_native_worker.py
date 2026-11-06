"""Composition root for native scanner/evidence extensions; platform sources stay intact."""
import logging
import signal
import threading
import time

def main():
    from delta_platform.config import Settings
    from delta_platform.core.database import engine_for,ready
    from delta_platform.core.store import Store
    from delta_platform.core.auth import Auth
    from delta_platform.core.applications import ApplicationStore
    from delta_platform.adapters.github import GitHubReader
    from delta_platform.adapters.security import SecurityRunner
    from delta_platform.adapters.dast import DastInspector,DastRunner
    from delta_platform.adapters.sandbox import runner_for
    from delta_platform.worker.main import process_one
    from delta_pipeline.onprem_scanners import SAST_TARGET,DAST_TARGET,security_runner
    from delta_pipeline.onprem_evidence import runner
    settings=Settings();engine=engine_for(settings)
    if not ready(engine):raise SystemExit('DB migration required')
    store,auth,reader=Store(engine),Auth(engine,settings),GitHubReader()
    class Security:
        def execute(self,claim,store,progress):
            active=security_runner(settings) if claim['input_snapshot']['security_target']==SAST_TARGET else SecurityRunner(settings)
            return active.execute(claim,store,progress)
    class Dast:
        def execute(self,claim,store,progress):
            source=claim['input_snapshot'];cloud=source['sandbox_target'].get('cloud','aws')
            active=(runner(settings) if cloud=='onprem' and source.get('dast_target')==DAST_TARGET else
                DastRunner(settings) if cloud=='aws' else runner_for(settings,cloud,inspector=DastInspector(settings)))
            return active.execute(claim,store,progress)
    class Sandbox:
        def execute(self,claim,store,progress):
            cloud=claim['input_snapshot']['sandbox_target'].get('cloud','aws')
            active=runner(settings) if cloud=='onprem' else runner_for(settings,cloud)
            return active.execute(claim,store,progress)
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(name)s %(message)s')
    applications=ApplicationStore(engine,settings);next_cleanup=0
    try:
        while not stop.is_set():
            try:
                if time.monotonic()>=next_cleanup:auth.cleanup();next_cleanup=time.monotonic()+60
                applications.tick()
                worked=process_one(store,reader,security=Security(),dast=Dast(),sandbox=Sandbox())
            except Exception:
                logging.getLogger('delta_platform.worker').error('worker_store_unavailable');worked=False
            if not worked:stop.wait(1)
    finally:reader.close();engine.dispose()

if __name__=='__main__':main()
