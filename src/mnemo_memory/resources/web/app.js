const byId = (id) => document.getElementById(id);

function card(title, state, body, command) {
  const item = document.createElement("article");
  item.className = "card";
  const top = document.createElement("div");
  top.className = "card-top";
  const heading = document.createElement("h3");
  heading.textContent = title;
  const badge = document.createElement("span");
  badge.className = `badge ${state === "ready" ? "ready" : "pending"}`;
  badge.textContent = state;
  top.append(heading, badge);
  const text = document.createElement("p");
  text.textContent = body;
  item.append(top, text);
  if (command) {
    const code = document.createElement("code");
    code.textContent = command;
    item.append(code);
  }
  return item;
}

function render(data) {
  const lifecycle = data.lifecycle;
  byId("service-state").textContent = lifecycle.initialized ? "Store ready" : "Setup needed";
  byId("service-state").className = `pill ${lifecycle.initialized ? "ready" : ""}`;
  byId("version").textContent = data.version;
  const connections = data.connections;
  const connected = connections.codex.connected || connections.claude_code.connected;
  const registered = data.project.registered;
  const steps = [
    ["Initialize the store", lifecycle.initialized, "Create the private local database and configuration.", "mnemo-memory init"],
    ["Connect a coding agent", connected, "Register the local MCP server with Codex or Claude Code.", "mnemo-memory connect codex --auto-memory --yes"],
    ["Enable this project", registered, "Bind the current repository to a stable private scope.", "mnemo-memory memory enable codex --yes"],
  ];
  const onboarding = byId("onboarding");
  onboarding.replaceChildren(...steps.map(([title, ready, body, command]) => card(title, ready ? "ready" : "next", body, command)));
  byId("progress").textContent = `${steps.filter((step) => step[1]).length} / 3 ready`;

  const source = data.indexes.source;
  const dbt = data.indexes.dbt;
  const knowledge = data.indexes.knowledge;
  byId("indexes").replaceChildren(
    card("Source structure", source.status, `${source.files} files · ${source.symbols} symbols · ${source.relationships} relationships`, null),
    card("dbt lineage", dbt.status, `${dbt.nodes} nodes · ${dbt.relationships} relationships`, null),
    card("Project knowledge", knowledge.status, `${knowledge.documents} current documents`, null),
  );
  const privacy = byId("privacy-list");
  privacy.replaceChildren(...[
    "The personal profile uses local SQLite storage.",
    "The service listens on loopback only.",
    "Optional model calls are disabled by default.",
    "Retrieved documents remain cited untrusted evidence.",
  ].map((value) => { const li = document.createElement("li"); li.textContent = value; return li; }));
}

function setSettings(settings) {
  const form = byId("settings-form");
  for (const [name, value] of Object.entries(settings)) {
    const input = form.elements.namedItem(name);
    if (!input) continue;
    if (input.type === "checkbox") input.checked = value;
    else input.value = value ?? "";
  }
  byId("settings-state").textContent = "Saved locally";
}

async function loadSettings() {
  const response = await fetch("/api/settings", {headers: {"Accept": "application/json"}});
  if (!response.ok) throw new Error("settings");
  setSettings(await response.json());
}

byId("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const enabled = form.elements.namedItem("optional_model_enabled").checked;
  const names = ["episodic_retention_days", "context_total_tokens", "context_active_task_checkpoint_tokens", "context_episodic_tokens", "context_knowledge_tokens", "context_structural_tokens", "context_skills_tokens", "context_provenance_tokens"];
  const value = {
    repository_knowledge_sync_enabled: form.elements.namedItem("repository_knowledge_sync_enabled").checked,
    approved_event_capture_enabled: form.elements.namedItem("approved_event_capture_enabled").checked,
    optional_model_enabled: enabled,
    model_provider: enabled ? form.elements.namedItem("model_provider").value.trim() : null,
    model_id: enabled ? form.elements.namedItem("model_id").value.trim() : null,
  };
  for (const name of names) value[name] = Number(form.elements.namedItem(name).value);
  byId("settings-state").textContent = "Saving…";
  const response = await fetch("/api/settings", {
    method: "PUT",
    headers: {"Content-Type": "application/json", "X-Mnemo-Intent": "update-settings"},
    body: JSON.stringify(value),
  });
  if (!response.ok) { byId("settings-state").textContent = "Invalid settings"; return; }
  setSettings(await response.json());
});

async function refresh() {
  byId("error").hidden = true;
  try {
    const response = await fetch("/api/dashboard", {headers: {"Accept": "application/json"}});
    if (!response.ok) throw new Error("status");
    render(await response.json());
  } catch (_) {
    byId("error").hidden = false;
    byId("service-state").textContent = "Unavailable";
  }
}

byId("refresh").addEventListener("click", refresh);
refresh();
loadSettings().catch(() => { byId("settings-state").textContent = "Unavailable"; });
