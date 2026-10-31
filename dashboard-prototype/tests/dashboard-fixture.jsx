import { useState } from "react";
import { createRoot } from "react-dom/client";
import { App } from "../src/App.jsx";
import { GroupDetailView, GroupsView } from "../src/views/GroupsView.jsx";
import "../src/styles.css";

function Fixture() {
  const [refreshKey, setRefreshKey] = useState(0);
  const [chatId, setChatId] = useState("-1001");
  const screen = new URLSearchParams(location.search).get("screen");
  if (screen === "app") return <App />;
  return <>
    <button onClick={() => setRefreshKey((value) => value + 1)}>Refresh fixture</button>
    <button onClick={() => setChatId("-1002")}>Other fixture group</button>
    {screen === "detail"
      ? <GroupDetailView chatId={chatId} refreshKey={refreshKey} onBack={() => {}}
          onCreatedAction={() => {}} onToast={() => {}} />
      : <GroupsView refreshKey={refreshKey} onOpenGroup={() => {}} />}
  </>;
}

createRoot(document.getElementById("root")).render(<Fixture />);
