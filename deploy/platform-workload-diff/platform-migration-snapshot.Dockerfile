FROM 616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform@sha256:bfb81ab739cd18f7f8d89611935e683d768e3fde4f2fe4a09ae01e532b80c9df
USER root
COPY onprem-diff-snapshot.json /opt/delta-release/onprem-diff-snapshot.json
RUN chmod 0444 /opt/delta-release/onprem-diff-snapshot.json
USER 10001:10001
