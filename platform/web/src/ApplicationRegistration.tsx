import { useEffect, useRef, useState } from "react";
import { api, message, type DeploymentOption, type Service } from "./api";

type Database = {
  id: string;
  label: string;
  engine: string;
  modes: string[];
  clouds: string[];
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
  return (
    <section className="panel registration">
      <div className="panel-top">
        <h2>애플리케이션 등록</h2>
        <button onClick={onCancel}>닫기</button>
      </div>
      <p>
        샘플 앱 기준으로 프론트엔드·백엔드를 함께 배포하고 PostgreSQL에
        연결합니다.
      </p>
      <form onSubmit={submit}>
        <div className="form-grid">
          <label>
            이름
            <input
              name="name"
              required
              maxLength={80}
              defaultValue="Delta Notes"
            />
          </label>
          <label>
            공개 저장소
            <input
              name="repository_url"
              required
              defaultValue="https://github.com/2026-sbh-delta/delta-sample-app"
            />
          </label>
          <label>
            브랜치
            <input name="source_ref" required defaultValue="main" />
          </label>
          <label>
            배포 환경
            <select
              value={cloud}
              onChange={(e) => {
                setCloud(e.target.value);
                setProfile("");
                setDatabase("");
              }}
            >
              {options.map((o) => (
                <option key={o.cloud} value={o.cloud}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
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
                  {db.label}
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
        <details>
          <summary>소스 경로</summary>
          <div className="form-grid">
            <label>
              프론트엔드 빌드 경로
              <input name="frontend_context" required defaultValue="frontend" />
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
              <input name="backend_context" required defaultValue="backend" />
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
        <p className="muted">
          AWS·GCP: VM 한 대에서 Compose로 FE·BE 실행. 온프레미스: 기존 k3s 사용.
          DB는 별도 서버에 저장하며 앱 삭제 시 보존합니다.
        </p>
        {loaded && !target?.configured && (
          <p role="status">
            {target?.reason} 등록 후 해당 단계에서 대기합니다.
          </p>
        )}
        {loaded && !available.length && (
          <p role="status">
            운영자가 선택한 환경의 PostgreSQL 연결을 등록해야 합니다.
          </p>
        )}
        <p>
          소스 확인 → Jenkins 빌드 → SAST → 샌드박스·DAST → VM·DB 준비 →
          배포·연결 확인
        </p>
        <p className="muted">
          등록 및 배포를 누르면 자동으로 진행합니다. 검사 결과 검토가 필요하면
          멈춥니다.
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
        <button type="button" onClick={() => setReload((v) => v + 1)}>
          연결 다시 확인
        </button>
        <button type="button" onClick={onSingle}>
          단일 컨테이너 등록
        </button>
      </form>
    </section>
  );
}
