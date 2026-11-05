import { api, type Page, type Run, type Service } from "./api";
import type { Environment } from "./observability-api";

// Explicit application identity; legacy single-container services omit this contract.
export type ApplicationBundle = {
  version: string;
  database: "none" | "memory" | "external" | "unknown";
  components: {
    role: "frontend" | "backend";
    service_id: string;
    build_id: string | null;
  }[];
};
export type ComponentState = {
  role: "frontend" | "backend" | "app";
  service: Service;
  build: Run | null;
  checks: Run[];
  historyIncomplete: boolean;
};
export type ApplicationState = {
  components: ComponentState[];
  environments: Environment[];
  bundle: ApplicationBundle | null;
};
export const cloudName = { aws: "AWS", gcp: "GCP", onprem: "온프레미스" };
export const roleName = {
  frontend: "프론트엔드",
  backend: "백엔드",
  app: "등록 서비스",
};
export const runLabel = {
  queued: "대기",
  running: "실행 중",
  succeeded: "완료",
  failed: "실패",
};
export const runCloud = (run: Run) =>
  run.result?.execution_cloud ?? run.execution_cloud;
export const stamp = (value?: string | null) =>
  value ? new Date(value).toLocaleString("ko-KR") : "확인 기록 없음";
export function checkLabel(run: Run | null, kind: "runtime" | "sast" | "dast") {
  if (!run) return "검사 미실행";
  if (
    run.cancel_requested &&
    !["completed", "stopped"].includes(run.result?.cleanup ?? "")
  )
    return "취소 요청 · 종료 확인 필요";
  if (run.status !== "succeeded") return runLabel[run.status];
  const result =
    kind === "runtime"
      ? run.result?.runtime_check
      : kind === "sast"
        ? run.result?.security_check
        : run.result?.dast_check;
  if (result === "not_run" || result === "skipped") return "검사 미실행";
  if (result === "failed") return "검사 실패";
  if (
    result === "passed" ||
    result === "completed" ||
    result === "sast_completed"
  )
    return "검사 완료";
  return "결과 확인 필요";
}
export function latestCheck(
  component: ComponentState,
  kind: Run["kind"],
  cloud?: string,
) {
  // A failed/active run without a public build identity must not resurrect an older pass.
  if (
    component.build?.status !== "succeeded" ||
    !component.build.result?.image_digest
  )
    return null;
  const candidates = component.checks.filter(
    (r) =>
      r.kind === kind &&
      (!r.result?.build_id || r.result.build_id === component.build?.id) &&
      (!cloud || !runCloud(r) || runCloud(r) === cloud),
  );
  const candidate = candidates[0];
  if (
    !candidate ||
    candidate.result?.build_id !== component.build?.id ||
    (cloud && runCloud(candidate) !== cloud)
  )
    return null;
  return candidate;
}
export async function loadApplication(
  service: Service,
  signal: AbortSignal,
): Promise<ApplicationState> {
  const bundle = service.application_bundle ?? null;
  if (
    bundle &&
    (!bundle.version ||
      bundle.components.length !== 2 ||
      !bundle.components.some((c) => c.role === "frontend") ||
      !bundle.components.some((c) => c.role === "backend") ||
      new Set(bundle.components.map((c) => c.role)).size !== 2 ||
      new Set(bundle.components.map((c) => c.service_id)).size !== 2 ||
      !bundle.components.some((c) => c.service_id === service.id))
  )
    throw new Error("invalid bundle");
  const refs = bundle?.components ?? [
    { role: "app" as const, service_id: service.id, build_id: null },
  ];
  const [components, envs] = await Promise.all([
    Promise.all(
      refs.map(async (ref) => {
        const base = `/services/${encodeURIComponent(ref.service_id)}`;
        const [component, builds, runtime, sast, dast] = await Promise.all([
          ref.service_id === service.id
            ? Promise.resolve(service)
            : api<Service>(base, undefined, undefined, signal),
          ref.build_id
            ? api<Run>(
                `/builds/${encodeURIComponent(ref.build_id)}`,
                undefined,
                undefined,
                signal,
              ).then((run) => ({ items: [run], next_cursor: null }))
            : api<Page<Run>>(`${base}/builds`, undefined, undefined, signal),
          api<Page<Run>>(
            `${base}/sandbox-checks`,
            undefined,
            undefined,
            signal,
          ),
          api<Page<Run>>(
            `${base}/security-checks`,
            undefined,
            undefined,
            signal,
          ),
          api<Page<Run>>(`${base}/dast-checks`, undefined, undefined, signal),
        ]);
        const build = builds.items[0] ?? null;
        if (
          component.id !== ref.service_id ||
          (service.project_id !== undefined &&
            component.project_id !== service.project_id) ||
          (build &&
            (build.service_id !== ref.service_id ||
              build.kind !== "image_build" ||
              (ref.build_id && build.id !== ref.build_id)))
        )
          throw new Error("component identity mismatch");
        const checks = [...runtime.items, ...sast.items, ...dast.items]
          .filter((r) => r.service_id === ref.service_id)
          .sort((a, b) => b.created_at.localeCompare(a.created_at));
        return {
          role: ref.role,
          service: component,
          build,
          checks,
          historyIncomplete: !!(
            runtime.next_cursor ||
            sast.next_cursor ||
            dast.next_cursor
          ),
        };
      }),
    ),
    api<{ items: Environment[] }>(
      `/services/${encodeURIComponent(service.id)}/environments`,
      undefined,
      undefined,
      signal,
    ),
  ]);
  return { components, environments: envs.items, bundle };
}

export function componentCheckLabel(
  component: ComponentState,
  kind: "sandbox_check" | "security_check" | "dast_check",
  cloud?: string,
) {
  if (
    component.build?.status !== "succeeded" ||
    !component.build.result?.image_digest
  )
    return "빌드 준비 필요";
  const check = latestCheck(component, kind, cloud);
  if (
    !check &&
    component.checks.some(
      (r) =>
        r.kind === kind && (!r.result?.build_id || (cloud && !runCloud(r))),
    )
  )
    return "검사 연결 확인 필요";
  const label = checkLabel(
    check,
    kind === "security_check"
      ? "sast"
      : kind === "dast_check"
        ? "dast"
        : "runtime",
  );
  if (
    kind === "security_check" &&
    label === "검사 완료" &&
    check?.result?.findings
  )
    return `${label} · 발견 ${check.result.findings.length}건${check.result.findings_truncated ? " 이상" : ""}`;
  return label;
}
