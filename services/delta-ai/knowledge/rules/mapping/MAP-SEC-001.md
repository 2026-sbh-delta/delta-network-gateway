---
rule_id: MAP-SEC-001
doc_type: rule
provider: any
title: 보안 도구 능력 대응
---

## 왜 위험한가

GuardDuty 하나에 대응하는 단일 제품은 없다. Security Command Center 의 Event, Container, VM Threat Detection 은 탐지 범위가 나뉘고 Premium 이상에서 쓴다. 거꾸로 SCC 를 AWS 로 옮기면 GuardDuty 기본 탐지와 Runtime Monitoring 같은 기능을 따로 켜야 하고, VM Threat Detection 과 달리 Runtime Monitoring 은 인스턴스에 에이전트가 있어야 한다. GuardDuty 와 SCC 사이 공식 대응표는 없어 이 대응은 팀 판단이며 같은 탐지를 보장하지 않는다.

## 판정 기준

GuardDuty 기능은 API 이름으로 받는다. EKS_AUDIT_LOGS 는 Event, EKS_RUNTIME_MONITORING 은 Container, RUNTIME_MONITORING 은 Container 와 VM, EBS_MALWARE_PROTECTION 은 VM Threat Detection 으로 본다. S3_DATA_EVENTS, RDS_LOGIN_EVENTS, LAMBDA_NETWORK_LOGS 는 대응을 정하지 않았다. 켜진 능력을 덮는 가장 작은 조합을 고른다. 하나면 approximate, 여럿이면 split 이다. 등급을 모르면 unknown, Premium 이 없거나 대응 없는 플랜이면 gap 이다. 사용 여부를 모르면 unknown 이고, 꺼진 것으로 보지 않는다.

GCP → AWS 는 켠 SCC 서비스 이름을 받는다. Event Threat Detection 은 GuardDuty 기본 탐지가, Container 와 VM Threat Detection 은 RUNTIME_MONITORING 이 덮는다고 본다. 기능을 더 켜야 하면 split, 기본 탐지만이면 approximate 이다. GuardDuty 는 등급이 없어 등급을 묻지 않는다. Security Health Analytics 처럼 대응을 정하지 않은 서비스는 gap 이고, 켠 서비스 목록을 모르면 unknown 이다.

## 고치는 법

AWS → GCP 는 Premium 을 쓸지, 능력 손실을 승인할지 사람이 정한다. GCP → AWS 는 대응 없는 서비스의 손실을 승인할지 사람이 정하고, Runtime Monitoring 에이전트가 대상 인스턴스에서 도는지 확인한다. 승인한 능력은 accepted_gaps 에 넣고 표에는 gap 으로 남긴다. 탐지가 쓰는 감사 로그를 켜고 샘플 탐지로 확인한다.

## 출처

- https://docs.aws.amazon.com/guardduty/latest/ug/what-is-guardduty.html
- https://docs.cloud.google.com/security-command-center/docs/service-tiers
- https://docs.cloud.google.com/security-command-center/docs/concepts-event-threat-detection-overview
- https://docs.aws.amazon.com/guardduty/latest/ug/runtime-monitoring.html
