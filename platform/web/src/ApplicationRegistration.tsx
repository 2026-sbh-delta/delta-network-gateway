import { useEffect, useRef, useState } from "react";
import { api, message, type DeploymentOption, type Service } from "./api";
import "./registration.css";

type Database = {
  id: string;
  label: string;
  engine: string;
  modes: string[];
  clouds: string[];
  server_mode?: "create" | "prepared";
};
export function ApplicationRegistration({
  onCreated,
  onCancel,
  onSingle,
}: {
  onCreated: (service: Service) => void;
  onCancel: () => void;
  onSingle: () => void;
}) {
  const [options, setOptions] = useState<DeploymentOption[]>([]);
  const [databases, setDatabases] = useState<Database[]>([]);
  const [cloud, setCloud] = useState("aws");
  const [profile, setProfile] = useState("");
  const [database, setDatabase] = useState("");
  const [mode, setMode] = useState("new");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [reload, setReload] = useState(0);
  const [summary, setSummary] = useState({
    repo: "https://github.com/2026-sbh-delta/delta-sample-app",
    branch: "main",
  });
  const attempt = useRef<{ payload: string; key: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoaded(false);
    Promise.all([
      api<{ items: DeploymentOption[] }>(
        "/deployment-options",
        undefined,
        undefined,
        controller.signal,
      ),
      api<{ items: Database[] }>(
        "/application-databases",
        undefined,
        undefined,
        controller.signal,
      ),
    ])
      .then(([targets, dbs]) => {
        if (controller.signal.aborted) return;
        setOptions(targets.items);
        setDatabases(dbs.items);
        setLoaded(true);
        setError("");
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(message(e));
      });
    return () => controller.abort();
  }, [reload]);
  const target = options.find((o) => o.cloud === cloud);
  const available = databases.filter((db) => db.clouds.includes(cloud));
  const selectedDb = available.find((db) => db.id === database) ?? available[0];
  const selectedMode = selectedDb?.modes.includes(mode)
    ? mode
    : selectedDb?.modes[0];
  const selectedProfile =
    target?.profiles.find((p) => p.id === profile) ?? target?.profiles.at(-1);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!target || !selectedProfile || !selectedDb || !selectedMode) return;
    const input = {
      ...Object.fromEntries(new FormData(event.currentTarget)),
      deployment_target: {
        cloud: target.cloud,
        region: target.region,
        profile_id: selectedProfile.id,
        ...(cloud === "onprem" ? { host_id: target.hosts[0]?.id } : {}),
      },
      database: { profile_id: selectedDb.id, mode: selectedMode },
    };
    const payload = JSON.stringify(input);
    if (attempt.current?.payload !== payload)
      attempt.current = { payload, key: crypto.randomUUID() };
    setBusy(true);
    setError("");
    try {
      onCreated(
        await api<Service>("/applications", input, attempt.current.key),
      );
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  const blocked = loaded && (!target?.configured || !available.length);
  const repoName = summary.repo.replace(/^https:\/\/github\.com\//, "");
  const branch = summary.branch;
  function touch(event: React.FormEvent<HTMLFormElement>) {
    const data = new FormData(event.currentTarget);
    setSummary({
      repo: String(data.get("repository_url") ?? ""),
      branch: String(data.get("source_ref") ?? ""),
    });
  }
  return (
    <section className="panel registration reg" aria-label="새 프로젝트 등록">
      <div className="panel-top">
        <div>
          <div className="eyebrow">NEW PROJECT</div>
          <h2>애플리케이션 등록</h2>
        </div>
        <button onClick={onCancel}>닫기</button>
      </div>
      <form onSubmit={submit} className="reg-layout" onChange={touch}>
        <div className="reg-steps">
          <fieldset className="reg-step">
            <legend>
              <span>1</span>저장소
            </legend>
            <div className="form-grid">
              <label>
                이름
                <input
                  name="name"
                  required
                  maxLength={80}
                  defaultValue="A기업 게시판"
                />
              </label>
              <label>
                브랜치
                <input name="source_ref" required defaultValue="main" />
              </label>
              <label className="full">
                공개 저장소
                <input
                  name="repository_url"
                  required
                  defaultValue="https://github.com/2026-sbh-delta/delta-sample-app"
                />
              </label>
            </div>
            <details>
              <summary>소스 경로 · frontend/ · backend/</summary>
              <div className="form-grid">
                <label>
                  프론트엔드 빌드 경로
                  <input
                    name="frontend_context"
                    required
                    defaultValue="frontend"
                  />
                </label>
                <label>
                  프론트엔드 Dockerfile
                  <input
                    name="frontend_dockerfile"
                    required
                    defaultValue="frontend/Dockerfile"
                  />
                </label>
                <label>
                  백엔드 빌드 경로
                  <input
                    name="backend_context"
                    required
                    defaultValue="backend"
                  />
                </label>
                <label>
                  백엔드 Dockerfile
                  <input
                    name="backend_dockerfile"
                    required
                    defaultValue="backend/Dockerfile"
                  />
                </label>
              </div>
            </details>
          </fieldset>

          <fieldset className="reg-step">
            <legend>
              <span>2</span>현재 운영 환경
            </legend>
            <div
              className="reg-clouds"
              role="radiogroup"
              aria-label="배포 환경"
            >
              {options.map((o) => (
                <label
                  key={o.cloud}
                  className={`reg-cloud${o.cloud === cloud ? " on" : ""}`}
                >
                  {/* No name: the form body is sent as-is, so this choice must not join it. */}
                  <input
                    type="radio"
                    value={o.cloud}
                    checked={o.cloud === cloud}
                    onChange={() => {
                      setCloud(o.cloud);
                      setProfile("");
                      setDatabase("");
                    }}
                  />
                  <strong>{o.label}</strong>
                  <small>
                    {o.cloud === "onprem" ? "k3s" : "VM · Compose"} · {o.region}
                  </small>
                </label>
              ))}
            </div>
            <label>
              {cloud === "onprem" ? "앱 자원 한도" : "VM 사양"}
              <select
                value={selectedProfile?.id ?? ""}
                onChange={(e) => setProfile(e.target.value)}
              >
                {target?.profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label} · {p.memory_mb} MB
                  </option>
                ))}
              </select>
            </label>
            {loaded && !target?.configured && (
              <p role="status" className="reg-warn">
                {target?.reason} 등록 후 해당 단계에서 대기합니다.
              </p>
            )}
          </fieldset>

          <fieldset className="reg-step">
            <legend>
              <span>3</span>데이터베이스
            </legend>
            <div className="form-grid">
              <label>
                PostgreSQL 서버
                <select
                  value={selectedDb?.id ?? ""}
                  onChange={(e) => setDatabase(e.target.value)}
                >
                  {!available.length && (
                    <option value="">연결된 DB 서버 없음</option>
                  )}
                  {available.map((db) => (
                    <option key={db.id} value={db.id}>
                      {db.label} ·{" "}
                      {db.server_mode === "create"
                        ? "새 서버 생성"
                        : "준비된 서버"}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                데이터베이스
                <select
                  value={selectedMode ?? ""}
                  onChange={(e) => setMode(e.target.value)}
                >
                  {selectedDb?.modes.map((m) => (
                    <option key={m} value={m}>
                      {m === "new"
                        ? "새 앱 데이터베이스 생성"
                        : "기존 앱 데이터베이스 연결"}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {selectedDb && (
              <p className="muted">
                {selectedDb.server_mode === "create"
                  ? "전용 서버 새로 생성 · 준비 시간 필요 · 앱 삭제 시 보존"
                  : "준비된 서버 사용 · 앱 삭제 시 보존"}
              </p>
            )}
            {loaded && !available.length && (
              <p role="status" className="reg-warn">
                운영자가 선택한 환경의 PostgreSQL 연결을 등록해야 합니다.
              </p>
            )}
          </fieldset>
        </div>

        <aside className="reg-summary" aria-label="등록 요약">
          <h3>요약</h3>
          <ul className="reg-chips">
            <li>{repoName}</li>
            <li>{branch}</li>
            <li>
              {target?.label ?? cloud} · {target?.region ?? ""}
            </li>
            {selectedProfile && <li>{selectedProfile.label}</li>}
            {selectedDb && <li>{selectedDb.label}</li>}
          </ul>
          <h3>이후 흐름</h3>
          <ol className="reg-flow">
            <li className="now">등록 · 첫 배포</li>
            <li>이전 대상 선택</li>
            <li>보안 파이프라인</li>
            <li>운영 배포 · 도메인 전환</li>
          </ol>
          <p className="muted">
            빌드 → 샌드박스 → VM·DB → 연결 확인 · 보안 검사는 이전
            파이프라인에서
          </p>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          <button
            className="primary"
            disabled={busy || !loaded || !selectedDb || !selectedProfile}
          >
            {busy ? "등록 중…" : "등록 및 배포"}
          </button>
          <div className="reg-links">
            {(blocked || error) && (
              <button type="button" onClick={() => setReload((v) => v + 1)}>
                연결 다시 확인
              </button>
            )}
            <button type="button" onClick={onSingle}>
              단일 컨테이너 등록
            </button>
          </div>
        </aside>
      </form>
    </section>
  );
}
