import json
import threading
import time
import uuid
import webbrowser
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import re
from marketing_campaign_agent import get_marketing_system_prompt, set_marketing_system_prompt, generate_campaign_from_text
import send_dm

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


def clean_email_body(body):
    """
    Strips history/quoted messages from the email body to show only the latest message.
    """
    if not body:
        return ""
    
    # Common markers that indicate the start of a quoted thread or history
    history_markers = [
        r"(?i)^From:.*",
        r"(?i)^Sent:.*",
        r"(?i)^Subject:.*",
        r"(?i)^To:.*",
        r"(?i)^On\s+.*\s+wrote:.*",
        r"^---.*---.*",
        r"^_{10,}.*",
        r"(?i)^---Original Message---.*",
        r"(?i)^Begin forwarded message:.*",
        r"^\[.*\]<https?://.*>", # Signature links/logos
    ]
    
    lines = body.splitlines()
    clean_lines = []
    
    for line in lines:
        stripped_line = line.strip()
        is_history_start = False
        for marker in history_markers:
            if re.match(marker, stripped_line):
                is_history_start = True
                break
        if is_history_start:
            break
        clean_lines.append(line)
        
    return "\n".join(clean_lines).strip()


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
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
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
      word-break: break-all;
      overflow-wrap: anywhere;
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
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
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
      overflow: hidden;
      word-wrap: break-word;
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

    .item-name { 
      font-weight: 700; 
      font-size: 0.9rem; 
      margin-bottom: 2px;
      word-break: break-word;
      overflow-wrap: anywhere;
    }
    .item-sub { 
      font-size: 0.75rem; 
      color: var(--muted); 
      margin-bottom: 8px;
      word-break: break-word;
      overflow-wrap: anywhere;
    }
    .item-snippet {
      font-size: 0.8rem;
      color: #7d8590;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      margin-bottom: 12px;
      word-break: break-word;
      overflow-wrap: anywhere;
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

    /* Marketing View Redesign */
    .view-content { display: none; }
    .view-content.active { display: block; }
    .marketing-container {
      max-width: 1400px;
      margin: 0 auto;
    }
    .marketing-grid {
      display: grid;
      grid-template-columns: 400px 1fr;
      gap: 32px;
      align-items: start;
    }
    .marketing-sidebar {
      display: flex;
      flex-direction: column;
      gap: 24px;
      position: sticky;
      top: 24px;
    }
    .marketing-main {
      display: flex;
      flex-direction: column;
      gap: 24px;
    }
    .glass-card {
      background: rgba(255, 255, 255, 0.03);
      backdrop-filter: blur(12px);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 16px;
      padding: 24px;
      box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
    }
    .marketing-section h2 { 
      margin-top: 0; 
      margin-bottom: 8px; 
      font-size: 1.25rem;
      letter-spacing: -0.01em;
    }
    .marketing-section p { 
      font-size: 0.85rem; 
      color: var(--muted); 
      margin-bottom: 16px; 
    }
    
    /* Campaign Cards in Feed */
    .campaign-feed {
      display: flex;
      flex-direction: column;
      gap: 20px;
    }
    .campaign-card {
      border-left: 4px solid var(--accent);
      transition: transform 0.2s, box-shadow 0.2s;
    }
    .campaign-card:hover {
      transform: translateY(-2px);
      box-shadow: 0 12px 40px 0 rgba(0, 0, 0, 0.4);
    }
    .campaign-card.sent { border-left-color: var(--success); }
    .campaign-card.cancelled { border-left-color: var(--danger); opacity: 0.8; }
    
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
    }
    .card-tag {
      font-size: 0.7rem;
      font-weight: 700;
      text-transform: uppercase;
      padding: 4px 8px;
      border-radius: 4px;
      background: rgba(47, 129, 247, 0.1);
      color: var(--accent);
    }
    .campaign-card.sent .card-tag { background: rgba(35, 134, 54, 0.1); color: var(--success); }
    
    .campaign-card textarea {
      background: rgba(0, 0, 0, 0.2);
      border: 1px solid rgba(255, 255, 255, 0.05);
      margin-bottom: 16px;
      font-size: 0.9rem;
      line-height: 1.5;
    }

    .status-msg {
      margin-top: 12px;
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 0.85rem;
      display: none;
    }
    .status-msg.success { background: rgba(35, 134, 54, 0.15); color: var(--success); display: block; }
    .status-msg.error { background: rgba(218, 54, 51, 0.15); color: var(--danger); display: block; }

    /* Apollo CRM tab styling */
    .apollo-container {
      max-width: 1400px;
      margin: 0 auto;
    }
    .apollo-grid {
      display: grid;
      grid-template-columns: 350px 1fr;
      gap: 32px;
      align-items: start;
    }
    .apollo-sidebar {
      display: flex;
      flex-direction: column;
      gap: 20px;
      position: sticky;
      top: 24px;
      max-height: calc(100vh - 100px);
      overflow-y: auto;
      padding-right: 4px;
    }
    .apollo-sidebar::-webkit-scrollbar {
      width: 6px;
    }
    .apollo-sidebar::-webkit-scrollbar-track {
      background: transparent;
    }
    .apollo-sidebar::-webkit-scrollbar-thumb {
      background: var(--border);
      border-radius: 3px;
    }
    .apollo-sidebar::-webkit-scrollbar-thumb:hover {
      background: var(--accent);
    }
    .apollo-main {
      display: flex;
      flex-direction: column;
      gap: 20px;
    }
    .apollo-table-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 20px;
    }
    .apollo-lead-card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
      margin-bottom: 12px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      transition: transform 0.2s, box-shadow 0.2s;
    }
    .apollo-lead-card:hover {
      transform: translateY(-2px);
      box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    }
    .lead-row {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
    }
    .lead-details {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .lead-name {
      font-size: 1.05rem;
      font-weight: 700;
      color: #fff;
    }
    .lead-company-badge {
      display: inline-block;
      padding: 2px 6px;
      border-radius: 4px;
      background: rgba(47, 129, 247, 0.1);
      color: var(--accent);
      font-size: 0.75rem;
      font-weight: 600;
    }
    .lead-meta {
      font-size: 0.8rem;
      color: var(--muted);
    }
    .status-badge-apollo {
      font-size: 0.7rem;
      font-weight: 700;
      text-transform: uppercase;
      padding: 2px 8px;
      border-radius: 4px;
    }
    .status-fetched {
      background: rgba(210, 153, 34, 0.15);
      color: var(--warning);
      border: 1px solid rgba(210, 153, 34, 0.3);
    }
    .status-drafted {
      background: rgba(47, 129, 247, 0.15);
      color: var(--accent);
      border: 1px solid rgba(47, 129, 247, 0.3);
    }
    .status-sent {
      background: rgba(35, 134, 54, 0.15);
      color: var(--success);
      border: 1px solid rgba(35, 134, 54, 0.3);
    }
    .status-discarded {
      background: rgba(218, 54, 51, 0.15);
      color: var(--danger);
      border: 1px solid rgba(218, 54, 51, 0.3);
    }
    .lead-draft-box {
      background: #0d1117;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 12px;
      margin-top: 8px;
      font-size: 0.85rem;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .btn-sm {
      padding: 6px 12px;
      font-size: 0.75rem;
    }

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
      <button class="nav-tab" data-target="marketing-view">Marketing Agent</button>
      <button class="nav-tab" data-target="apollo-view">Apollo CRM</button>
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
          <div class="connector-meta">Account: dalbir.bains@galaxypharma.net</div>
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
          <div class="connector-meta">Profile: Dalbir Bains</div>
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
        <div class="marketing-grid">
          <!-- Sidebar: Inputs and Prompt -->
          <div class="marketing-sidebar">
            <div class="marketing-section glass-card">
              <h2><i class="fas fa-magic"></i> New Campaign</h2>
              <p>Enter product details and target profile.</p>
              <div class="approval-form">
                <label class="input-label">Product Description</label>
                <textarea id="manual-marketing-description" placeholder="Describe the product..." style="min-height: 120px;"></textarea>
                
                <label class="input-label" style="margin-top: 10px;">Target LinkedIn URL</label>
                <input type="text" id="manual-marketing-url" placeholder="Paste LinkedIn profile link here..." style="background: rgba(0,0,0,0.2); border: 1px solid var(--border); border-radius: 6px; padding: 10px; color: #fff; margin-bottom: 15px; width: 100%; box-sizing: border-box;">
                
                <button id="generate-manual-marketing" class="btn btn-primary" style="width: 100%;">
                  Generate Campaign
                </button>
              </div>
            </div>

            <div class="marketing-section glass-card">
              <h2><i class="fas fa-cog"></i> Settings</h2>
              <p>Adjust the AI's core persona and rules.</p>
              <div class="approval-form">
                <textarea id="marketing-prompt-textarea" placeholder="System prompt..." style="min-height: 150px; font-size: 0.8rem;"></textarea>
                <button id="save-marketing-prompt" class="btn btn-secondary" style="width: 100%;">
                  Save Settings
                </button>
                <div id="marketing-status" class="status-msg"></div>
              </div>
            </div>
          </div>

          <!-- Main Area: Campaign Feed -->
          <div class="marketing-main">
            <div class="column-header">
              <div class="column-title">Campaign History Feed</div>
              <div id="manual-marketing-status" class="status-msg" style="margin: 0;"></div>
            </div>
            <div class="campaign-feed" id="campaign-history-feed">
              <!-- Empty State -->
              <div id="feed-empty-state" style="text-align: center; padding: 60px; color: var(--muted);">
                <i class="fas fa-paper-plane" style="font-size: 3rem; margin-bottom: 16px; opacity: 0.2;"></i>
                <p>No campaigns generated yet.<br>Use the sidebar to start a new one.</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Apollo CRM View -->
    <div id="apollo-view" class="view-content">
      <div class="apollo-container">
        <!-- CRM Stats Dashboard Scorecard -->
        <section class="stats-grid" style="margin-bottom: 24px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px;">
          <div class="stat-card" style="border-top: 4px solid var(--accent); background: var(--card); border-radius: var(--radius); padding: 20px; border-left: 1px solid var(--border); border-right: 1px solid var(--border); border-bottom: 1px solid var(--border);">
            <div class="stat-value" id="apollo-stat-total" style="font-size: 2rem; font-weight: 800; color: #fff;">0</div>
            <div class="stat-label" style="color: var(--muted); font-size: 0.85rem; font-weight: 600; margin-top: 4px;"><i class="fas fa-users" style="margin-right: 6px; color: var(--accent);"></i> Total Enriched Leads</div>
          </div>
          <div class="stat-card" style="border-top: 4px solid var(--success); background: var(--card); border-radius: var(--radius); padding: 20px; border-left: 1px solid var(--border); border-right: 1px solid var(--border); border-bottom: 1px solid var(--border);">
            <div class="stat-value" id="apollo-stat-sent" style="font-size: 2rem; font-weight: 800; color: #fff;">0</div>
            <div class="stat-label" style="color: var(--muted); font-size: 0.85rem; font-weight: 600; margin-top: 4px;"><i class="fas fa-paper-plane" style="margin-right: 6px; color: var(--success);"></i> Sent Campaigns</div>
          </div>
          <div class="stat-card" style="border-top: 4px solid var(--danger); background: var(--card); border-radius: var(--radius); padding: 20px; border-left: 1px solid var(--border); border-right: 1px solid var(--border); border-bottom: 1px solid var(--border);">
            <div class="stat-value" id="apollo-stat-discarded" style="font-size: 2rem; font-weight: 800; color: #fff;">0</div>
            <div class="stat-label" style="color: var(--muted); font-size: 0.85rem; font-weight: 600; margin-top: 4px;"><i class="fas fa-trash-alt" style="margin-right: 6px; color: var(--danger);"></i> Discarded Leads</div>
          </div>
        </section>

        <div class="apollo-grid">
          <!-- Sidebar: Control Panel -->
          <div class="apollo-sidebar">
            <div class="marketing-section glass-card">
              <h2><i class="fas fa-search"></i> Apollo Lead Finder</h2>
              <p>Search and enrich targets matching your ideal pharmaceutical profile.</p>
              
              <div class="approval-form">
                <label class="input-label">Domain Keywords</label>
                <input type="text" id="apollo-keyword" value="pharmaceutical" style="background: rgba(0,0,0,0.2); border: 1px solid var(--border); border-radius: 6px; padding: 8px; color: #fff; width: 100%; box-sizing: border-box; font-size: 0.85rem; margin-bottom: 10px;">
                
                <label class="input-label">Target Roles (comma-separated)</label>
                <textarea id="apollo-roles" style="background: rgba(0,0,0,0.2); border: 1px solid var(--border); border-radius: 6px; padding: 10px; color: #fff; font-size: 0.8rem; width: 100%; box-sizing: border-box; min-height: 100px; line-height: 1.4; resize: vertical; margin-bottom: 10px;">Procurement Manager, Business Development, Supply Chain Manager, Purchasing Manager, Sourcing Manager</textarea>
                
                <div style="display: flex; gap: 10px; margin-top: 10px;">
                  <div style="flex: 1;">
                    <label class="input-label">Pages</label>
                    <input type="number" id="apollo-max-pages" value="1" min="1" max="10" style="background: rgba(0,0,0,0.2); border: 1px solid var(--border); border-radius: 6px; padding: 8px; color: #fff; width: 100%; box-sizing: border-box;">
                  </div>
                  <div style="flex: 1;">
                    <label class="input-label">Per Page</label>
                    <input type="number" id="apollo-per-page" value="10" min="5" max="100" style="background: rgba(0,0,0,0.2); border: 1px solid var(--border); border-radius: 6px; padding: 8px; color: #fff; width: 100%; box-sizing: border-box;">
                  </div>
                </div>

                <button id="fetch-apollo-btn" class="btn btn-primary" style="width: 100%; margin-top: 15px;">
                  Find & Enrich Leads
                </button>
                <div id="apollo-fetch-status" class="status-msg" style="display: none; font-size: 0.8rem;"></div>
              </div>
            </div>

            <div class="marketing-section glass-card">
              <h2><i class="fas fa-cog"></i> Apollo System Prompt</h2>
              <p>Adjust the AI prompt rules for personalized pharmaceutical outreaches.</p>
              <div class="approval-form">
                <textarea id="apollo-prompt-textarea" placeholder="Apollo Campaign Prompt..." style="min-height: 280px; font-size: 0.8rem; width: 100%; box-sizing: border-box; background: rgba(0,0,0,0.2); border: 1px solid var(--border); border-radius: 6px; padding: 10px; color: #fff; line-height: 1.4; resize: vertical;"></textarea>
                <button id="save-apollo-prompt-btn" class="btn btn-secondary" style="width: 100%; margin-top: 8px;">
                  Save Prompt Settings
                </button>
                <div id="apollo-prompt-status" class="status-msg" style="display: none; margin-top: 8px;"></div>
              </div>
            </div>
          </div>

          <!-- Main Panel: Contacts & CRM -->
          <div class="apollo-main">
            <div class="column-header">
              <div class="column-title">Apollo Prospect Database</div>
              <div class="column-count" id="apollo-contacts-count">0 Leads</div>
            </div>
            
            <div id="apollo-contacts-list" style="display: flex; flex-direction: column; gap: 15px;">
              <!-- Lead Cards go here -->
              <div id="apollo-empty-state" style="text-align: center; padding: 60px; color: var(--muted); background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);">
                <i class="fas fa-users" style="font-size: 3rem; margin-bottom: 16px; opacity: 0.2;"></i>
                <p>No leads fetched yet.<br>Click "Find & Enrich Leads" to retrieve pharmaceutical targets.</p>
              </div>
            </div>
          </div>
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
    
    const manualMarketingDescription = document.getElementById("manual-marketing-description");
    const generateManualMarketingBtn = document.getElementById("generate-manual-marketing");
    const manualMarketingStatus = document.getElementById("manual-marketing-status");

    // Tab Switching
    document.querySelectorAll(".nav-tab").forEach(tab => {
      tab.addEventListener("click", () => {
        if (!tab.dataset.target) return;
        document.querySelectorAll(".nav-tab").forEach(t => t.classList.remove("active"));
        document.querySelectorAll(".view-content").forEach(v => v.classList.remove("active"));
        tab.classList.add("active");
        document.getElementById(tab.dataset.target).classList.add("active");
        if (tab.dataset.target === "marketing-view") loadMarketingPrompt();
        if (tab.dataset.target === "apollo-view") {
          loadApolloContacts();
          loadApolloPrompt();
        }
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

    const manualMarketingUrl = document.getElementById("manual-marketing-url");
    const campaignHistoryFeed = document.getElementById("campaign-history-feed");
    const feedEmptyState = document.getElementById("feed-empty-state");

    function createCampaignCard(text, url) {
      const id = "camp-" + Date.now();
      const card = document.createElement("div");
      card.className = "campaign-card glass-card";
      card.id = id;
      const targetLabel = `Target: <strong style="color:var(--accent)">${url.split('/').filter(p=>p).pop()}</strong>`;
      
      card.innerHTML = `
        <div class="card-header">
          <span class="card-tag">Pending Review</span>
          <span style="font-size: 0.75rem; color: #888;">${targetLabel}</span>
          <input type="hidden" class="card-url" value="${escapeHtml(url || '')}">
        </div>
        <textarea style="min-height: 200px;">${escapeHtml(text)}</textarea>
        <div class="button-row">
          <button class="btn btn-primary btn-sm send-btn" onclick="sendCampaign('${id}')">
            <i class="fas fa-paper-plane"></i> Send
          </button>
          <button class="btn btn-secondary btn-sm" onclick="cancelCampaign('${id}')">
            <i class="fas fa-times"></i> Discard
          </button>
        </div>
        <div class="card-error status-msg error" style="display:none; margin-top:10px;"></div>
      `;
      return card;
    }

    async function sendCampaign(cardId) {
      const card = document.getElementById(cardId);
      const text = card.querySelector("textarea").value;
      const url = card.querySelector(".card-url").value;
      const btn = card.querySelector(".send-btn");
      const tag = card.querySelector(".card-tag");
      const cardError = card.querySelector(".card-error");
      
      btn.disabled = true;
      tag.textContent = "⌛ Sending...";
      cardError.style.display = "none";
      
      try {
        const res = await fetch("/api/send-manual-marketing", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text, profile_url: url })
        });
        if (res.ok) {
          tag.textContent = "✓ Sent";
          card.classList.add("sent");
          card.querySelector(".button-row").style.display = "none";
          card.querySelector("textarea").disabled = true;
        } else {
          const data = await res.json();
          cardError.textContent = "Failed to send: " + (data.error || "Unknown error");
          cardError.style.display = "block";
          btn.disabled = false;
          tag.textContent = "❌ Failed";
        }
      } catch (e) {
        cardError.textContent = "Network error";
        cardError.style.display = "block";
        btn.disabled = false;
        tag.textContent = "❌ Error";
      }
    }

    function cancelCampaign(cardId) {
      const card = document.getElementById(cardId);
      card.classList.add("cancelled");
      card.querySelector(".card-tag").textContent = "Cancelled";
      card.querySelector(".button-row").style.display = "none";
      card.querySelector("textarea").disabled = true;
      setTimeout(() => {
        card.style.opacity = "0.4";
      }, 500);
    }

    generateManualMarketingBtn.addEventListener("click", async () => {
      const description = manualMarketingDescription.value.trim();
      const url = manualMarketingUrl.value.trim();
      
      if (!description) {
        manualMarketingStatus.textContent = "✗ Please enter a product description";
        manualMarketingStatus.className = "status-msg error";
        manualMarketingStatus.style.display = "block";
        return;
      }
      if (!url) {
        manualMarketingStatus.textContent = "✗ Please enter a LinkedIn Profile URL";
        manualMarketingStatus.className = "status-msg error";
        manualMarketingStatus.style.display = "block";
        return;
      }

      generateManualMarketingBtn.disabled = true;
      manualMarketingStatus.textContent = "⌛ Generating message...";
      manualMarketingStatus.className = "status-msg";
      manualMarketingStatus.style.display = "block";

      try {
        const res = await fetch("/api/manual-marketing", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ description })
        });
        const data = await res.json();
        if (res.ok) {
          manualMarketingStatus.style.display = "none";
          if (feedEmptyState) feedEmptyState.style.display = "none";
          
          const card = createCampaignCard(data.message, url);
          campaignHistoryFeed.prepend(card);
          manualMarketingDescription.value = "";
          manualMarketingUrl.value = "";
        } else {
          manualMarketingStatus.textContent = "✗ " + (data.error || "Generation failed");
          manualMarketingStatus.className = "status-msg error";
        }
      } catch (e) {
        manualMarketingStatus.textContent = "✗ Network error";
        manualMarketingStatus.className = "status-msg error";
      } finally {
        generateManualMarketingBtn.disabled = false;
      }
    });

    // Apollo CRM Logic
    const apolloContactsList = document.getElementById("apollo-contacts-list");
    const apolloContactsCount = document.getElementById("apollo-contacts-count");
    const fetchApolloBtn = document.getElementById("fetch-apollo-btn");
    const apolloFetchStatus = document.getElementById("apollo-fetch-status");

    async function loadApolloPrompt() {
      try {
        const res = await fetch("/api/apollo/prompt");
        const data = await res.json();
        document.getElementById("apollo-prompt-textarea").value = data.prompt || "";
      } catch (e) {}
    }

    document.getElementById("save-apollo-prompt-btn").addEventListener("click", async () => {
      const btn = document.getElementById("save-apollo-prompt-btn");
      const textarea = document.getElementById("apollo-prompt-textarea");
      const status = document.getElementById("apollo-prompt-status");

      btn.disabled = true;
      status.textContent = "Saving...";
      status.className = "status-msg";
      status.style.display = "block";

      try {
        const res = await fetch("/api/apollo/prompt", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt: textarea.value })
        });
        if (res.ok) {
          status.textContent = "✓ Prompt saved successfully";
          status.className = "status-msg success";
        } else {
          throw new Error();
        }
      } catch (e) {
        status.textContent = "✗ Failed to save prompt";
        status.className = "status-msg error";
      } finally {
        btn.disabled = false;
        setTimeout(() => status.style.display = "none", 3000);
      }
    });

    async function loadApolloContacts() {
      try {
        const res = await fetch("/api/apollo/contacts");
        const data = await res.json();
        const contacts = data.contacts || [];
        
        // Calculate stats
        const total = contacts.length;
        const sent = contacts.filter(c => c.status === "Sent").length;
        const discarded = contacts.filter(c => c.status === "Discarded").length;
        
        // Update stats DOM elements safely
        const totalEl = document.getElementById("apollo-stat-total");
        const sentEl = document.getElementById("apollo-stat-sent");
        const discardedEl = document.getElementById("apollo-stat-discarded");
        if (totalEl) totalEl.textContent = total;
        if (sentEl) sentEl.textContent = sent;
        if (discardedEl) discardedEl.textContent = discarded;
        
        apolloContactsCount.textContent = total + " Leads";
        
        // Remove previous cards to avoid duplicates
        const cards = apolloContactsList.querySelectorAll(".apollo-lead-card");
        cards.forEach(c => c.remove());

        if (contacts.length === 0) {
          document.getElementById("apollo-empty-state").style.display = "block";
          return;
        }
        
        document.getElementById("apollo-empty-state").style.display = "none";
        
        contacts.forEach(c => {
          const card = createApolloLeadCard(c);
          apolloContactsList.appendChild(card);
        });
      } catch (e) {
        console.error("Error loading Apollo contacts:", e);
      }
    }

    function createApolloLeadCard(c) {
      const card = document.createElement("div");
      card.className = "apollo-lead-card";
      card.dataset.email = c.email;
      
      let statusClass = "status-fetched";
      if (c.status === "Draft Generated") statusClass = "status-drafted";
      if (c.status === "Sent") statusClass = "status-sent";
      if (c.status === "Discarded") statusClass = "status-discarded";
      
      const linkedinHtml = c.linkedin ? `<a href="${escapeHtml(c.linkedin)}" target="_blank" style="color:var(--accent); font-size:0.75rem; text-decoration:none;"><i class="fab fa-linkedin"></i> LinkedIn Profile</a>` : `<span style="color:var(--muted); font-size:0.75rem;"><i class="fab fa-linkedin"></i> No LinkedIn</span>`;
      
      let actionHtml = "";
      if (c.status === "Lead Fetched") {
        actionHtml = `
          <button class="btn btn-primary btn-sm" onclick="generateLeadDraft('${escapeHtml(c.email)}', this)">
            <i class="fas fa-magic"></i> Generate AI Campaign
          </button>
          <button class="btn btn-secondary btn-sm" onclick="discardLead('${escapeHtml(c.email)}', this)">
            Discard
          </button>
        `;
      } else if (c.status === "Draft Generated") {
        actionHtml = `
          <div class="lead-draft-box" style="width: 100%;">
            <div>
              <label class="input-label">Subject</label>
              <input type="text" class="draft-subject" value="${escapeHtml(c.subject || '')}" style="background:#1c1f24; border:1px solid var(--border); border-radius:4px; padding:6px; color:#fff; width:100%; box-sizing:border-box; font-size:0.85rem; font-weight:600; margin-top:4px;">
            </div>
            <div>
              <label class="input-label">Email Draft Body</label>
              <textarea class="draft-body" style="min-height:150px; font-size:0.8rem; margin-top:4px;">${escapeHtml(c.draft || '')}</textarea>
            </div>
            <div class="button-row">
              <button class="btn btn-primary btn-sm" onclick="sendLeadEmail('${escapeHtml(c.email)}', this)">
                <i class="fas fa-paper-plane"></i> Send via Outlook
              </button>
              <button class="btn btn-secondary btn-sm" onclick="generateLeadDraft('${escapeHtml(c.email)}', this)">
                <i class="fas fa-sync"></i> Regenerate
              </button>
              <button class="btn btn-secondary btn-sm" onclick="discardLead('${escapeHtml(c.email)}', this)">
                Discard
              </button>
            </div>
          </div>
        `;
      } else if (c.status === "Sent") {
        actionHtml = `
          <div style="color: var(--success); font-size: 0.85rem; font-weight: 600; display: flex; align-items: center; gap: 6px;">
            <i class="fas fa-check-circle"></i> Campaign Sent via Graph API
          </div>
          <div style="font-size:0.75rem; color: var(--muted); margin-top: 4px; margin-bottom: 8px;">
            <strong>Subject:</strong> ${escapeHtml(c.subject)}
          </div>
          <div style="width: 100%;">
            <label class="input-label" style="font-size: 0.7rem; color: var(--muted); text-transform: uppercase; font-weight: 600;">Sent Campaign Message</label>
            <textarea readonly style="min-height:120px; font-size:0.78rem; margin-top:4px; background: rgba(0,0,0,0.25); border: 1px solid var(--border); border-radius: 4px; padding: 8px; color: #ccc; width: 100%; box-sizing: border-box; resize: vertical; font-family: inherit; line-height: 1.4;">${escapeHtml(c.draft || '')}</textarea>
          </div>
        `;
      } else if (c.status === "Discarded") {
        actionHtml = `
          <div style="color: var(--danger); font-size: 0.8rem; font-weight: 600;">
            Lead Discarded
          </div>
          <button class="btn btn-secondary btn-sm" style="margin-top:4px;" onclick="restoreLead('${escapeHtml(c.email)}', this)">
            Restore Lead
          </button>
        `;
      }
      
      card.innerHTML = `
        <div class="lead-row">
          <div class="lead-details">
            <div class="lead-name">${escapeHtml(c.name || 'Unknown')}</div>
            <div style="font-size:0.8rem; color:var(--muted); font-weight:600;">
              ${escapeHtml(c.title || 'Procurement Contact')}
            </div>
            <div style="display:flex; gap:8px; align-items:center; margin-top:4px;">
              <span class="lead-company-badge">${escapeHtml(c.company || 'Pharmaceutical Target')}</span>
              <span style="font-size:0.75rem; color:var(--muted);">${escapeHtml(c.city || '')}${c.city && c.country ? ', ' : ''}${escapeHtml(c.country || '')}</span>
            </div>
            <div style="font-size:0.75rem; color:var(--muted); font-family:monospace; margin-top:2px;">
              ${escapeHtml(c.email)}
            </div>
            <div style="margin-top:6px;">
              ${linkedinHtml}
            </div>
          </div>
          <div>
            <span class="status-badge-apollo ${statusClass}">${escapeHtml(c.status)}</span>
          </div>
        </div>
        <div class="lead-actions-container" style="border-top: 1px solid var(--border); padding-top:12px; margin-top:4px;">
          ${actionHtml}
        </div>
      `;
      return card;
    }

    async function generateLeadDraft(email, btn) {
      btn.disabled = true;
      const originalText = btn.innerHTML;
      btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> Generating...`;
      try {
        const res = await fetch("/api/apollo/generate-draft", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email })
        });
        if (res.ok) {
          loadApolloContacts();
        } else {
          const data = await res.json();
          alert("Error: " + (data.error || "Failed to generate draft"));
          btn.disabled = false;
          btn.innerHTML = originalText;
        }
      } catch (e) {
        alert("Network error occurred");
        btn.disabled = false;
        btn.innerHTML = originalText;
      }
    }

    async function discardLead(email, btn) {
      btn.disabled = true;
      try {
        const res = await fetch("/api/apollo/discard", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email })
        });
        if (res.ok) {
          loadApolloContacts();
        } else {
          alert("Failed to discard lead");
          btn.disabled = false;
        }
      } catch (e) {
        alert("Network error");
        btn.disabled = false;
      }
    }

    async function restoreLead(email, btn) {
      btn.disabled = true;
      try {
        const res = await fetch("/api/apollo/restore", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email })
        });
        if (res.ok) {
          loadApolloContacts();
        } else {
          alert("Failed to restore lead");
          btn.disabled = false;
        }
      } catch (e) {
        alert("Network error");
        btn.disabled = false;
      }
    }

    async function sendLeadEmail(email, btn) {
      const card = btn.closest(".apollo-lead-card");
      const subject = card.querySelector(".draft-subject").value;
      const body = card.querySelector(".draft-body").value;
      
      btn.disabled = true;
      const originalText = btn.innerHTML;
      btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> Sending...`;
      
      try {
        const res = await fetch("/api/apollo/send", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, subject, body })
        });
        if (res.ok) {
          loadApolloContacts();
        } else {
          const data = await res.json();
          alert("Error: " + (data.error || "Failed to send email"));
          btn.disabled = false;
          btn.innerHTML = originalText;
        }
      } catch (e) {
        alert("Network error occurred");
        btn.disabled = false;
        btn.innerHTML = originalText;
      }
    }

    fetchApolloBtn.addEventListener("click", async () => {
      const perPage = document.getElementById("apollo-per-page").value;
      const maxPages = document.getElementById("apollo-max-pages").value;
      const keyword = document.getElementById("apollo-keyword").value.trim();
      const rolesVal = document.getElementById("apollo-roles").value.trim();
      
      fetchApolloBtn.disabled = true;
      apolloFetchStatus.textContent = "⌛ Querying Apollo API & Enriching Leads...";
      apolloFetchStatus.className = "status-msg";
      apolloFetchStatus.style.display = "block";
      
      try {
        const res = await fetch("/api/apollo/fetch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ 
            per_page: parseInt(perPage), 
            max_pages: parseInt(maxPages),
            keyword: keyword,
            roles: rolesVal
          })
        });
        const data = await res.json();
        if (res.ok) {
          apolloFetchStatus.textContent = "✓ " + data.message;
          apolloFetchStatus.className = "status-msg success";
          loadApolloContacts();
        } else {
          apolloFetchStatus.textContent = "✗ " + (data.error || "Fetch failed");
          apolloFetchStatus.className = "status-msg error";
        }
      } catch (e) {
        apolloFetchStatus.textContent = "✗ Network error during Apollo fetch";
        apolloFetchStatus.className = "status-msg error";
      } finally {
        fetchApolloBtn.disabled = false;
        setTimeout(() => {
          apolloFetchStatus.style.display = "none";
        }, 5000);
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
                        "body": clean_email_body(item["email_data"].get("body", "")),
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

        if path == "/api/apollo/contacts":
            import apollo_agent
            contacts = apollo_agent.load_contacts()
            has_key = bool(apollo_agent.get_apollo_api_key().strip())
            self._send(200, {"contacts": contacts, "has_key": has_key})
            return

        if path == "/api/apollo/prompt":
            import apollo_agent
            prompt = apollo_agent.get_apollo_system_prompt()
            self._send(200, {"prompt": prompt})
            return

        self._send(404, {"error": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in (
            "/api/reply",
            "/api/cancel",
            "/api/marketing-prompt",
            "/api/manual-marketing",
            "/api/send-manual-marketing",
            "/api/apollo/fetch",
            "/api/apollo/generate-draft",
            "/api/apollo/send",
            "/api/apollo/discard",
            "/api/apollo/restore",
            "/api/apollo/prompt"
        ):
            self._send(404, {"error": "Not found"})
            return

        try:
            payload = self._read_json()
            item_id = payload.get("id")
            final_draft = payload.get("final_draft", "")
            description = payload.get("description", "")
        except Exception:
            self._send(400, {"error": "Invalid JSON"})
            return

        if path == "/api/manual-marketing":
            if not description:
                self._send(400, {"error": "Missing description"})
                return
            
            print(f"Generating manual marketing campaign for: {description[:100]}...")
            result = generate_campaign_from_text(description)
            # Just return the message for review, don't send yet
            if result and (getattr(result, 'is_marketing_product_document', False) or getattr(result, 'linkedin_message', '')):
                self._send(200, {"status": "ok", "message": result.linkedin_message})
            else:
                print("Failed to generate campaign or document not recognized as marketing.")
                self._send(400, {"error": "Failed to generate campaign or description not recognized as marketing product please give more information related to it"})
            return

        if path == "/api/send-manual-marketing":
            message = payload.get("message")
            profile_url = payload.get("profile_url")
            if not message:
                self._send(400, {"error": "Missing message"})
                return
            
            print(f"Sending manual campaign to LinkedIn ({profile_url or 'default profile'})...")
            success = send_dm.send_dm(message, profile_url=profile_url)
            if success:
                self._send(200, {"status": "ok"})
            else:
                self._send(500, {"error": "Message not send due to no connection with this account"})
            return

        if path == "/api/marketing-prompt":
            new_prompt = payload.get("prompt")
            if new_prompt is not None:
                set_marketing_system_prompt(new_prompt)
                self._send(200, {"status": "ok", "prompt": new_prompt})
            else:
                self._send(400, {"error": "Missing prompt field"})
            return

        if path == "/api/apollo/prompt":
            import apollo_agent
            new_prompt = payload.get("prompt")
            if new_prompt is not None:
                apollo_agent.set_apollo_system_prompt(new_prompt)
                print(f"[Apollo CRM Server] Custom Apollo System Prompt saved persistently.")
                self._send(200, {"status": "ok", "prompt": new_prompt})
            else:
                self._send(400, {"error": "Missing prompt field"})
            return

        if path == "/api/apollo/fetch":
            import apollo_agent
            per_page = payload.get("per_page", 25)
            max_pages = payload.get("max_pages", 1)
            keyword = payload.get("keyword", "pharmaceutical")
            roles_input = payload.get("roles", [])
            
            # clean up roles if it's a list or parse comma-separated values
            if isinstance(roles_input, str):
                roles = [r.strip() for r in roles_input.split(",") if r.strip()]
            elif isinstance(roles_input, list):
                roles = [str(r).strip() for r in roles_input if str(r).strip()]
            else:
                roles = None

            print(f"[Apollo CRM Server] Request to fetch and enrich leads. per_page={per_page}, max_pages={max_pages}, keyword='{keyword}', roles={roles}")
            result_msg = apollo_agent.fetch_apollo_leads(per_page=per_page, max_pages=max_pages, keyword=keyword, roles=roles)
            print(f"[Apollo CRM Server] Lead fetch completed. Outcome: {result_msg}")
            self._send(200, {"status": "ok", "message": result_msg})
            return

        if path == "/api/apollo/generate-draft":
            import apollo_agent
            from graph import Graph
            from config import CLIENT_ID, AUTHORITY, GRAPH_SCOPES
            
            email = payload.get("email")
            if not email:
                print("[Apollo CRM Server] ERROR: generate-draft missing email parameter")
                self._send(400, {"error": "Missing email"})
                return
            print(f"[Apollo CRM Server] Generating personalized OpenAI marketing draft for: {email}")
            res = apollo_agent.generate_campaign_draft(email)
            if "error" in res:
                print(f"[Apollo CRM Server] ERROR: Draft generation failed: {res['error']}")
                self._send(400, res)
                return
            
            # Immediately auto-send via Graph API
            subject = res.get("subject")
            body = res.get("body") or res.get("draft")
            
            try:
                print(f"[Apollo CRM Server] Auto-dispatching outbound Graph API email to {email}...")
                g = Graph(CLIENT_ID, AUTHORITY, GRAPH_SCOPES)
                success = g.send_new_email(email, subject, body)
                if success:
                    print(f"[Apollo CRM Server] Outbound email successfully sent to {email} and saved in Sent Items.")
                    # Update CRM state
                    contacts = apollo_agent.load_contacts()
                    contact = next((c for c in contacts if c["email"] == email), None)
                    if contact:
                        contact["status"] = "Sent"
                        contact["subject"] = subject
                        contact["draft"] = body
                        apollo_agent.save_contacts(contacts)
                    self._send(200, {"status": "success", "subject": subject, "draft": body})
                else:
                    self._send(200, {"status": "draft", "subject": subject, "draft": body, "error": "Microsoft Graph API returned failure."})
            except Exception as e:
                print(f"[Apollo CRM Server] EXCEPTION: Outbound Graph send error for {email}: {e}")
                self._send(200, {"status": "draft", "subject": subject, "draft": body, "error": str(e)})
            return

        if path == "/api/apollo/send":
            import apollo_agent
            from graph import Graph
            from config import CLIENT_ID, AUTHORITY, GRAPH_SCOPES
            
            email = payload.get("email")
            subject = payload.get("subject")
            body = payload.get("body")
            
            if not email or not subject or not body:
                print(f"[Apollo CRM Server] ERROR: send outbound missing fields. email={email}, subject={subject is not None}")
                self._send(400, {"error": "Missing email, subject, or body"})
                return
                
            try:
                print(f"[Apollo CRM Server] Dispatching outbound Graph API email to {email} (Subject: {subject[:50]}...)")
                g = Graph(CLIENT_ID, AUTHORITY, GRAPH_SCOPES)
                success = g.send_new_email(email, subject, body)
                if success:
                    print(f"[Apollo CRM Server] Outbound email successfully sent to {email} and saved in Sent Items.")
                    # Update CRM state
                    contacts = apollo_agent.load_contacts()
                    contact = next((c for c in contacts if c["email"] == email), None)
                    if contact:
                        contact["status"] = "Sent"
                        contact["subject"] = subject
                        contact["draft"] = body
                        apollo_agent.save_contacts(contacts)
                    self._send(200, {"status": "ok"})
                else:
                    print(f"[Apollo CRM Server] ERROR: Microsoft Graph API returned failure when sending email to {email}")
                    self._send(500, {"error": "Failed to send email via Graph API. Check server console/token cache."})
            except Exception as e:
                print(f"[Apollo CRM Server] EXCEPTION: Outbound Graph send error for {email}: {e}")
                self._send(500, {"error": f"Error sending email: {str(e)}"})
            return

        if path == "/api/apollo/discard":
            import apollo_agent
            email = payload.get("email")
            if not email:
                print("[Apollo CRM Server] ERROR: discard missing email parameter")
                self._send(400, {"error": "Missing email"})
                return
            print(f"[Apollo CRM Server] Discarding lead in database: {email}")
            contacts = apollo_agent.load_contacts()
            contact = next((c for c in contacts if c["email"] == email), None)
            if contact:
                contact["status"] = "Discarded"
                apollo_agent.save_contacts(contacts)
                print(f"[Apollo CRM Server] Lead {email} successfully marked as Discarded")
                self._send(200, {"status": "ok"})
            else:
                print(f"[Apollo CRM Server] ERROR: Lead not found for discard: {email}")
                self._send(404, {"error": "Lead not found"})
            return

        if path == "/api/apollo/restore":
            import apollo_agent
            email = payload.get("email")
            if not email:
                print("[Apollo CRM Server] ERROR: restore missing email parameter")
                self._send(400, {"error": "Missing email"})
                return
            print(f"[Apollo CRM Server] Restoring lead in database: {email}")
            contacts = apollo_agent.load_contacts()
            contact = next((c for c in contacts if c["email"] == email), None)
            if contact:
                contact["status"] = "Lead Fetched"
                contact["subject"] = None
                contact["draft"] = None
                apollo_agent.save_contacts(contacts)
                print(f"[Apollo CRM Server] Lead {email} successfully restored to Fetched status.")
                self._send(200, {"status": "ok"})
            else:
                print(f"[Apollo CRM Server] ERROR: Lead not found for restore: {email}")
                self._send(404, {"error": "Lead not found"})
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
