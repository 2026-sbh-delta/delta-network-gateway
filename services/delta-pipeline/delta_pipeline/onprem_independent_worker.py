"""Independent container/queue, no shared Worker replacement or platform DB writes."""
import logging,signal,threading

def main():
    from delta_pipeline.onprem_independent_store import IndependentStore,catalog,settings
    from delta_pipeline.onprem_scanners import security_runner
    from delta_pipeline.onprem_evidence import runner
    from delta_platform.worker.main import process_one
    config=settings();store=IndependentStore(catalog(config));stop=threading.Event()
    for sig in(signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    logging.basicConfig(level=logging.INFO)
    try:
        while not stop.is_set():
            try:worked=process_one(store,None,security=security_runner(config),dast=runner(config),sandbox=runner(config))
            except Exception:logging.getLogger('onprem_executor').error('private_queue_unavailable');worked=False
            if not worked:stop.wait(1)
    finally:store.engine.dispose()
if __name__=='__main__':main()
