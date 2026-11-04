import { useEffect, useState } from "react";
import { message, type Service } from "./api";
import {
  cloudName,
  roleName,
  runLabel,
  componentCheckLabel,
  latestCheck,
  loadApplication,
  stamp,
  type ApplicationState,
} from "./application-model";
import {
  observabilityApi,
  type Environment,
  type Observability,
} from "./observability-api";
import type { View } from "./AppShell";

function EnvironmentCard({
  serviceId,
  environment,
}: {
  serviceId: string;
  environment: Environment;
}) {
  const [snapshot, setSnapshot] = useState<Observability | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function read() {
      try {
        const data = await observabilityApi.snapshot(
          serviceId,
          environment.id,
          15,
          controller.signal,
        );
        if (!controller.signal.aborted) {
          if (
            data.service_id !== serviceId ||
            data.environment.id !== environment.id
          )
            throw new Error("identity mismatch");
          setSnapshot(data);
          setError("");
        }
      } catch (e) {
        if (!controller.signal.aborted) {
          setError(message(e));
          setSnapshot(null);
        }
      }
      if (!controller.signal.aborted) timer = setTimeout(read, 30000);
    }
    void read();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [serviceId, environment.id]);
  const metric = snapshot?.metrics.app_up;
  const state = error
    ? "수집 실패"
    : !metric
      ? "상태 조회 중"
      : metric.status === "ok" && metric.value !== null
        ? metric.value === 1
          ? "앱 응답 확인"
          : "앱 응답 없음"
        : {
            ok: "데이터 없음",
            no_data: "데이터 없음",
            stale: "수집 지연",
            error: "수집 실패",
            unsupported: "미지원",
          }[metric.status];
  return (
    <div className="release-env-instance">
      <div className="release-line">
        <strong>{environment.name}</strong>
        <span
          className={`release-state ${metric?.status === "ok" && metric.value === 1 ? "good" : ""}`}
        >
          {state}
        </span>
      </div>
      <dl>
        <div>
          <dt>등록 버전</dt>
          <dd>{environment.version ?? "미등록"}</dd>
        </div>
        <div>
          <dt>마지막 앱 관측</dt>
          <dd>{stamp(metric?.collected_at)}</dd>
        </div>
      </dl>
      {error && <p className="muted">{error}</p>}
      <p className="release-caption">
        등록 버전과 실제 응답 버전의 대조는 별도 확인이 필요합니다.
      </p>
    </div>
  );
}
export function ApplicationPanel({
  service,
  mode = "overview",
  onNavigate,
}: {
  service: Service;
  mode?: "overview" | "build";
  onNavigate: (view: View) => void;
}) {
  const [data, setData] = useState<ApplicationState | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setData(null);
    setError("");
    async function read() {
      try {
        const next = await loadApplication(service, controller.signal);
        if (!controller.signal.aborted) {
          setData(next);
          setError("");
        }
      } catch (e) {
        if (!controller.signal.aborted) {
          setData(null);
          setError(message(e));
        }
      }
      if (!controller.signal.aborted) timer = setTimeout(read, 30000);
    }
    void read();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [service, refresh]);
  if (error)
    return (
      <section className="panel release-error" role="alert">
        <strong>배포 상태를 불러오지 못했습니다.</strong>
        <p>{error}</p>
        <button onClick={() => setRefresh((n) => n + 1)}>다시 조회</button>
      </section>
    );
  if (!data)
    return (
      <section className="panel" aria-busy="true">
        배포 상태를 확인하고 있습니다…
      </section>
    );
  const built = data.components.every(
    (c) => c.build?.status === "succeeded" && c.build.result?.image_digest,
  );
  const hasFinding = data.components.some(
    (c) =>
      (latestCheck(c, "security_check")?.result?.findings?.length ?? 0) > 0,
  );
  const latestBuild = data.components
    .map((c) => c.build)
    .find(
      (r) =>
        r?.status === "failed" ||
        r?.status === "running" ||
        r?.status === "queued",
    );
  return (
    <div className="release-workspace">
      <section className="release-summary panel" aria-label="배포 준비 요약">
        <div>
          <div className="eyebrow">APPLICATION DELIVERY</div>
          <h2>
            {latestBuild
              ? `이미지 빌드 ${runLabel[latestBuild.status]}`
              : built
                ? "검사 결과를 확인하세요"
                : "배포할 버전을 준비하세요"}
          </h2>
          <p>
            소스와 이미지를 확인하고, 대상 환경의 검사 결과를 바탕으로 다음
            단계를 결정합니다.
          </p>
        </div>
        <button
          className="primary"
          onClick={() => onNavigate(mode === "build" ? "migration" : "build")}
        >
          {mode === "build" ? "환경·계획 확인" : "빌드·검사 확인"}{" "}
          <span aria-hidden="true">→</span>
        </button>
      </section>
      <nav className="release-steps" aria-label="발표 진행 흐름">
        {[
          [
            "01",
            "버전 준비",
            built ? "이미지 생성 완료" : "빌드 결과 확인",
            "build",
          ],
          ["02", "환경 차이", "대상별 판단 확인", "migration"],
          ["03", "계획 승인", "승인 기록 확인", "migration"],
          ["04", "운영 확인", "실제 관측 확인", "monitoring"],
        ].map(([n, title, detail, view]) => (
          <button key={n} onClick={() => onNavigate(view as View)}>
            <span>{n}</span>
            <div>
              <strong>{title}</strong>
              <small>{detail}</small>
            </div>
          </button>
        ))}
      </nav>
      <section className="panel release-bundle" aria-label="애플리케이션 구성">
        <div className="section-heading">
          <div>
            <span className="eyebrow">RELEASE CONTENTS</span>
            <h2>애플리케이션 구성</h2>
          </div>
          <span className="release-state">
            {data.bundle ? "FE + BE" : "단일 서비스 기록"}
          </span>
        </div>
        {!data.bundle && (
          <p className="release-notice">
            FE·BE 배포 묶음 미연결 · 현재 등록된 서비스의 결과만 표시합니다. 한
            구성 요소의 성공은 전체 앱의 성공을 의미하지 않습니다.
          </p>
        )}
        <div className="release-components">
          {data.components.map((c) => (
            <article className="release-component" key={c.service.id}>
              <div className="release-line">
                <span className="release-role">{roleName[c.role]}</span>
                <span
                  className={`release-state ${c.build?.status === "failed" ? "bad" : c.build?.status === "succeeded" ? "good" : ""}`}
                >
                  {c.build ? `빌드 ${runLabel[c.build.status]}` : "빌드 전"}
                </span>
              </div>
              <h3>{c.service.name}</h3>
              <p className="release-commit">
                {c.build?.resolved_commit_sha?.slice(0, 12) ??
                  c.service.source_ref}
                <span> · {c.service.build_context}</span>
              </p>
              <div className="release-check-row">
                <span>SAST</span>
                <b>{componentCheckLabel(c, "security_check")}</b>
              </div>
              {c.build?.error && (
                <p className="error">{c.build.error.message}</p>
              )}
              <details>
                <summary>커밋·이미지와 실행 정보</summary>
                <dl>
                  <div>
                    <dt>커밋</dt>
                    <dd>
                      <code>{c.build?.resolved_commit_sha ?? "미확정"}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>이미지 digest</dt>
                    <dd>
                      <code>{c.build?.result?.image_digest ?? "미생성"}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>실행 경로</dt>
                    <dd>
                      {c.service.container_port} · {c.service.healthcheck_path}
                    </dd>
                  </div>
                  <div>
                    <dt>빌드 접수</dt>
                    <dd>{stamp(c.build?.created_at)}</dd>
                  </div>
                </dl>
                {c.historyIncomplete && (
                  <p>
                    최근 검사 이력만 표시합니다. 이전 기록은 검사 상세에서
                    확인하세요.
                  </p>
                )}
                <a href={`#/build/${encodeURIComponent(c.service.id)}`}>
                  구성 요소 빌드·검사 열기 →
                </a>
              </details>
            </article>
          ))}
        </div>
        <div className="release-database">
          <strong>데이터 저장</strong>
          <span>
            {data.bundle?.database === "memory"
              ? "DB 미구성 · 메모리 저장, 재시작 시 데이터 소실"
              : data.bundle?.database === "none"
                ? "DB 미구성"
                : data.bundle?.database === "external"
                  ? "외부 DB · 연결·이전 상태는 모니터링에서 확인"
                  : "DB 구성 확인 필요"}
          </span>
        </div>
      </section>
      {mode === "overview" ? (
        <section aria-label="환경별 현재 상태">
          <div className="section-heading">
            <h2>환경별 현재 상태</h2>
            <span>실제 수집 · 30초 갱신</span>
          </div>
          <div className="release-environments">
            {(["aws", "gcp", "onprem"] as const).map((cloud) => (
              <article
                className={`panel release-environment cloud-${cloud}`}
                key={cloud}
              >
                <div className="release-line">
                  <h3>{cloudName[cloud]}</h3>
                  <span className="release-caption">
                    {cloud === "aws" ? "출발 환경 확인" : "대상 환경 확인"}
                  </span>
                </div>
                {data.environments.filter((e) => e.cloud === cloud).length ? (
                  data.environments
                    .filter((e) => e.cloud === cloud)
                    .map((env) => (
                      <EnvironmentCard
                        key={env.id}
                        serviceId={service.id}
                        environment={env}
                      />
                    ))
                ) : (
                  <div className="release-empty">
                    <strong>미연결</strong>
                    <p>이 서비스에 등록된 환경이 없습니다.</p>
                  </div>
                )}
              </article>
            ))}
          </div>
          <p className="release-caption">
            검증된 서비스 주소·응답 버전이 연결되면 서비스 접속 결과를 함께
            확인할 수 있습니다.
          </p>
        </section>
      ) : (
        <section aria-label="대상별 검사 결과">
          <div className="section-heading">
            <h2>대상별 검사 결과</h2>
            <span>각 환경의 결과를 개별 확인</span>
          </div>
          <div className="release-environments">
            {(["aws", "gcp", "onprem"] as const).map((cloud) => (
              <article className="panel release-environment" key={cloud}>
                <h3>{cloudName[cloud]}</h3>
                {data.components.map((c) => {
                  const runtime = latestCheck(c, "sandbox_check", cloud);
                  const dast = latestCheck(c, "dast_check", cloud);
                  return (
                    <div className="release-target-check" key={c.service.id}>
                      <strong>{roleName[c.role]}</strong>
                      <div className="release-check-row">
                        <span>실행 검사</span>
                        <b>{componentCheckLabel(c, "sandbox_check", cloud)}</b>
                      </div>
                      <div className="release-check-row">
                        <span>DAST</span>
                        <b>{componentCheckLabel(c, "dast_check", cloud)}</b>
                      </div>
                      {dast?.result?.cleanup && (
                        <p>
                          샌드박스 정리:{" "}
                          {["completed", "stopped"].includes(
                            dast.result.cleanup,
                          )
                            ? "완료"
                            : "확인 필요"}
                        </p>
                      )}
                      <small>
                        {stamp(dast?.created_at ?? runtime?.created_at)}
                      </small>
                    </div>
                  );
                })}
                <p className="release-caption">
                  다음 행동: 아래 검사 상세에서 대상·이미지·정리 상태 확인
                </p>
              </article>
            ))}
          </div>
          <p className="release-notice">
            실행 대상 또는 빌드 ID가 없는 기록은 성공 결과로 합산하지 않습니다.
            실행·취소·재시도는 아래 기존 검사 화면에서 진행하세요.
          </p>
          {hasFinding && (
            <div className="release-remediation">
              <strong>발견 사항을 검토하세요</strong>
              <p>발견 내용 → AI 수정안 → 사람 승인 → 새 빌드 → 재검사</p>
              <span>
                AI 코드 수정·승인 연동 대기 · 현재 발견 사항은 보안 검사
                상세에서 확인합니다.
              </span>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
