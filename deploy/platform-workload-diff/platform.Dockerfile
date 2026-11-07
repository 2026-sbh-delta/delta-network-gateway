FROM 616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform@sha256:e68ab0f08b0d4c9bd68518164d5bdbb97f1c1325c80524a79f0c993b03c91c4e
USER root
COPY workflow_diff.py workload_inventory.py /app/backend/.venv/lib/python3.12/site-packages/delta_platform/adapters/
COPY patch.py /opt/delta-release/patch.py
RUN python /opt/delta-release/patch.py platform e15bcdd5a074d4652fba2e9267fc8a693d4381ed3184e75b52e3e0867959bd75
USER 10001:10001
