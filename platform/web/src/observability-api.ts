import { api, type Page, type Service } from "./api";

export type State = "ok" | "no_data" | "stale" | "error" | "unsupported";
export type Metric = {
  status: State;
  value: number | null;
  unit: string;
  collected_at: string | null;
  message: string | null;
  series: { at: string; value: number }[];
};
export type Environment = {
  id: string;
  name: string;
  cloud: "aws" | "gcp" | "onprem";
  region: string;
  version: string | null;
};
export type Observability = {
  service_id: string;
  environment: Environment;
  observed_at: string;
  window: { minutes: number; start: string; end: string };
  metrics: {
    requests: Metric;
    error_rate: Metric;
    p95_ms: Metric;
    app_up: Metric;
    db_up: Metric;
  };
  infra: {
    status: State;
    message: string | null;
    instances: {
      id: string;
      name: string;
      state: string;
      cpu_percent: Metric;
      memory_percent: Metric;
    }[];
  };
  logs: {
    status: State;
    message: string | null;
    items: {
      timestamp: string;
      severity: string;
      message: string;
      trace_id: string | null;
    }[];
  };
  trace: {
    status: State;
    message: string | null;
    id: string | null;
    duration_ms: number | null;
    spans: {
      span_id: string;
      parent_id: string | null;
      name: string;
      service: string;
      started_at: string;
      duration_ms: number;
    }[];
  };
  migration: null | {
    workflow_id: string;
    run_id: string;
    stage:
      | "planned"
      | "preparing"
      | "copying"
      | "synchronizing"
      | "fencing_source"
      | "source_fenced"
      | "validated"
      | "promotion_pending"
      | "promoted"
      | "cleaned"
      | "aborted";
    lag_bytes: number | null;
    validation: "pending" | "passed";
    observed_at: string;
    status: "ok" | "stale" | "blocked" | "completed";
  };
};

export const observabilityApi = {
  services: () => api<Page<Service>>("/services"),
  environments: (serviceId: string, signal?: AbortSignal) =>
    api<{ items: Environment[] }>(
      `/services/${encodeURIComponent(serviceId)}/environments`,
      undefined,
      undefined,
      signal,
    ),
  snapshot: (
    serviceId: string,
    environmentId: string,
    minutes: number,
    signal?: AbortSignal,
  ) =>
    api<Observability>(
      `/services/${encodeURIComponent(serviceId)}/observability?environment_id=${encodeURIComponent(environmentId)}&minutes=${minutes}`,
      undefined,
      undefined,
      signal,
    ),
};
