import { useEffect, useState } from "react";
import { api, message } from "./api";
import type { View } from "./AppShell";
import { cloudName } from "./application-model";
import { observabilityApi, type Environment } from "./observability-api";
import "./deployed-environments.css";

export function DeployedEnvironments({
  serviceId,
  onNavigate,
}: {
  serviceId: string;
  onNavigate: (view: View) => void;
}) {
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  // A pipeline still running (started from the migration screen) gets a way back from here.
  const [liveRun, setLiveRun] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    api<{ runs: { run_id: string; outcome: string | null }[] }>(
      "/pipeline/runs",
      undefined,
      undefined,
      controller.signal,
    )
      .then((data) => {
        const run = data.runs[0];
        if (!controller.signal.aborted && run && !run.outcome)
          setLiveRun(run.run_id);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [serviceId]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setEnvironments([]);
    setLoading(true);
    setError("");

    async function refresh() {
      try {
        const result = await observabilityApi.environments(
          serviceId,
          controller.signal,
        );
        if (!controller.signal.aborted) {
          setEnvironments(result.items);
          setError("");
        }
      } catch (e) {
        if (!controller.signal.aborted) setError(message(e));
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          timer = setTimeout(refresh, 10000);
        }
      }
    }

    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [serviceId]);

  return (
    <section className="panel deployed-environments" aria-label="배포 환경">
      <div className="panel-top">
        <div>
          <h2>배포 환경</h2>
          <p>
            현재 서비스가 배포된 위치입니다. 실제 응답과 지표는 대시보드에서
            확인하세요.
          </p>
        </div>
        <div className="deployment-actions">
          {liveRun && (
            <button
              onClick={() => {
                location.hash = `/pipeline/${encodeURIComponent(liveRun)}`;
              }}
            >
              파이프라인 진행 중 →
            </button>
          )}
          <button onClick={() => onNavigate("migration")}>
            마이그레이션 보기
          </button>
          <button className="primary" onClick={() => onNavigate("monitoring")}>
            대시보드 열기
          </button>
        </div>
      </div>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {loading && <p role="status">배포 환경을 확인하고 있습니다…</p>}
      {!loading && !error && environments.length === 0 && (
        <p className="muted">연결된 배포 환경이 없습니다.</p>
      )}

      <div className="deployment-environment-cards">
        {environments.map((environment) => (
          <article
            key={environment.id}
            className={`deployment-environment-card cloud-${environment.cloud}`}
            aria-label={`${cloudName[environment.cloud]} 배포 환경`}
          >
            <div className="release-line">
              <h3>{cloudName[environment.cloud]}</h3>
              <span className="release-state">
                {environment.runtime_status === "removed"
                  ? "앱 삭제됨"
                  : environment.runtime_status === "removing"
                    ? "앱 정리 중"
                    : "배포 등록됨"}
              </span>
            </div>
            <dl>
              <div>
                <dt>리전</dt>
                <dd>{environment.region}</dd>
              </div>
              <div>
                <dt>
                  {environment.cloud === "aws"
                    ? "계정"
                    : environment.cloud === "gcp"
                      ? "프로젝트"
                      : "호스트"}
                </dt>
                <dd>
                  {environment.cloud === "aws"
                    ? (environment.account_id ?? "미등록")
                    : environment.cloud === "gcp"
                      ? (environment.gcp_project ?? "미등록")
                      : "팀 온프레미스 서버"}
                </dd>
              </div>
              <div>
                <dt>배포 버전</dt>
                <dd>
                  <code>{environment.version ?? "미등록"}</code>
                </dd>
              </div>
              {environment.instances?.map((instance) => (
                <div key={instance.id}>
                  <dt>
                    {environment.cloud === "onprem" ? "실행 호스트" : "VM"}
                  </dt>
                  <dd>
                    {instance.name}
                    <code>{instance.id}</code>
                    {instance.zone && <small>{instance.zone}</small>}
                  </dd>
                </div>
              ))}
              {environment.database_profile_id && (
                <div>
                  <dt>DB 생성 설정</dt>
                  <dd>
                    {environment.database_profile_id}
                    <small>
                      DB 이름이 아닌 설정 ID입니다. 앱 DB는 환경별 고유 이름으로
                      생성됩니다.
                    </small>
                  </dd>
                </div>
              )}
            </dl>
          </article>
        ))}
      </div>
    </section>
  );
}
