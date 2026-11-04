import type { Service } from "./api";
import type { Observability } from "./observability-api";
import { roleName, stamp } from "./application-model";

export function RuntimeComponents({
  service,
  snapshot,
}: {
  service: Service | null;
  snapshot: Observability;
}) {
  const bundle = service?.application_bundle;
  if (!bundle)
    return (
      <p className="release-caption">
        단일 서비스의 관측 결과입니다. FE·BE 전체 준비 상태는 묶음 연결 후
        확인할 수 있습니다.
      </p>
    );
  const evidence =
    snapshot.application?.bundle_version === bundle.version
      ? snapshot.application
      : null;
  return (
    <div className="release-runtime-components">
      <p className="release-caption">
        실행 환경에서 수집한 구성 요소 상태 · 빌드 완료와 별도
      </p>
      {bundle.components.map((component) => {
        const runtime = evidence?.components.find(
          (c) =>
            c.service_id === component.service_id &&
            c.role === component.role &&
            c.build_id === component.build_id,
        );
        const ready = runtime?.readiness;
        const label = !ready
          ? "관측 미연결"
          : ready.status === "ok" && ready.value !== null
            ? ready.value === 1
              ? "준비됨"
              : "준비되지 않음"
            : {
                ok: "데이터 없음",
                no_data: "데이터 없음",
                stale: "수집 지연",
                error: "수집 실패",
                unsupported: "미지원",
              }[ready.status];
        return (
          <div key={component.role} className="release-target-check">
            <div className="release-line">
              <strong>{roleName[component.role]}</strong>
              <span
                className={`release-state ${ready?.status === "ok" && ready.value === 1 ? "good" : ""}`}
              >
                {label}
              </span>
            </div>
            <p>
              실제 버전 · {runtime?.commit_sha?.slice(0, 12) ?? "확인 필요"}
            </p>
            <small>마지막 수집 · {stamp(ready?.collected_at)}</small>
            {runtime && (
              <details>
                <summary>실행 이미지</summary>
                <code>{runtime.image_digest}</code>
              </details>
            )}
          </div>
        );
      })}
    </div>
  );
}
