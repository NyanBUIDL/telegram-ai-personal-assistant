import { useState } from "react";
import { createRoot } from "react-dom/client";
import { App } from "../src/App.jsx";
import { GroupDetailView, GroupsView } from "../src/views/GroupsView.jsx";
import { KnowledgeView } from "../src/views/KnowledgeView.jsx";
import { ConnectionsView } from "../src/views/SystemViews.jsx";
import { OverviewView } from "../src/views/OverviewView.jsx";
import { api } from "../src/api.js";
import { useResource } from "../src/hooks.js";
import "../src/styles.css";

function ResourceFixture({ scope }) {
  const resource = useResource(() => api.group(scope), [scope]);
  return <>
    <button onClick={resource.reload}>Reload resource</button>
    <output data-testid="resource-value">{resource.data?.title}</output>
  </>;
}

function Fixture() {
  const [refreshKey, setRefreshKey] = useState(0);
  const [chatId, setChatId] = useState("-1001");
  const [toast, setToast] = useState("");
  const [action, setAction] = useState(null);
  const screen = new URLSearchParams(location.search).get("screen");
  if (screen === "app") return <App />;
  const callbacks = { onToast: setToast, onCreatedAction: setAction };
  return <>
    <button onClick={() => setRefreshKey((value) => value + 1)}>Refresh fixture</button>
    <button onClick={() => setChatId("-1002")}>Other fixture group</button>
    <output data-testid="fixture-toast">{toast}</output>
    <output data-testid="fixture-action">{action ? JSON.stringify(action) : ""}</output>
    {screen === "resource" ? <ResourceFixture scope={chatId} />
      : screen === "knowledge" ? <KnowledgeView refreshKey={refreshKey} {...callbacks} />
      : screen === "connections" ? <ConnectionsView refreshKey={refreshKey} realtimeState="live" onRetry={() => {}} />
      : screen === "overview" ? <OverviewView refreshKey={refreshKey} onReview={() => {}} onNavigate={() => {}} />
      : screen === "detail"
      ? <GroupDetailView chatId={chatId} refreshKey={refreshKey} onBack={() => {}}
          {...callbacks} />
      : <GroupsView refreshKey={refreshKey} onOpenGroup={() => {}} />}
  </>;
}

createRoot(document.getElementById("root")).render(<Fixture />);
