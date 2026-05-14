import json
import threading
import time
import uuid
import webbrowser
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from marketing_campaign_agent import get_marketing_system_prompt, set_marketing_system_prompt

APPROVAL_HOST = "127.0.0.1"
APPROVAL_PORT = 8787
APPROVAL_URL = f"http://{APPROVAL_HOST}:{APPROVAL_PORT}"

_SERVER = None
_SERVER_LOCK = threading.Lock()
_ITEMS = {}
_ITEMS_LOCK = threading.Lock()

_STATS_LOCK = threading.Lock()
_STATS = {
    "replied": 0,
    "cancelled": 0,
    "total_queued": 0,
    "unread_inbox": None,
    "recent_inbox_sample": None,
    "stats_updated_at": None,
}


def update_inbox_stats(unread_count, recent_fetched_count):
    """Called from main agent loop so the UI can show live mailbox numbers."""
    with _STATS_LOCK:
        _STATS["unread_inbox"] = unread_count
        _STATS["recent_inbox_sample"] = recent_fetched_count
        _STATS["stats_updated_at"] = time.time()


def _stats_snapshot(pending_count):
    with _STATS_LOCK:
        return {
            "pending_queue": pending_count,
            "unread_inbox": _STATS["unread_inbox"],
            "recent_inbox_sample": _STATS["recent_inbox_sample"],
            "total_ever_queued": _STATS["total_queued"],
            "replied": _STATS["replied"],
            "cancelled": _STATS["cancelled"],
            "stats_updated_at": _STATS["stats_updated_at"],
        }


HTML_PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Email | Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      color-scheme: dark;
      --bg: #0c0d0f;
      --surface: #15171b;
      --card: #1c1f24;
      --border: #2d333b;
      --text: #e6edf3;
      --muted: #8b949e;
      --accent: #2f81f7;
      --success: #238636;
      --danger: #da3633;
      --warning: #d29922;
      --font: "Inter", system-ui, -apple-system, sans-serif;
      --radius: 10px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: var(--font);
      color: var(--text);
      background-color: var(--bg);
      line-height: 1.5;
      overflow-x: hidden;
    }
    
    /* Top Header */
    header {
      padding: 12px 24px;
      background: #010409;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 100;
    }
    .top-nav {
      display: flex;
      gap: 24px;
    }
    .nav-tab {
      padding: 8px 4px;
      font-size: 0.85rem;
      font-weight: 600;
      color: var(--muted);
      cursor: pointer;
      background: none;
      border: none;
      border-bottom: 2px solid transparent;
      transition: all 0.2s;
    }
    .nav-tab.active {
      color: var(--accent);
      border-bottom-color: var(--accent);
    }
    .nav-tab:hover:not(.active) {
      color: var(--text);
    }

    main {
      max-width: 1400px;
      margin: 0 auto;
      padding: 24px;
    }

    /* Connector Cards */
    .connectors {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 20px;
      margin-bottom: 24px;
    }
    .connector-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .connector-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .connector-info {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .connector-icon {
      width: 32px;
      height: 32px;
      border-radius: 6px;
      background: var(--accent);
      display: grid;
      place-items: center;
      font-weight: bold;
    }
    .connector-name {
      font-size: 0.95rem;
      font-weight: 700;
    }
    .status-badge {
      font-size: 0.7rem;
      font-weight: 700;
      text-transform: uppercase;
      padding: 2px 8px;
      border-radius: 4px;
      background: rgba(35, 134, 54, 0.15);
      color: var(--success);
      border: 1px solid rgba(35, 134, 54, 0.3);
    }
    .connector-desc {
      font-size: 0.75rem;
      color: var(--muted);
    }
    .connector-meta {
      font-size: 0.75rem;
      background: #0d1117;
      padding: 8px;
      border-radius: 6px;
      color: var(--muted);
      font-family: monospace;
    }
    .disconnect-btn {
      align-self: flex-start;
      background: none;
      border: 1px solid var(--border);
      color: var(--muted);
      padding: 4px 12px;
      border-radius: 4px;
      font-size: 0.75rem;
      cursor: pointer;
    }

    /* Stats Grid */
    .stats-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin-bottom: 24px;
    }
    .stat-tile {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px 16px;
    }
    .stat-label {
      font-size: 0.65rem;
      font-weight: 700;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 4px;
    }
    .stat-value {
      font-size: 1.5rem;
      font-weight: 800;
      letter-spacing: -0.02em;
    }

    /* Two Column Layout */
    .dashboard-content {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 24px;
      align-items: start;
    }
    .column-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 16px;
      padding-bottom: 8px;
      border-bottom: 1px solid var(--border);
    }
    .column-title {
      font-size: 0.75rem;
      font-weight: 800;
      text-transform: uppercase;
      color: var(--muted);
      letter-spacing: 0.05em;
    }
    .column-count {
      font-size: 0.7rem;
      color: var(--muted);
    }

    /* Item Cards */
    .item-list {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .item-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
      position: relative;
    }
    .item-card.pipeline { border-left: 4px solid var(--warning); }
    .item-card.approval { border-left: 4px solid var(--accent); }
    
    .item-badge {
      position: absolute;
      top: 16px;
      right: 16px;
      font-size: 0.6rem;
      font-weight: 700;
      padding: 2px 6px;
      border-radius: 4px;
      background: rgba(210, 153, 34, 0.15);
      color: var(--warning);
    }
    .item-card.approval .item-badge {
      background: rgba(47, 129, 247, 0.15);
      color: var(--accent);
    }

    .item-name { font-weight: 700; font-size: 0.9rem; margin-bottom: 2px; }
    .item-sub { font-size: 0.75rem; color: var(--muted); margin-bottom: 8px; }
    .item-snippet {
      font-size: 0.8rem;
      color: #7d8590;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      margin-bottom: 12px;
    }
    .item-date { font-size: 0.7rem; color: var(--muted); }

    /* Approval Form */
    .approval-form {
      margin-top: 16px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .input-label {
      font-size: 0.65rem;
      font-weight: 700;
      text-transform: uppercase;
      color: var(--muted);
    }
    .msg-box {
      background: #0d1117;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 12px;
      font-size: 0.8rem;
      color: var(--text);
      white-space: pre-wrap;
      max-height: 150px;
      overflow-y: auto;
    }
    textarea {
      background: #0d1117;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 12px;
      font-family: inherit;
      font-size: 0.85rem;
      color: var(--text);
      min-height: 120px;
      width: 100%;
      resize: vertical;
    }
    textarea:focus { outline: 1px solid var(--accent); }
    
    .button-row { display: flex; gap: 8px; }
    .btn {
      padding: 8px 16px;
      border-radius: 6px;
      font-size: 0.85rem;
      font-weight: 600;
      cursor: pointer;
      border: 1px solid var(--border);
      transition: all 0.2s;
    }
    .btn-primary {
      background: var(--accent);
      color: #fff;
      border: none;
    }
    .btn-primary:hover { background: #478be6; }
    .btn-secondary {
      background: var(--surface);
      color: var(--text);
    }
    .btn-secondary:hover { background: var(--border); }

    /* Marketing View */
    .view-content { display: none; }
    .view-content.active { display: block; }
    .marketing-container {
      max-width: 800px;
      margin: 0 auto;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 32px;
    }
    .marketing-container h2 { margin-top: 0; }
    .marketing-container textarea { min-height: 400px; margin-bottom: 20px; }

    .status-msg {
      margin-top: 12px;
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 0.85rem;
      display: none;
    }
    .status-msg.success { background: rgba(35, 134, 54, 0.15); color: var(--success); display: block; }
    .status-msg.error { background: rgba(218, 54, 51, 0.15); color: var(--danger); display: block; }

    @media (max-width: 1000px) {
      .dashboard-content, .connectors { grid-template-columns: 1fr; }
      .stats-grid { grid-template-columns: 1fr 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div class="top-nav">
      <button class="nav-tab active" data-target="dashboard-view">Pipeline</button>
      <!--<button class="nav-tab">Scorecard</button>-->
      <button class="nav-tab" data-target="marketing-view">Agent prompt</button>
    </div>
  </header>

  <main>
    <div id="dashboard-view" class="view-content active">
      <!-- Connectors Section -->
      <section class="connectors">
        <div class="connector-card">
          <div class="connector-header">
            <div class="connector-info">
              <div class="connector-icon" style="background: #0078d4;">O</div>
              <div class="connector-name">Outlook</div>
            </div>
            <span class="status-badge">Connected</span>
          </div>
          <p class="connector-desc">Read inbound replies - send approved drafts</p>
          <div class="connector-meta">Account: haris@outlook.com</div>
          <button class="disconnect-btn">Disconnect</button>
        </div>
        <div class="connector-card">
          <div class="connector-header">
            <div class="connector-info">
              <div class="connector-icon" style="background: #00a4ef;">S</div>
              <div class="connector-name">OneDrive</div>
            </div>
            <span class="status-badge">Connected</span>
          </div>
          <p class="connector-desc">Marketing & product context as grounding</p>
          <div class="connector-meta">Folder: /Marketing Campaigns</div>
          <button class="disconnect-btn">Disconnect</button>
        </div>
        <div class="connector-card">
          <div class="connector-header">
            <div class="connector-info">
              <div class="connector-icon" style="background: #0a66c2;">L</div>
              <div class="connector-name">LinkedIn</div>
            </div>
            <span class="status-badge">Connected</span>
          </div>
          <p class="connector-desc">Context & profiles - post content</p>
          <div class="connector-meta">Profile: Muhammad Usama</div>
          <button class="disconnect-btn">Disconnect</button>
        </div>
      </section>

      <!-- Stats Section -->
      <section class="stats-grid" id="stats-container">
        <div class="stat-tile">
          <div class="stat-label">Pipeline</div>
          <div class="stat-value" id="stat-total">0</div>
        </div>
        <div class="stat-tile">
          <div class="stat-label">Awaiting Reply</div>
          <div class="stat-value" id="stat-pending">0</div>
        </div>
        <div class="stat-tile">
          <div class="stat-label">Approval Queue</div>
          <div class="stat-value" id="stat-queue">0</div>
        </div>
        <div class="stat-tile">
          <div class="stat-label">Events Recorded</div>
          <div class="stat-value" id="stat-replied">0</div>
        </div>
      </section>

      <!-- Main Dashboard Grid -->
      <div class="dashboard-content">
        <!-- Pipeline Column -->
        <div class="column">
          <div class="column-header">
            <div class="column-title">Pipeline</div>
            <div class="column-count" id="pipeline-count">0 Items</div>
          </div>
          <div class="item-list" id="pipeline-list"></div>
        </div>

        <!-- Approval Column -->
        <div class="column">
          <div class="column-header">
            <div class="column-title">Human Approval Queue</div>
            <div class="column-count" id="approval-count">0 Pending</div>
          </div>
          <div class="item-list" id="approval-list"></div>
        </div>
      </div>
    </div>

    <!-- Marketing/Prompt View -->
    <div id="marketing-view" class="view-content">
      <div class="marketing-container">
        <h2>Agent System Prompt</h2>
        <p style="color: var(--muted); margin-bottom: 24px;">Configure the core identity and rules for the Marketing Campaign Agent. Changes apply to all new document analyses.</p>
        <div class="approval-form">
          <label class="input-label">System Prompt</label>
          <textarea id="marketing-prompt-textarea" placeholder="Paste system prompt here..."></textarea>
          <button id="save-marketing-prompt" class="btn btn-primary" style="align-self: flex-start; padding: 12px 24px;">Save Prompt</button>
          <div id="marketing-status" class="status-msg"></div>
        </div>
      </div>
    </div>
  </main>

  <script>
    const pipelineList = document.getElementById("pipeline-list");
    const approvalList = document.getElementById("approval-list");
    const marketingTextarea = document.getElementById("marketing-prompt-textarea");
    const saveMarketingBtn = document.getElementById("save-marketing-prompt");
    const marketingStatus = document.getElementById("marketing-status");

    // Tab Switching
    document.querySelectorAll(".nav-tab").forEach(tab => {
      tab.addEventListener("click", () => {
        if (!tab.dataset.target) return;
        document.querySelectorAll(".nav-tab").forEach(t => t.classList.remove("active"));
        document.querySelectorAll(".view-content").forEach(v => v.classList.remove("active"));
        tab.classList.add("active");
        document.getElementById(tab.dataset.target).classList.add("active");
        if (tab.dataset.target === "marketing-view") loadMarketingPrompt();
      });
    });

    function formatTime(ts) {
      if (!ts) return "";
      const d = new Date(ts * 1000);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) + " · " + d.toLocaleDateString([], { month: 'short', day: 'numeric' });
    }

    function escapeHtml(v) {
      return String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    }

    function createPipelineCard(item) {
      return `
        <div class="item-card pipeline">
          <div class="item-badge">Shared working here</div>
          <div class="item-name">${escapeHtml(item.sender)}</div>
          <div class="item-sub">${escapeHtml(item.subject)}</div>
          <div class="item-snippet">${escapeHtml(item.body)}</div>
          <div class="item-date">Last touch: ${formatTime(item.created_at)}</div>
        </div>
      `;
    }

    function createApprovalCard(item) {
      return `
        <div class="item-card approval" data-id="${item.id}">
          <div class="item-name">${escapeHtml(item.sender)}</div>
          <div class="item-sub">${escapeHtml(item.subject)}</div>
          <div class="approval-form">
            <div class="input-label">Incoming Message</div>
            <div class="msg-box">${escapeHtml(item.body)}</div>
            <div class="input-label">AI Draft Response</div>
            <textarea data-draft>${escapeHtml(item.draft)}</textarea>
            <div class="button-row">
              <button class="btn btn-primary" onclick="decide('${item.id}', 'reply', this)">Approve & Send</button>
              <button class="btn btn-secondary" onclick="decide('${item.id}', 'cancel', this)">Discard</button>
            </div>
          </div>
        </div>
      `;
    }

    async function loadItems() {
      try {
        const [itemsRes, statsRes] = await Promise.all([
          fetch("/api/items"),
          fetch("/api/stats"),
        ]);
        const data = await itemsRes.json();
        const stats = await statsRes.json();
        const items = data.items || [];

        // Update Stats
        document.getElementById("stat-total").textContent = stats.total_ever_queued || 0;
        document.getElementById("stat-pending").textContent = items.length;
        document.getElementById("stat-queue").textContent = items.length;
        document.getElementById("stat-replied").textContent = stats.replied || 0;
        
        document.getElementById("pipeline-count").textContent = items.length + " Items";
        document.getElementById("approval-count").textContent = items.length + " Pending";

        // Preserve textarea edits if any
        const drafts = {};
        approvalList.querySelectorAll("textarea").forEach(ta => {
          const card = ta.closest(".item-card");
          if (card) drafts[card.dataset.id] = ta.value;
        });

        // Render
        pipelineList.innerHTML = items.map(createPipelineCard).join("");
        approvalList.innerHTML = items.map(createApprovalCard).join("");

        // Restore drafts
        approvalList.querySelectorAll("textarea").forEach(ta => {
          const card = ta.closest(".item-card");
          if (card && drafts[card.dataset.id]) ta.value = drafts[card.dataset.id];
        });
      } catch (e) { console.error(e); }
    }

    async function decide(id, action, btn) {
      const card = btn.closest(".item-card");
      const draft = card.querySelector("textarea").value;
      btn.disabled = true;
      try {
        const res = await fetch("/api/" + action, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id, final_draft: draft })
        });
        if (res.ok) loadItems();
        else alert("Action failed");
      } catch (e) { alert("Error connecting to server"); }
    }

    async function loadMarketingPrompt() {
      try {
        const res = await fetch("/api/marketing-prompt");
        const data = await res.json();
        marketingTextarea.value = data.prompt || "";
      } catch (e) {}
    }

    saveMarketingBtn.addEventListener("click", async () => {
      saveMarketingBtn.disabled = true;
      marketingStatus.textContent = "Saving...";
      marketingStatus.className = "status-msg";
      marketingStatus.style.display = "block";
      try {
        const res = await fetch("/api/marketing-prompt", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt: marketingTextarea.value })
        });
        if (res.ok) {
          marketingStatus.textContent = "✓ Prompt saved successfully";
          marketingStatus.className = "status-msg success";
        } else {
          throw new Error();
        }
      } catch (e) {
        marketingStatus.textContent = "✗ Failed to save prompt";
        marketingStatus.className = "status-msg error";
      } finally {
        saveMarketingBtn.disabled = false;
        setTimeout(() => marketingStatus.style.display = "none", 3000);
      }
    });

    loadItems();
    setInterval(loadItems, 3000);
  </script>
</body>
</html>
"""


class ApprovalRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _send(self, status, body, content_type="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw_body = self.rfile.read(length).decode("utf-8")
        return json.loads(raw_body or "{}")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, HTML_PAGE, "text/html; charset=utf-8")
            return

        if path == "/api/items":
            with _ITEMS_LOCK:
                items = [
                    {
                        "id": item_id,
                        "sender": item["email_data"].get("sender", "Unknown"),
                        "subject": item["email_data"].get("subject", "No Subject"),
                        "body": item["email_data"].get("body", ""),
                        "generated_subject": f"Re: {item['email_data'].get('subject', 'No Subject')}",
                        "draft": item["draft"],
                        "created_at": item.get("created_at", 0),
                    }
                    for item_id, item in _ITEMS.items()
                    if item["result"]["status"] == "pending"
                ]
            items.sort(key=lambda x: x.get("created_at", 0))
            self._send(200, {"items": items})
            return

        if path == "/api/stats":
            with _ITEMS_LOCK:
                pending = sum(
                    1
                    for it in _ITEMS.values()
                    if it["result"]["status"] == "pending"
                )
            self._send(200, _stats_snapshot(pending))
            return

        if path == "/api/marketing-prompt":
            prompt = get_marketing_system_prompt()
            self._send(200, {"prompt": prompt})
            return

        self._send(404, {"error": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/reply", "/api/cancel", "/api/marketing-prompt"):
            self._send(404, {"error": "Not found"})
            return

        try:
            payload = self._read_json()
            item_id = payload.get("id")
            final_draft = payload.get("final_draft", "")
        except Exception:
            self._send(400, {"error": "Invalid JSON"})
            return

        if path == "/api/marketing-prompt":
            new_prompt = payload.get("prompt")
            if new_prompt is not None:
                set_marketing_system_prompt(new_prompt)
                self._send(200, {"status": "ok", "prompt": new_prompt})
            else:
                self._send(400, {"error": "Missing prompt field"})
            return

        with _ITEMS_LOCK:
            item = _ITEMS.get(item_id)
            if not item:
                self._send(404, {"error": "Approval item not found"})
                return

            if item["result"]["status"] != "pending":
                self._send(400, {"error": "Already decided"})
                return

            if path == "/api/reply":
                item["result"]["status"] = "approved"
                item["result"]["final_draft"] = final_draft
                decision = "reply"
            else:
                item["result"]["status"] = "cancelled"
                item["result"]["final_draft"] = None
                decision = "cancel"

            ev = item.get("event")
            if ev is not None:
                ev.set()

        with _STATS_LOCK:
            if decision == "reply":
                _STATS["replied"] += 1
            else:
                _STATS["cancelled"] += 1

        self._send(200, {"status": "ok"})


def _start_server_once():
    global _SERVER
    with _SERVER_LOCK:
        if _SERVER:
            return

        _SERVER = ThreadingHTTPServer(
            (APPROVAL_HOST, APPROVAL_PORT), ApprovalRequestHandler
        )
        thread = threading.Thread(target=_SERVER.serve_forever, daemon=True)
        thread.start()
        print(f"Approval web UI running at {APPROVAL_URL}")

        try:
            webbrowser.open(APPROVAL_URL)
        except Exception:
            pass


def ensure_approval_server():
    """Start the HTTP UI once (non-blocking). Safe to call from main loop."""
    _start_server_once()


def enqueue_approval(email_data, draft):
    """
    Add an email + draft to the approval queue without blocking the agent loop.
    The UI updates via polling; the main process should call pop_completed_approvals()
    periodically to send replies and clear completed items.
    """
    _start_server_once()
    item_id = str(uuid.uuid4())
    item = {
        "email_data": email_data,
        "draft": draft,
        "event": None,
        "result": {"status": "pending", "final_draft": None},
        "created_at": time.time(),
    }
    with _ITEMS_LOCK:
        _ITEMS[item_id] = item
    with _STATS_LOCK:
        _STATS["total_queued"] += 1
    print(f"Queued for approval: {email_data.get('subject')} → open {APPROVAL_URL}")
    return item_id


def pop_completed_approvals():
    """
    Return and remove all items that were approved or cancelled in the UI.
    Each entry: {"email_data", "approved": bool, "final_draft": str|None}.
    """
    done = []
    with _ITEMS_LOCK:
        to_pop = []
        for item_id, item in list(_ITEMS.items()):
            status = item["result"]["status"]
            if status == "pending":
                continue
            done.append(
                {
                    "id": item_id,
                    "email_data": item["email_data"],
                    "approved": status == "approved",
                    "final_draft": item["result"].get("final_draft"),
                }
            )
            to_pop.append(item_id)
        for item_id in to_pop:
            _ITEMS.pop(item_id, None)
    return done


def _add_approval_item(email_data, draft):
    item_id = str(uuid.uuid4())
    event = threading.Event()
    item = {
        "email_data": email_data,
        "draft": draft,
        "event": event,
        "result": {"status": "pending", "final_draft": None},
        "created_at": time.time(),
    }
    with _ITEMS_LOCK:
        _ITEMS[item_id] = item
    with _STATS_LOCK:
        _STATS["total_queued"] += 1
    return item_id, item


def _remove_approval_item(item_id):
    with _ITEMS_LOCK:
        _ITEMS.pop(item_id, None)


def get_approval(email_data, draft, ai_agent_module, history=None, attachment_context=None):
    """Blocking approval (legacy). Prefer enqueue_approval + pop_completed_approvals in new code."""
    _start_server_once()
    item_id, item = _add_approval_item(email_data, draft)
    print(f"Open {APPROVAL_URL} to approve or cancel the pending reply.")

    item["event"].wait()
    result = item["result"]
    _remove_approval_item(item_id)

    if result["status"] == "approved":
        return True, result["final_draft"]
    return False, None
