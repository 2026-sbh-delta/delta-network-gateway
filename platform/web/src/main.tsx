import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  api,
  ApiError,
  message,
  setSession,
  type Session,
  type Service,
  type ServiceInput,
  type Run,
  type RunEvent,
  type Page,
} from "./api";
import "./style.css";
import { BuildPanel } from "./BuildPanel";

const labels = {
  queued: "확인 대기",
  running: "소스 확인 중",
  succeeded: "소스 확인 완료",
  failed: "확인 실패",
};
const steps: Record<string, string> = {
  repository: "공개 저장소",
  revision: "검사할 커밋",
  build_context: "빌드 경로",
  dockerfile: "Dockerfile",
};
const time = (value: string) => new Date(value).toLocaleString("ko-KR");
const active = (run: Run | null) =>
  run?.status === "queued" || run?.status === "running";
function Status({ run }: { run?: Run | null }) {
  return (
    <span className={`status ${run?.status ?? ""}`}>
      {run ? labels[run.status] : "검사 전"}
    </span>
  );
}
function ErrorBox({ error }: { error: string }) {
  return error ? (
    <div className="error" role="alert">
      {error}
    </div>
  ) : null;
}

function Login({ onLogin }: { onLogin: (s: Session) => void }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      onLogin(
        await api<Session>("/auth/login", {
          login_id: form.get("login_id"),
          password: form.get("password"),
        }),
      );
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login-layout">
      <section className="login-copy">
        <div className="eyebrow">DELTA PLATFORM</div>
        <h1>
          서비스의 시작을,
          <br />
          하나의 작업 공간에서.
        </h1>
        <p>
          공개 저장소를 등록하고
          <br />
          배포할 소스가 준비됐는지 확인하세요.
        </p>
        <div className="scope">
          <span>01 소스 확인</span>
          <span>02 이미지 빌드</span>
          <span>03 배포 · 후속 단계</span>
        </div>
      </section>
      <section className="login-card">
        <div className="mini-mark">δ</div>
        <h2>워크스페이스 로그인</h2>
        <p className="muted">팀에서 정한 ID와 비밀번호를 사용하세요.</p>
        <form onSubmit={submit}>
          <label>
            아이디
            <input
              name="login_id"
              autoComplete="username"
              maxLength={128}
              required
              autoFocus
            />
          </label>
          <label>
            비밀번호
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              maxLength={1024}
              required
            />
          </label>
          <ErrorBox error={error} />
          <button className="primary" disabled={busy}>
            {busy ? "로그인 중…" : "로그인"}
          </button>
        </form>
        <p className="footnote">팀 공통 작업 공간 · 고정 계정</p>
      </section>
    </main>
  );
}

function Register({
  onCreated,
  onCancel,
}: {
  onCreated: (s: Service) => void;
  onCancel: () => void;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const attempt = useRef<{ payload: string; key: string } | null>(null);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const input = Object.fromEntries(data.entries()) as unknown as ServiceInput;
    input.container_port = Number(data.get("container_port"));
    const payload = JSON.stringify(input);
    if (attempt.current?.payload !== payload)
      attempt.current = { payload, key: crypto.randomUUID() };
    setBusy(true);
    setError("");
    try {
      onCreated(await api<Service>("/services", input, attempt.current.key));
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel registration">
      <div className="panel-top">
        <div>
          <div className="eyebrow">NEW SERVICE</div>
          <h2>서비스 등록</h2>
        </div>
        <button onClick={onCancel}>닫기</button>
      </div>
      <p className="muted">공개 GitHub 저장소와 Dockerfile이 필요합니다.</p>
      <form onSubmit={submit}>
        <div className="form-grid">
          <label>
            서비스 이름
            <input
              name="name"
              maxLength={80}
              placeholder="orders-api"
              required
            />
          </label>
          <label>
            브랜치 또는 커밋
            <input
              name="source_ref"
              maxLength={255}
              defaultValue="main"
              required
            />
          </label>
          <label className="full">
            공개 GitHub 저장소
            <input
              name="repository_url"
              type="url"
              placeholder="https://github.com/team/orders-api"
              required
            />
          </label>
          <label>
            빌드 경로
            <input name="build_context" defaultValue="." required />
            <small>저장소 최상위는 .</small>
          </label>
          <label>
            Dockerfile 경로
            <input name="dockerfile_path" defaultValue="Dockerfile" required />
            <small>저장소를 기준으로 입력</small>
          </label>
          <label>
            앱 포트
            <input
              name="container_port"
              type="number"
              min={1}
              max={65535}
              defaultValue={8080}
              required
            />
          </label>
          <label>
            헬스 체크 경로
            <input name="healthcheck_path" defaultValue="/health" required />
          </label>
        </div>
        <p className="notice">
          지금은 소스만 확인합니다. 포트와 헬스 체크 경로는 이후 배포에 사용할
          정보입니다.
        </p>
        <ErrorBox error={error} />
        <button className="primary" disabled={busy}>
          {busy ? "등록 중…" : "등록하고 상세 보기"}
        </button>
      </form>
    </section>
  );
}

function Detail({ id, onBack }: { id: string; onBack: () => void }) {
  const [service, setService] = useState<Service | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [history, setHistory] = useState<Run[]>([]);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [reload, setReload] = useState(0);
  const key = useRef<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let stopped = false;
    async function load() {
      try {
        const [s, h] = await Promise.all([
          api<Service>(
            `/services/${id}`,
            undefined,
            undefined,
            controller.signal,
          ),
          api<Page<Run>>(
            `/services/${id}/source-checks`,
            undefined,
            undefined,
            controller.signal,
          ),
        ]);
        if (!stopped) {
          setService(s);
          setRun(s.latest_source_check ?? null);
          setHistory(h.items);
          setHistoryCursor(h.next_cursor);
          setError("");
        }
      } catch (e) {
        if (!stopped) setError(message(e));
      }
    }
    load();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [id, reload]);
  useEffect(() => {
    if (!run) {
      setEvents([]);
      return;
    }
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const runId = run.id;
    async function refresh() {
      try {
        const current = await api<Run>(
          `/source-checks/${runId}`,
          undefined,
          undefined,
          controller.signal,
        );
        let after = 0,
          more = true;
        const collected: RunEvent[] = [];
        while (more) {
          const page = await api<{
            items: RunEvent[];
            has_more: boolean;
            next_sequence: number;
          }>(
            `/source-checks/${runId}/events?after_sequence=${after}`,
            undefined,
            undefined,
            controller.signal,
          );
          collected.push(...page.items);
          after = page.next_sequence;
          more = page.has_more;
        }
        if (stopped) return;
        setRun(current);
        setEvents(collected);
        setError("");
        setHistory((h) =>
          h.map((item) => (item.id === current.id ? current : item)),
        );
        if (active(current)) timer = setTimeout(refresh, 3000);
      } catch (e) {
        if (!stopped) {
          setError(message(e));
          if (active(run)) timer = setTimeout(refresh, 3000);
        }
      }
    }
    setEvents([]);
    refresh();
    return () => {
      stopped = true;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [run?.id, reload]);
  async function check() {
    setBusy(true);
    setError("");
    key.current ??= crypto.randomUUID();
    try {
      const result = await api<Run>(
        `/services/${id}/source-checks`,
        {},
        key.current,
      );
      key.current = null;
      setRun(result);
      setHistory((h) => [result, ...h.filter((x) => x.id !== result.id)]);
    } catch (e) {
      setError(message(e));
      if (e instanceof ApiError && e.code === "source_check_in_progress") {
        key.current = null;
        setReload((n) => n + 1);
      }
    } finally {
      setBusy(false);
    }
  }
  async function moreHistory() {
    if (!historyCursor) return;
    setLoadingHistory(true);
    try {
      const page = await api<Page<Run>>(
        `/services/${id}/source-checks?cursor=${encodeURIComponent(historyCursor)}`,
      );
      setHistory((h) => [...h, ...page.items]);
      setHistoryCursor(page.next_cursor);
    } catch (e) {
      setError(message(e));
    } finally {
      setLoadingHistory(false);
    }
  }
  return (
    <>
      <button className="back" onClick={onBack}>
        ← 서비스 목록
      </button>
      <ErrorBox error={error} />
      {error && (
        <button onClick={() => setReload((n) => n + 1)}>다시 조회</button>
      )}
      {!service ? (
        <p className="muted">서비스를 불러오는 중입니다.</p>
      ) : (
        <>
          <div className="detail-heading">
            <div>
              <div className="eyebrow">SERVICE</div>
              <h1>{service.name}</h1>
              <a href={service.repository_url} target="_blank" rel="noreferrer">
                {service.repository_url.replace("https://", "")} ↗
              </a>
            </div>
            <button
              className="primary"
              onClick={check}
              disabled={busy || active(run)}
            >
              {busy ? "접수 중…" : active(run) ? "소스 확인 중…" : "소스 확인"}
            </button>
          </div>
          <div className="detail-grid">
            <section className="panel">
              <div className="panel-top">
                <h2>소스 확인 결과</h2>
                <Status run={run} />
              </div>
              <p className="muted">
                저장소 · 커밋 · 빌드 경로 · Dockerfile을 확인합니다.
              </p>
              <ol className="check-steps">
                {Object.entries(steps).map(([step, label], i) => (
                  <li
                    key={step}
                    className={
                      run?.status === "succeeded"
                        ? "complete"
                        : run?.current_step === step
                          ? "current"
                          : ""
                    }
                  >
                    <span>{String(i + 1).padStart(2, "0")}</span>
                    {label}
                  </li>
                ))}
              </ol>
              {!run && (
                <div className="empty small">
                  <h3>아직 확인하지 않았습니다</h3>
                  <p>소스 확인을 눌러 등록한 경로를 검사하세요.</p>
                </div>
              )}
              {run?.resolved_commit_sha && (
                <div className="commit">
                  <span>확정 커밋</span>
                  <a
                    href={`${service.repository_url}/commit/${run.resolved_commit_sha}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <code>{run.resolved_commit_sha}</code> ↗
                  </a>
                </div>
              )}
              {run?.error && (
                <div className="error">
                  <strong>{run.error.message}</strong>
                  <small>{run.error.code}</small>
                  {run.error.retry_at && (
                    <p>다시 시도 가능 시각: {time(run.error.retry_at)}</p>
                  )}
                </div>
              )}
              {run?.status === "succeeded" && (
                <p className="success-note">
                  등록한 소스 위치를 확인했습니다. 이미지 빌드는 아래에서 별도로
                  요청합니다. 소스 확인 성공은 실행 검사·배포 성공을 뜻하지
                  않습니다.
                </p>
              )}
              <h3 className="subhead">
                진행 기록 {run && <small>· 시도 {run.attempt}/3</small>}
              </h3>
              <ul className="timeline">
                {events.map((e) => (
                  <li key={e.sequence}>
                    <span className="dot" />
                    <div>
                      {e.message}
                      <time>{time(e.created_at)}</time>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
            <aside className="panel">
              <h2>등록 정보</h2>
              <dl>
                {[
                  ["브랜치 / 커밋", service.source_ref],
                  ["빌드 경로", service.build_context],
                  ["Dockerfile", service.dockerfile_path],
                  ["앱 포트", service.container_port],
                  ["헬스 체크", service.healthcheck_path],
                ].map(([label, value]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{value}</dd>
                  </div>
                ))}
              </dl>
              <p className="notice">
                소스 확인 후 별도 빌드 환경에서 이미지를 만들 수 있습니다.
                <br />
                비밀값은 등록 정보에 넣지 마세요.
              </p>
            </aside>
          </div>
          <BuildPanel key={id} serviceId={id} source={run} />
          <section className="panel history">
            <h2>검사 이력</h2>
            {history.length === 0 ? (
              <p className="muted">저장된 검사 이력이 없습니다.</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>접수 시각</th>
                      <th>결과</th>
                      <th>커밋</th>
                      <th>상세</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((item) => (
                      <tr key={item.id}>
                        <td>{time(item.created_at)}</td>
                        <td>
                          <Status run={item} />
                        </td>
                        <td>
                          <code>
                            {item.resolved_commit_sha?.slice(0, 12) ?? "—"}
                          </code>
                        </td>
                        <td>
                          <button
                            disabled={active(run) && run?.id !== item.id}
                            onClick={() => setRun(item)}
                            aria-pressed={run?.id === item.id}
                          >
                            기록 보기
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {historyCursor && (
              <button disabled={loadingHistory} onClick={moreHistory}>
                이력 더 보기
              </button>
            )}
          </section>
        </>
      )}
    </>
  );
}

function Workspace({
  session,
  onLogout,
}: {
  session: Session;
  onLogout: () => void;
}) {
  const [services, setServices] = useState<Service[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [register, setRegister] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  async function load(more = false) {
    setLoading(true);
    setError("");
    try {
      const page = await api<Page<Service>>(
        "/services" +
          (more && cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""),
      );
      setServices((old) => (more ? [...old, ...page.items] : page.items));
      setCursor(page.next_cursor);
    } catch (e) {
      setError(message(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    if (!selected) load();
  }, [selected]);
  async function logout() {
    try {
      await api("/auth/logout", {});
      onLogout();
    } catch (e) {
      setError(message(e));
    }
  }
  return (
    <>
      <header className="topbar">
        <a
          className="brand"
          href="#"
          onClick={() => {
            setSelected(null);
            setRegister(false);
          }}
        >
          <span>δ</span> DELTA <small>Platform</small>
        </a>
        <div className="account">
          <span>{session.login_id} · 공통 작업 공간</span>
          <button onClick={logout}>로그아웃</button>
        </div>
      </header>
      <main className="workspace">
        <ErrorBox error={error} />
        {selected ? (
          <Detail
            key={selected}
            id={selected}
            onBack={() => setSelected(null)}
          />
        ) : (
          <>
            <div className="page-heading">
              <div>
                <div className="eyebrow">WORKSPACE / SERVICES</div>
                <h1>내 서비스</h1>
                <p>소스를 등록하고, 배포 준비를 시작하세요.</p>
              </div>
              <button className="primary" onClick={() => setRegister(true)}>
                + 서비스 등록
              </button>
            </div>
            <div className="stage-banner">
              <span className="stage-number">01</span>
              <div>
                <strong>소스 확인부터 이미지 빌드까지.</strong>
                <p>
                  공개 저장소의 커밋을 확인하고 이미지를 만듭니다. 실행 검사와
                  배포는 후속 단계입니다.
                </p>
              </div>
              <span className="status running">소스 · 빌드</span>
            </div>
            {register && (
              <Register
                onCreated={(s) => {
                  setRegister(false);
                  setSelected(s.id);
                }}
                onCancel={() => setRegister(false)}
              />
            )}
            <section className="panel">
              <div className="panel-top">
                <h2>
                  등록한 서비스{" "}
                  <span className="count">
                    {services.length}
                    {cursor ? "+" : ""}
                  </span>
                </h2>
                <button onClick={() => load()} disabled={loading}>
                  새로고침
                </button>
              </div>
              {loading && services.length === 0 ? (
                <p className="muted">서비스를 불러오는 중입니다.</p>
              ) : services.length === 0 ? (
                <div className="empty">
                  <div className="empty-icon">⌘</div>
                  <h3>첫 서비스를 등록해 보세요</h3>
                  <p>공개 GitHub 저장소 주소와 Dockerfile만 준비하면 됩니다.</p>
                  <button onClick={() => setRegister(true)}>서비스 등록</button>
                </div>
              ) : (
                <div className="service-list">
                  {services.map((s) => (
                    <button
                      className="service-row"
                      key={s.id}
                      onClick={() => setSelected(s.id)}
                    >
                      <span className="service-icon">
                        {s.name[0].toUpperCase()}
                      </span>
                      <span className="service-text">
                        <strong>{s.name}</strong>
                        <small>
                          {s.repository_url.replace("https://github.com/", "")}
                        </small>
                      </span>
                      <span className="ref">{s.source_ref}</span>
                      <span className="arrow">→</span>
                    </button>
                  ))}
                </div>
              )}
              {cursor && (
                <button onClick={() => load(true)} disabled={loading}>
                  서비스 더 보기
                </button>
              )}
            </section>
          </>
        )}
        <footer>
          DELTA PLATFORM{" "}
          <span>실제 저장된 서비스와 검사 결과를 표시합니다.</span>
        </footer>
      </main>
    </>
  );
}

function App() {
  const [session, save] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  function login(s: Session | null) {
    setSession(s);
    save(s);
  }
  async function load() {
    setLoading(true);
    setError("");
    try {
      login(await api<Session>("/auth/session"));
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setError(message(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
    const expired = () => login(null);
    window.addEventListener("session-expired", expired);
    return () => window.removeEventListener("session-expired", expired);
  }, []);
  if (loading)
    return <div className="loading">작업 공간을 확인하고 있습니다…</div>;
  if (error)
    return (
      <main className="connection-error">
        <ErrorBox error={error} />
        <button onClick={load}>다시 연결</button>
      </main>
    );
  return session ? (
    <Workspace session={session} onLogout={() => login(null)} />
  ) : (
    <Login onLogin={login} />
  );
}
createRoot(document.getElementById("root")!).render(<App />);
