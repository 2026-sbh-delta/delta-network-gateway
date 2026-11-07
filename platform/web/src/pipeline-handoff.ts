const key = (runId: string) => `delta:pipeline-handoff:${runId}`;

export function openPipelineRun(runId: string, warning?: string) {
  try {
    if (warning) sessionStorage.setItem(key(runId), warning);
    else sessionStorage.removeItem(key(runId));
  } catch {
    // Navigation still works when browser storage is unavailable.
  }
  location.hash = `/pipeline/${encodeURIComponent(runId)}`;
}

export function pipelineHandoffWarning(runId: string | null) {
  try {
    return runId ? sessionStorage.getItem(key(runId)) : null;
  } catch {
    return null;
  }
}
