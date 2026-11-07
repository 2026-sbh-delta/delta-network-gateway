/// <reference types="vite/client" />

import { useEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { api, message, setSession, type Session } from "./api";
import { PipelinePanel } from "./PipelinePanel";
import "./production-pipeline.css";

function ProductionPipeline() {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api<Session>("/auth/session")
      .then((session) => {
        if (!active) return;
        setSession(session);
        setReady(true);
      })
      .catch((reason: unknown) => active && setError(message(reason)));
    return () => {
      active = false;
    };
  }, []);
  if (error) return <p role="alert">{error}</p>;
  if (!ready) return <p role="status">파이프라인을 불러오는 중입니다.</p>;
  return <PipelinePanel />;
}

const roots = new Map<HTMLElement, Root>();
function sync() {
  for (const [element, root] of roots) {
    if (!element.isConnected) {
      root.unmount();
      roots.delete(element);
      setSession(null);
    }
  }
  const element = document.getElementById("delta-pipeline-root");
  if (element && !roots.has(element)) {
    const root = createRoot(element);
    roots.set(element, root);
    root.render(<ProductionPipeline />);
  }
}
const observer = new MutationObserver(sync);
function resume() {
  observer.observe(document.body, { childList: true, subtree: true });
  sync();
}
function pause() {
  observer.disconnect();
  for (const root of roots.values()) root.unmount();
  roots.clear();
  setSession(null);
}
function restored(event: PageTransitionEvent) {
  if (event.persisted) resume();
}
resume();
window.addEventListener("pagehide", pause);
window.addEventListener("pageshow", restored);
if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    window.removeEventListener("pagehide", pause);
    window.removeEventListener("pageshow", restored);
    pause();
  });
}
