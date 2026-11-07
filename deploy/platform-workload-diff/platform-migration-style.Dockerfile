FROM 616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform@sha256:93f6aac65aec6c7fc6b4b4cb8a5209b8d4fbb07e5edb299cd0724f9ed50253b6
USER root
COPY install-migration-web.py migration-diff.css /opt/delta-release/
RUN python /opt/delta-release/install-migration-web.py --style-only
USER 10001:10001
