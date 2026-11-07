FROM 616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-cp-orchestrator@sha256:895705a37ccdc673514ab6be0f33bd34f29e0685625909357f76e96c768c21d6
USER root
COPY platform_diff.py /usr/local/lib/python3.13/site-packages/delta_orchestrator/platform_diff.py
COPY patch.py /opt/delta-release/patch.py
RUN python /opt/delta-release/patch.py orchestrator 9134851fb3c61cbf2f240b685259987d3bb3b761c2bec5bd8c39b67965758bb4
USER 10001:10001
