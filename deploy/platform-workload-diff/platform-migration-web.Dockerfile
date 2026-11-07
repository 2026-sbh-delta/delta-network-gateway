FROM 616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform@sha256:7fdb663c01768f714ad83076e45034b57828273591e07f8d7a6d9508c2ec3624
USER root
COPY install-migration-web.py migration-diff-entry.js /opt/delta-release/
RUN python /opt/delta-release/install-migration-web.py
USER 10001:10001
