---
rule_id: MAP-DB-001
doc_type: rule
provider: any
title: PostgreSQL 대응
---

## 판정 기준

일반 PostgreSQL을 RDS PostgreSQL·Cloud SQL for PostgreSQL·k3s StatefulSet/Service/PVC로 제안한다. Aurora·다른 엔진은 gap이다. 버전·스토리지·HA가 불명확하면 unknown이다. 대상 YAML 주 버전 불일치·부족한 PVC·HA 손실은 gap이다. 확장·콜레이션·TLS·권한·복원과 실제 데이터 보존은 별도 시험한다.

## 출처

- https://www.postgresql.org/docs/current/backup-dump.html
