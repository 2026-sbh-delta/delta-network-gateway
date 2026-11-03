export type Session = {
  login_id: string;
  expires_at: string;
  csrf_token: string;
};
export type Run = {
  id: string;
  service_id: string;
  kind: "source_preflight" | "image_build" | "sandbox_check";
  cancel_requested?: boolean;
  external_id: string | null;
  status: "queued" | "running" | "succeeded" | "failed";
  current_step: string | null;
  attempt: number;
  resolved_commit_sha: string | null;
  created_at: string;
  result: {
    runtime_check?: string;
    security_check?: string;
    build_id?: string;
    container_port?: number;
    healthcheck_path?: string;
    cleanup?: string;
    dockerfile_blob_sha?: string;
    checked_at?: string;
    image?: string;
    image_digest?: string;
    platform?: string;
  } | null;
  error: { code: string; message: string; retry_at?: string } | null;
};
export type Service = {
  id: string;
  name: string;
  repository_url: string;
  source_ref: string;
  build_context: string;
  dockerfile_path: string;
  container_port: number;
  healthcheck_path: string;
  created_at: string;
  latest_source_check?: Run | null;
};
export type ServiceInput = Omit<
  Service,
  "id" | "created_at" | "latest_source_check"
>;
export type RunEvent = {
  sequence: number;
  event_type: string;
  message: string;
  created_at: string;
};
export type Page<T> = { items: T[]; next_cursor: string | null };
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId?: string,
  ) {
    super(message);
  }
}
let csrf = "";
export function setSession(session: Session | null) {
  csrf = session?.csrf_token ?? "";
}
export async function api<T>(
  path: string,
  body?: unknown,
  key?: string,
  signal?: AbortSignal,
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined)
    Object.assign(headers, {
      "Content-Type": "application/json",
      "X-Platform-Request": "1",
      "X-CSRF-Token": csrf,
    });
  if (key) headers["Idempotency-Key"] = key;
  const response = await fetch("/api/v1" + path, {
    method: body === undefined ? "GET" : "POST",
    credentials: "same-origin",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  if (response.status === 204) return undefined as T;
  let data;
  try {
    data = await response.json();
  } catch {
    throw new ApiError(
      response.status,
      "invalid_response",
      "서버 응답을 확인할 수 없습니다.",
    );
  }
  if (!response.ok) {
    if (response.status === 401 && path !== "/auth/login")
      window.dispatchEvent(new Event("session-expired"));
    throw new ApiError(
      response.status,
      data.error?.code ?? "request_failed",
      data.error?.message ?? "요청에 실패했습니다.",
      data.error?.request_id,
    );
  }
  return data;
}
export function message(error: unknown) {
  return error instanceof ApiError
    ? `${error.message}${error.requestId ? ` (요청 ${error.requestId.slice(0, 8)})` : ""}`
    : "연결에 실패했습니다. 다시 시도하세요.";
}
