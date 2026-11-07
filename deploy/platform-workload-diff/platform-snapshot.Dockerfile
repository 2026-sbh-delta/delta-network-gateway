FROM 616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform@sha256:59f7086b8867ad226ab05af28479bc1e668dc346152a824b1569c0805f47a46d
USER root
COPY workflow_diff.py workload_inventory.py /app/backend/.venv/lib/python3.12/site-packages/delta_platform/adapters/
COPY onprem-diff-snapshot.json /opt/delta-release/onprem-diff-snapshot.json
RUN chmod 0444 /opt/delta-release/onprem-diff-snapshot.json
USER 10001:10001
