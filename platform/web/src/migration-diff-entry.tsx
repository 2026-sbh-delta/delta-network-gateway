import { createRoot, type Root } from "react-dom/client";
import { MigrationDiffComparison } from "./MigrationDiffComparison";

const uuid = "[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}";
const migrationRoute = new RegExp(`^#/migration/(${uuid})$`, "i");

// The deployed shell has its own React root. Attach only to its migration
// route and leave the shell bundle and its existing pipeline controls intact.
let mounted: { serviceId: string; host: HTMLElement; root: Root } | undefined;
let scheduled = false;

function sync() {
  scheduled = false;
  const serviceId = migrationRoute.exec(location.hash)?.[1];
  if (mounted && mounted.serviceId === serviceId && mounted.host.isConnected)
    return;
  if (mounted) {
    mounted.root.unmount();
    mounted.host.remove();
    mounted = undefined;
  }
  if (!serviceId) return;
  const map = document.querySelector(
    "#workspace-content section.migration-panel > section.migration-map",
  );
  if (!map) return;
  const host = document.createElement("div");
  map.after(host);
  const root = createRoot(host);
  mounted = { serviceId, host, root };
  root.render(<MigrationDiffComparison serviceId={serviceId} />);
}

function schedule() {
  if (scheduled) return;
  scheduled = true;
  requestAnimationFrame(sync);
}

window.addEventListener("hashchange", schedule);
new MutationObserver(() => {
  if (migrationRoute.test(location.hash) && !mounted?.host.isConnected)
    schedule();
}).observe(document.body, { childList: true, subtree: true });
schedule();
