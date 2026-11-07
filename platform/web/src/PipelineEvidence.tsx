import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Board, BoardNode } from "./pipeline-model";

// Evidence shown in the step panel: what the step actually changed, not just "done".
type Detail = Record<string, unknown>;
const short = (digest?: unknown) =>
  typeof digest === "string" ? digest.replace("sha256:", "").slice(0, 12) : "—";
const cloudOf = (host?: string) =>
  !host
    ? ""
    : host.startsWith("aws")
      ? "AWS"
      : host.startsWith("gcp")
        ? "GCP"
        : host.startsWith("onprem")
          ? "온프렘"
          : "";

// Jenkins console tail as a small terminal; follows while the step runs.
export function Term({
  board,
  node,
  title,
}: {
  board: Board;
  node: BoardNode;
  title: string;
}) {
  const [text, setText] = useState("");
  const [failed, setFailed] = useState(false);
  const pre = useRef<HTMLPreElement>(null);
  const live = node.state === "running" || node.state === "waiting";
  useEffect(() => {
    if (node.state === "pending") return;
    let stop = false;
    const load = () =>
      api<{ text: string }>(`/pipeline/runs/${board.run_id}/log/${node.id}`)
        .then((r) => {
          if (stop) return;
          setFailed(false);
          setText(
            r.text
              .split("\n")
              .filter((l) => l.trim())
              .slice(-16)
              .join("\n"),
          );
        })
        .catch(() => !stop && setFailed(true));
    load();
    if (!live) return () => void (stop = true);
    const t = setInterval(load, 4000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [board.run_id, node.id, live, node.state]);
  useEffect(() => {
    pre.current?.scrollTo({ top: pre.current.scrollHeight });
  }, [text]);
  if (failed && !text) return null;
  return (
    <section className="ev-term" aria-label={`${title} 로그`}>
      <header>
        <span className="ev-dots">
          <i />
          <i />
          <i />
        </span>
        <b>{title}</b>
        {live ? <em className="live">● 실시간</em> : <em>기록</em>}
      </header>
      <pre ref={pre}>{text || "로그를 불러오는 중…"}</pre>
    </section>
  );
}

function Fact({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="ev-fact">
      <span>{label}</span>
      <b className={mono ? "mono" : ""}>{value}</b>
    </div>
  );
}

export function BuildEvidence({ board }: { board: Board }) {
  const build = board.nodes.find((n) => n.id === "build");
  const sast = board.nodes.find((n) => n.id === "sast");
  const bundle = (build?.detail as Detail | undefined)?.bundle as
    Detail | undefined;
  const digests = (bundle?.image_digests ?? {}) as Record<string, string>;
  return (
    <div className="ev">
      {bundle && (
        <div className="ev-facts">
          <Fact
            label="커밋"
            value={String(bundle.commit ?? "").slice(0, 7)}
            mono
          />
          <Fact
            label="프론트엔드 이미지"
            value={short(digests.frontend)}
            mono
          />
          <Fact label="백엔드 이미지" value={short(digests.backend)} mono />
          {sast?.summary && <Fact label="SAST" value={sast.summary} />}
        </div>
      )}
      {build && (
        <Term board={board} node={build} title="Jenkins · delta-build" />
      )}
    </div>
  );
}

export function ReleaseEvidence({ board }: { board: Board }) {
  const node = board.nodes.find((n) => n.id === "release");
  if (!node) return null;
  const detail = (node.detail ?? {}) as Detail;
  const result = (detail.verified_result ?? {}) as Detail;
  const digests = (result.image_digests ?? {}) as Record<string, string>;
  const pg =
    typeof result.database_version_num === "number"
      ? result.database_version_num
      : null;
  return (
    <div className="ev">
      {result.state === "verified" && (
        <div className="ev-hero s-pass">
          <span className="ev-cloud gcp">GCP</span>
          <div>
            <b>운영 배포 검증 완료</b>
            <small>{String(result.target_id ?? detail.target_id ?? "")}</small>
          </div>
        </div>
      )}
      <div className="ev-facts">
        <Fact label="대상" value={String(detail.target_id ?? "—")} />
        {result.commit != null && (
          <Fact label="커밋" value={String(result.commit).slice(0, 7)} mono />
        )}
        {digests.frontend && (
          <Fact label="프론트엔드" value={short(digests.frontend)} mono />
        )}
        {digests.backend && (
          <Fact label="백엔드" value={short(digests.backend)} mono />
        )}
        {pg && (
          <Fact
            label="PostgreSQL"
            value={`${Math.floor(pg / 10000)}.${pg % 10000}`}
          />
        )}
        {detail.build != null && (
          <Fact label="Jenkins" value={`delta-release #${detail.build}`} />
        )}
      </div>
      <Term board={board} node={node} title="Jenkins · delta-release" />
    </div>
  );
}

type Domain = {
  configured?: boolean;
  record?: string;
  green?: string;
  blue?: string;
  target?: string | null;
};

// DNS switch as a before/after route plus the live entry the service domain points at now.
export function CutoverEvidence({ board }: { board: Board }) {
  const node = board.nodes.find((n) => n.id === "cutover");
  const [domain, setDomain] = useState<Domain | null>(null);
  useEffect(() => {
    let stop = false;
    const load = () =>
      api<Domain>("/pipeline/domain")
        .then((d) => !stop && setDomain(d))
        .catch(() => undefined);
    load();
    const t = setInterval(load, 10000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, []);
  if (!node) return null;
  const detail = (node.detail ?? {}) as Detail;
  const verified = (detail.verified ?? {}) as Detail;
  const precheck = (detail.precheck ?? {}) as Detail;
  const record =
    domain?.record ??
    (typeof verified.url === "string"
      ? new URL(verified.url).host
      : "서비스 도메인");
  const before = String(detail.previous ?? domain?.green ?? "");
  const after = String(verified.dns ?? domain?.blue ?? "");
  const now = domain?.target ?? "";
  const switched = !!verified.dns;
  return (
    <div className="ev">
      <div className="ev-route">
        <div className="ev-record">
          <small>서비스 도메인</small>
          <b>{record}</b>
        </div>
        <div className={`ev-hop from${switched ? " done" : ""}`}>
          <span className={`ev-cloud ${cloudOf(before).toLowerCase()}`}>
            {cloudOf(before) || "?"}
          </span>
          <code>{before || "—"}</code>
        </div>
        <div className="ev-arrow" aria-hidden="true">
          {switched
            ? "전환 완료"
            : node.state === "running"
              ? "전환 중…"
              : "전환 예정"}
        </div>
        <div className={`ev-hop to${switched ? " done" : ""}`}>
          <span className={`ev-cloud ${cloudOf(after).toLowerCase()}`}>
            {cloudOf(after) || "?"}
          </span>
          <code>{after || "—"}</code>
        </div>
      </div>
      {now && (
        <div className="ev-now">
          <span className="ev-pulse" />
          지금 입구
          <span className={`ev-cloud ${cloudOf(now).toLowerCase()}`}>
            {cloudOf(now)}
          </span>
          <code>{now}</code>
        </div>
      )}
      <ol className="ev-checks">
        <li className={precheck.status === 200 ? "ok" : ""}>
          전환 전 검증 · HTTP {String(precheck.status ?? "—")} ·{" "}
          {String(precheck.served_by ?? "—")}
        </li>
        <li className={switched ? "ok" : ""}>
          DNS 전용 CNAME 교체 · Cloudflare
        </li>
        <li className={detail.wait_seconds ? "ok" : ""}>
          기존 입구 유지 · TTL {String(detail.ttl ?? 60)}초
        </li>
        <li className={verified.served_by ? "ok" : ""}>
          실제 응답 · {String(verified.served_by ?? "—")}
        </li>
      </ol>
    </div>
  );
}
