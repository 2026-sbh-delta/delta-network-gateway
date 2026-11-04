import { useState } from "react";
import type { Environment } from "./observability-api";
import { cloudName, stamp } from "./application-model";

// A future public, secret-free projection. Raw AI tool results are not consumed.
export type AssessmentView = {
  workflow_id: string;
  environment_id: string;
  plan_hash: string;
  assessment_id: string;
  summary: string;
  impact: string;
  remaining_gaps: string[];
  differences: {
    item: string;
    source: string;
    target: string;
    treatment: string;
    judgment:
      | "satisfied"
      | "change_required"
      | "partial"
      | "unsupported"
      | "needs_check";
    before: string | null;
    after: string | null;
  }[];
  changes: { file: string; diff: string; kind: "configuration" | "code" }[];
};
export type Execution = {
  id: string;
  environment_id: string;
  kind: string;
  status: string;
  created_at: string;
  assessment?: AssessmentView;
};
const judgment = {
  satisfied: "충족",
  change_required: "변경 필요",
  partial: "일부 대응",
  unsupported: "미지원",
  needs_check: "확인 필요",
};
const executionStatus: Record<string, string> = {
  prepared: "준비됨",
  queued: "대기",
  running: "실행 중",
  succeeded: "실행 완료",
  failed: "실패",
};
export function EnvironmentComparison({
  environments,
  sourceId,
  targetIds,
  executions,
  workflowId,
  planHash,
}: {
  environments: Environment[];
  sourceId: string | null;
  targetIds: string[];
  executions: Execution[];
  workflowId: string | null;
  planHash: string | null;
}) {
  const [preferredTarget, setPreferredTarget] = useState("");
  const targetId = targetIds.includes(preferredTarget)
    ? preferredTarget
    : targetIds[0];
  const source = environments.find((e) => e.id === sourceId);
  const target = environments.find((e) => e.id === targetId);
  const targetExecutions = executions.filter(
    (e) => e.environment_id === targetId,
  );
  // Only the newest assessment for this exact target and immutable plan can be displayed.
  const latest = targetExecutions.find((e) => e.kind === "ai_assessment");
  const assessment =
    latest?.status === "succeeded" &&
    latest.assessment?.workflow_id === workflowId &&
    latest.assessment.environment_id === targetId &&
    latest.assessment.plan_hash === planHash &&
    latest.assessment.assessment_id === latest.id
      ? latest.assessment
      : null;
  return (
    <section className="release-comparison" aria-label="환경 차이와 AI 제안">
      <div className="section-heading">
        <div>
          <div className="eyebrow">ENVIRONMENT REVIEW</div>
          <h3>환경 차이와 AI 제안</h3>
        </div>
        <span className="release-state">
          {workflowId ? "선택한 계획 기준" : "계획 생성 전"}
        </span>
      </div>
      {!targetIds.length ? (
        <div className="release-empty">
          <strong>비교할 대상 환경을 선택하세요</strong>
          <p>
            출발 환경과 대상 환경의 차이, 변경 제안과 남는 제약을 함께
            확인합니다.
          </p>
        </div>
      ) : (
        <>
          <div
            className="release-target-tabs"
            role="group"
            aria-label="비교 대상 환경"
          >
            {targetIds.map((id) => {
              const env = environments.find((e) => e.id === id);
              return (
                <button
                  type="button"
                  aria-pressed={id === targetId}
                  key={id}
                  onClick={() => setPreferredTarget(id)}
                >
                  {env
                    ? `${cloudName[env.cloud]} · ${env.name}`
                    : "환경 정보 확인 필요"}
                </button>
              );
            })}
          </div>
          <div className="release-compare-route">
            <span>
              출발<strong>{source?.name ?? "출발 환경 없음"}</strong>
            </span>
            <b aria-hidden="true">→</b>
            <span>
              대상<strong>{target?.name ?? "환경 정보 확인 필요"}</strong>
            </span>
            <span>
              최근 실행
              <strong>
                {targetExecutions[0]
                  ? (executionStatus[targetExecutions[0].status] ??
                    "상태 확인 필요")
                  : "실행 전"}
              </strong>
            </span>
          </div>
          <div
            className="release-table-scroll"
            tabIndex={0}
            role="region"
            aria-label="환경 차이 표"
          >
            <table className="release-table">
              <thead>
                <tr>
                  <th>항목</th>
                  <th>출발 환경</th>
                  <th>대상 환경</th>
                  <th>처리 방법</th>
                  <th>판단</th>
                </tr>
              </thead>
              <tbody>
                {assessment?.differences.length ? (
                  assessment.differences.map((row, i) => (
                    <tr key={i}>
                      <th>{row.item}</th>
                      <td>
                        {row.source}
                        <small>변경 전: {row.before ?? "확인 기록 없음"}</small>
                      </td>
                      <td>
                        {row.target}
                        <small>변경 후: {row.after ?? "확인 기록 없음"}</small>
                      </td>
                      <td>{row.treatment}</td>
                      <td>
                        <span
                          className={`release-state ${row.judgment === "unsupported" ? "bad" : row.judgment === "satisfied" ? "good" : ""}`}
                        >
                          {judgment[row.judgment] ?? "확인 필요"}
                        </span>
                      </td>
                    </tr>
                  ))
                ) : (
                  <>
                    <tr>
                      <th>실행 위치</th>
                      <td>
                        {source
                          ? `${cloudName[source.cloud]} · ${source.region}`
                          : "미선택"}
                      </td>
                      <td>
                        {target
                          ? `${cloudName[target.cloud]} · ${target.region}`
                          : "미등록"}
                      </td>
                      <td>대상 자원·연결 상태 확인</td>
                      <td>확인 필요</td>
                    </tr>
                    <tr>
                      <th>등록 버전</th>
                      <td>{source?.version ?? "미등록"}</td>
                      <td>{target?.version ?? "미등록"}</td>
                      <td>실제 응답 버전과 대조</td>
                      <td>확인 필요</td>
                    </tr>
                    <tr>
                      <th>통신·권한·관측</th>
                      <td colSpan={2}>실제 환경 차이 분석 결과 미연결</td>
                      <td>환경 조사·AI 결과 연결 필요</td>
                      <td>확인 필요</td>
                    </tr>
                  </>
                )}
              </tbody>
            </table>
          </div>
          <div className="release-ai">
            <div className="release-line">
              <h4>AI 검토 요약</h4>
              <span className="release-state">
                {assessment
                  ? "판단 기록"
                  : latest
                    ? executionStatus[latest.status] === "실행 완료"
                      ? "상세 결과 미연결"
                      : (executionStatus[latest.status] ?? "확인 필요")
                    : "분석 결과 대기"}
              </span>
            </div>
            {assessment ? (
              <>
                <p>{assessment.summary}</p>
                <p>
                  <strong>영향</strong> · {assessment.impact}
                </p>
                {assessment.remaining_gaps.length > 0 && (
                  <div className="release-notice">
                    <strong>남는 차이</strong>
                    <ul>
                      {assessment.remaining_gaps.map((gap, i) => (
                        <li key={i}>{gap}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {assessment.changes.map((change, i) => (
                  <details key={i}>
                    <summary>
                      {change.kind === "code" ? "코드 변경" : "설정 변경"} ·{" "}
                      {change.file}
                    </summary>
                    <pre className="release-diff">
                      {change.diff.split("\n").map((line, n) => (
                        <span
                          key={n}
                          className={
                            line.startsWith("+")
                              ? "diff-add"
                              : line.startsWith("-")
                                ? "diff-remove"
                                : ""
                          }
                        >
                          {line}
                          {"\n"}
                        </span>
                      ))}
                    </pre>
                  </details>
                ))}
                {!assessment.changes.length && (
                  <p className="muted">연결된 변경 diff가 없습니다.</p>
                )}
                <small>
                  판단 {assessment.assessment_id} · {stamp(latest?.created_at)}
                </small>
              </>
            ) : (
              <p>
                등록된 환경 정보만 비교하고 있습니다. 실제 분석 결과가 연결되면
                변경 이유·영향·설정 diff와 미지원 항목을 표시합니다.
              </p>
            )}
            <p className="release-caption">
              계획 승인과 변경 적용은 별도입니다. 예외를 승인해도 미지원 기능은
              해결된 것으로 표시하지 않습니다.
            </p>
          </div>
        </>
      )}
    </section>
  );
}
