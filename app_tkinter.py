import os
import re
import json
import hashlib
import html
import threading
import time
from typing import Any
from pathlib import Path
from tkinter import (
    Tk,
    Frame,
    Label,
    Button,
    Entry,
    Text,
    Scrollbar,
    StringVar,
    IntVar,
    BooleanVar,
    END,
    BOTH,
    LEFT,
    RIGHT,
    Y,
    X,
    TOP,
    BOTTOM,
    filedialog,
    messagebox,
    ttk,
)

from dotenv import load_dotenv
from google import genai
from google.genai import types


load_dotenv()

APP_TITLE = "ChatMap Agent - Tkinter"
DEFAULT_MODEL = "gemini-2.5-flash"


# -----------------------------
# Cleaning
# -----------------------------

NOISE_LINE_PATTERNS = [
    r"^ChatGPT can make mistakes.*$",
    r"^Check important info.*$",
    r"^Copy$",
    r"^Copied$",
    r"^Share$",
    r"^Regenerate$",
    r"^Retry$",
    r"^Good response$",
    r"^Bad response$",
    r"^Read aloud$",
    r"^Search$",
    r"^Reasoned for.*$",
    r"^スポンサー.*$",
    r"^Sponsored.*$",
    r"^広告.*$",
    r"^Open in.*$",
    r"^Download.*$",
]

SPEAKER_REPLACEMENTS = [
    (r"^\s*You said:\s*$", "[User]"),
    (r"^\s*You:\s*$", "[User]"),
    (r"^\s*User:\s*$", "[User]"),
    (r"^\s*あなた:\s*$", "[User]"),
    (r"^\s*ChatGPT said:\s*$", "[Assistant]"),
    (r"^\s*ChatGPT:\s*$", "[Assistant]"),
    (r"^\s*Assistant:\s*$", "[Assistant]"),
    (r"^\s*アシスタント:\s*$", "[Assistant]"),
]


def normalize_speaker_lines(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        replaced = None

        for pattern, replacement in SPEAKER_REPLACEMENTS:
            if re.match(pattern, stripped, flags=re.IGNORECASE):
                replaced = replacement
                break

        lines.append(replaced if replaced else line)

    return "\n".join(lines)


def remove_noise_lines(text: str) -> str:
    kept = []
    for line in text.splitlines():
        stripped = line.strip()

        if not stripped:
            kept.append("")
            continue

        is_noise = any(re.match(pattern, stripped, flags=re.IGNORECASE) for pattern in NOISE_LINE_PATTERNS)
        if is_noise:
            continue

        # ChatGPTのWeb UI由来っぽい内部参照や余計なID
        if re.search(r"turn\d+(search|view|file|image|product|news)\d+", stripped):
            continue

        kept.append(line)

    return "\n".join(kept)


def compress_code_blocks(text: str, keep_short_code: bool = True, max_code_lines: int = 30) -> str:
    pattern = re.compile(r"```(\w+)?\n(.*?)```", flags=re.DOTALL)

    def repl(match: re.Match) -> str:
        lang = match.group(1) or "text"
        code = match.group(2)
        lines = code.splitlines()

        if keep_short_code and len(lines) <= max_code_lines:
            return match.group(0)

        digest = hashlib.md5(code.encode("utf-8")).hexdigest()[:8]
        return f"[コードブロック: {lang}, {len(lines)}行, hash={digest}]"

    return pattern.sub(repl, text)


def shrink_urls(text: str) -> str:
    return re.sub(r"https?://\S+", "[URL]", text)


def normalize_blank_lines(text: str) -> str:
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_chat_log(
    raw_text: str,
    remove_ui_noise: bool = True,
    replace_urls: bool = True,
    compress_code: bool = True,
) -> str:
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    text = normalize_speaker_lines(text)

    if remove_ui_noise:
        text = remove_noise_lines(text)

    if compress_code:
        text = compress_code_blocks(text)

    if replace_urls:
        text = shrink_urls(text)

    return normalize_blank_lines(text)


# -----------------------------
# Chunking
# -----------------------------

def split_by_speaker_blocks(text: str) -> list[str]:
    if "[User]" not in text and "[Assistant]" not in text:
        return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    parts = re.split(r"(?=^\[(?:User|Assistant)\])", text, flags=re.MULTILINE)
    return [p.strip() for p in parts if p.strip()]


def chunk_text(text: str, max_chars: int = 6000) -> list[str]:
    blocks = split_by_speaker_blocks(text)
    chunks = []
    current = ""

    for block in blocks:
        if len(block) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""

            for i in range(0, len(block), max_chars):
                chunks.append(block[i:i + max_chars].strip())
            continue

        if len(current) + len(block) + 2 <= max_chars:
            current = f"{current}\n\n{block}".strip()
        else:
            if current:
                chunks.append(current.strip())
            current = block

    if current:
        chunks.append(current.strip())

    return chunks


# -----------------------------
# Gemini
# -----------------------------

def get_client(api_key: str) -> genai.Client:
    api_key = api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Gemini APIキーが未設定です。.env または画面上部の入力欄にAPIキーを入れてください。")
    return genai.Client(api_key=api_key)


def extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    return json.loads(text)


def is_retryable_gemini_error(error: Exception) -> bool:
    message = str(error).lower()
    retryable_markers = [
        "503",
        "unavailable",
        "high demand",
        "resource exhausted",
        "rate limit",
        "429",
        "temporarily",
    ]
    return any(marker in message for marker in retryable_markers)


def format_gemini_error(error: Exception) -> str:
    message = str(error)
    if is_retryable_gemini_error(error):
        return (
            "Gemini API is temporarily unavailable or under high demand.\n\n"
            "The app retried automatically, but the request still failed. "
            "Please wait a little and run it again. If this keeps happening, "
            "try a different Gemini model in the Model field.\n\n"
            f"Original error:\n{message}"
        )
    return message

def generate_json_content(client: genai.Client, model: str, prompt: str, max_retries: int = 4):
    delay_seconds = 3
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            return client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
        except Exception as error:
            last_error = error
            if attempt >= max_retries or not is_retryable_gemini_error(error):
                raise RuntimeError(format_gemini_error(error)) from error
            time.sleep(delay_seconds)
            delay_seconds = min(delay_seconds * 2, 30)

    raise RuntimeError(format_gemini_error(last_error))


def summarize_chunk(chunk: str, chunk_index: int, total_chunks: int, model: str, api_key: str) -> dict:
    client = get_client(api_key)

    prompt = f"""
あなたは会話ログ整理エンジンです。
以下は長い会話ログの一部です。話の流れを追うために、このチャンクを構造化してください。

重要:
- 会話の流れ、転換点、決まったこと、未解決の疑問を優先する
- 細かい雑談やUI由来のノイズは無視する
- コード全文やURLそのものではなく、その役割を要約する
- JSONのみを返す

チャンク番号: {chunk_index}/{total_chunks}

返すJSON形式:
{{
  "chunk_title": "このチャンクの短いタイトル",
  "summary": "このチャンクの要約",
  "main_topics": ["主要トピック1", "主要トピック2"],
  "decisions": ["決まったこと1"],
  "open_questions": ["未解決の疑問1"],
  "turning_points": ["話が変わった/方針変更したポイント"],
  "candidate_nodes": [
    {{"label": "短いノード名", "description": "何の話か"}}
  ]
}}

会話ログ:
{chunk}
"""

    response = generate_json_content(client, model, prompt)

    return extract_json(response.text)


def build_global_map(chunk_summaries: list[dict], model: str, api_key: str) -> dict:
    client = get_client(api_key)
    summaries_json = json.dumps(chunk_summaries, ensure_ascii=False, indent=2)

    prompt = f"""
あなたは会話ログ全体をグラフ化するAIです。
以下は、長い会話ログをチャンクごとに要約したものです。
これらを統合し、会話全体の「話題の流れ」をノードとエッジで表現してください。

重要:
- ノードは最大12個
- ノードは会話の大きな話題・方針転換・決定事項を表す
- エッジは時間順または話題遷移を表す
- 小さすぎる話題は統合する
- 迷走や詰まりも重要な転換点として表現する
- JSONのみを返す

返すJSON形式:
{{
  "title": "会話全体のタイトル",
  "summary": "会話全体の要約",
  "timeline": [
    "時系列の流れ1",
    "時系列の流れ2"
  ],
  "decisions": ["決まったこと1", "決まったこと2"],
  "open_questions": ["未解決の疑問1", "未解決の疑問2"],
  "next_actions": ["次にやること1", "次にやること2", "次にやること3"],
  "nodes": [
    {{"id": "A", "label": "短いノード名", "description": "説明"}}
  ],
  "edges": [
    {{"from": "A", "to": "B", "label": "遷移理由"}}
  ]
}}

チャンク要約:
{summaries_json}
"""

    response = generate_json_content(client, model, prompt)

    return extract_json(response.text)


# -----------------------------
# Rendering
# -----------------------------

def safe_mermaid_label(label: str) -> str:
    label = str(label)
    label = label.replace('"', "'")
    label = label.replace("\n", " ")
    label = re.sub(r"\s+", " ", label)
    return label[:48]


def normalize_mermaid_id(value: str, fallback_index: int, used_ids: set[str]) -> str:
    node_id = re.sub(r"\W+", "_", str(value or "")).strip("_")
    if not node_id or not re.match(r"^[A-Za-z]", node_id):
        node_id = f"N{fallback_index}"

    base_id = node_id
    suffix = 2
    while node_id in used_ids:
        node_id = f"{base_id}_{suffix}"
        suffix += 1

    used_ids.add(node_id)
    return node_id


def build_mermaid_graph(data: dict, include_clicks: bool = False) -> tuple[str, list[dict], list[dict]]:
    lines = ["graph TD"]
    node_id_map = {}
    used_ids = set()
    node_details = []

    for index, node in enumerate(data.get("nodes", []), start=1):
        original_id = str(node.get("id", f"N{index}"))
        node_id = normalize_mermaid_id(original_id, index, used_ids)
        node_id_map[original_id] = node_id
        label = safe_mermaid_label(node.get("label", original_id))
        lines.append(f'  {node_id}["{label}"]')
        node_details.append(
            {
                "id": original_id,
                "graph_id": node_id,
                "label": str(node.get("label", original_id)),
                "description": str(node.get("description", "")),
            }
        )

    edge_details = []
    for index, edge in enumerate(data.get("edges", []), start=1):
        raw_src = str(edge.get("from", ""))
        raw_dst = str(edge.get("to", ""))
        src = node_id_map.get(raw_src) or normalize_mermaid_id(raw_src or f"Edge{index}From", len(used_ids) + 1, used_ids)
        dst = node_id_map.get(raw_dst) or normalize_mermaid_id(raw_dst or f"Edge{index}To", len(used_ids) + 1, used_ids)
        label = safe_mermaid_label(edge.get("label", ""))

        edge_details.append(
            {
                "from": raw_src,
                "to": raw_dst,
                "from_graph_id": src,
                "to_graph_id": dst,
                "label": str(edge.get("label", "")),
            }
        )

        if label:
            lines.append(f'  {src} -->|"{label}"| {dst}')
        else:
            lines.append(f"  {src} --> {dst}")

    if include_clicks and node_details:
        lines.append("")
        for node in node_details:
            graph_id = node["graph_id"]
            lines.append(f'  click {graph_id} call selectNode("{graph_id}") "Details"')

    return "\n".join(lines), node_details, edge_details


def to_mermaid(data: dict) -> str:
    mermaid, _, _ = build_mermaid_graph(data)
    return mermaid


def as_text_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def make_chatmap_view_data(data: dict, node_details: list[dict], edge_details: list[dict]) -> dict:
    node_by_graph_id = {node["graph_id"]: node for node in node_details}
    items = []

    for node in node_details:
        related_edges = [
            edge for edge in edge_details
            if edge["from_graph_id"] == node["graph_id"] or edge["to_graph_id"] == node["graph_id"]
        ]
        connections = []
        for edge in related_edges:
            direction = "out" if edge["from_graph_id"] == node["graph_id"] else "in"
            other_graph_id = edge["to_graph_id"] if direction == "out" else edge["from_graph_id"]
            other_node = node_by_graph_id.get(other_graph_id, {})
            connections.append(
                {
                    "direction": direction,
                    "label": edge["label"],
                    "target": other_node.get("label") or edge["to" if direction == "out" else "from"],
                    "targetGraphId": other_graph_id,
                }
            )

        items.append(
            {
                "id": node["graph_id"],
                "type": "node",
                "typeLabel": "Node",
                "title": node["label"],
                "body": node["description"] or "No description.",
                "connections": connections,
            }
        )

    section_defs = [
        ("timeline", "Timeline", data.get("timeline", [])),
        ("decision", "Decision", data.get("decisions", [])),
        ("open", "Open Question", data.get("open_questions", [])),
        ("action", "Next Action", data.get("next_actions", [])),
    ]
    for section_type, label, values in section_defs:
        for index, value in enumerate(as_text_list(values), start=1):
            items.append(
                {
                    "id": f"{section_type}-{index}",
                    "type": section_type,
                    "typeLabel": label,
                    "title": value[:80],
                    "body": value,
                    "connections": [],
                }
            )

    return {
        "title": data.get("title", "ChatMap Viewer"),
        "summary": data.get("summary", ""),
        "metrics": {
            "nodes": len(node_details),
            "edges": len(edge_details),
            "decisions": len(as_text_list(data.get("decisions", []))),
            "openQuestions": len(as_text_list(data.get("open_questions", []))),
            "nextActions": len(as_text_list(data.get("next_actions", []))),
        },
        "items": items,
        "sections": {
            "timeline": as_text_list(data.get("timeline", [])),
            "decisions": as_text_list(data.get("decisions", [])),
            "openQuestions": as_text_list(data.get("open_questions", [])),
            "nextActions": as_text_list(data.get("next_actions", [])),
        },
    }


def make_mermaid_html(data: dict, mermaid: str) -> str:
    interactive_mermaid, node_details, edge_details = build_mermaid_graph(data, include_clicks=True)
    view_data = make_chatmap_view_data(data, node_details, edge_details)
    title = html.escape(view_data["title"])
    graph = html.escape(interactive_mermaid)
    data_json = json.dumps(view_data, ensure_ascii=False).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f6f2;
      --panel: #ffffff;
      --ink: #1f2933;
      --muted: #667085;
      --line: #d9ded8;
      --accent: #0f766e;
      --accent-soft: #d8f3ee;
      --warn: #b45309;
      --warn-soft: #fff0d3;
      --focus: #2563eb;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    header {{
      padding: 18px 24px 14px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }}
    h1 {{ margin: 0; font-size: 22px; font-weight: 680; }}
    .summary {{ margin: 8px 0 0; max-width: 1100px; color: var(--muted); line-height: 1.6; }}
    .metrics {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }}
    .metric {{ border: 1px solid var(--line); border-radius: 999px; padding: 6px 10px; background: #fbfcfb; font-size: 13px; }}
    .layout {{ display: grid; grid-template-columns: minmax(520px, 1.35fr) minmax(340px, 0.65fr); gap: 14px; padding: 14px; min-height: calc(100vh - 156px); }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; min-width: 0; }}
    .graph-panel {{ overflow: auto; }}
    .graph-toolbar {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 12px 14px; border-bottom: 1px solid var(--line); }}
    .graph-title {{ font-size: 14px; font-weight: 650; }}
    .hint {{ color: var(--muted); font-size: 12px; }}
    .mermaid {{ min-width: 900px; padding: 22px; margin: 0; }}
    .side {{ display: grid; grid-template-rows: auto minmax(220px, 1fr) auto; overflow: hidden; }}
    .controls {{ display: grid; grid-template-columns: 1fr 150px; gap: 8px; padding: 12px; border-bottom: 1px solid var(--line); }}
    input, select {{ width: 100%; border: 1px solid var(--line); border-radius: 6px; padding: 9px 10px; font: inherit; background: #fff; color: var(--ink); }}
    input:focus, select:focus {{ outline: 2px solid var(--focus); outline-offset: 1px; }}
    .item-list {{ overflow: auto; padding: 8px; }}
    .item {{ width: 100%; text-align: left; border: 1px solid transparent; border-radius: 7px; padding: 10px; background: transparent; cursor: pointer; color: var(--ink); }}
    .item + .item {{ margin-top: 4px; }}
    .item:hover {{ background: #f4f7f6; }}
    .item.active {{ background: var(--accent-soft); border-color: #9edbd2; }}
    .tag {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 2px 7px; font-size: 11px; font-weight: 650; background: #eef2f7; color: #475467; }}
    .tag.node {{ background: var(--accent-soft); color: #0f766e; }}
    .tag.open {{ background: var(--warn-soft); color: var(--warn); }}
    .item-title {{ display: block; margin-top: 6px; font-size: 14px; line-height: 1.35; }}
    .detail {{ border-top: 1px solid var(--line); padding: 14px; max-height: 300px; overflow: auto; background: #fbfcfb; }}
    .detail h2 {{ margin: 0 0 8px; font-size: 17px; }}
    .detail p {{ margin: 0; line-height: 1.55; white-space: pre-wrap; }}
    .connections {{ margin-top: 12px; display: grid; gap: 6px; }}
    .connection {{ border-left: 3px solid var(--accent); padding-left: 8px; color: var(--muted); font-size: 13px; line-height: 1.4; }}
    .sections {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; padding: 0 14px 14px; }}
    .section {{ padding: 14px; }}
    .section h2 {{ margin: 0 0 10px; font-size: 15px; }}
    .section ul {{ margin: 0; padding-left: 18px; color: var(--muted); line-height: 1.5; }}
    .section li + li {{ margin-top: 7px; }}
    .empty {{ color: var(--muted); font-size: 13px; }}
    @media (max-width: 980px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .sections {{ grid-template-columns: 1fr 1fr; }}
      .mermaid {{ min-width: 760px; }}
    }}
    @media (max-width: 640px) {{
      header {{ padding: 16px; }}
      .layout {{ padding: 10px; }}
      .sections {{ grid-template-columns: 1fr; padding: 0 10px 10px; }}
      .controls {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <p class="summary" id="summary"></p>
    <div class="metrics" id="metrics"></div>
  </header>
  <main class="layout">
    <section class="panel graph-panel">
      <div class="graph-toolbar">
        <div class="graph-title">ChatMap Graph</div>
        <div class="hint">\u30ce\u30fc\u30c9\u3092\u30af\u30ea\u30c3\u30af\u3059\u308b\u3068\u8a73\u7d30\u3092\u8868\u793a</div>
      </div>
      <pre class="mermaid">{graph}</pre>
    </section>
    <aside class="panel side">
      <div class="controls">
        <input id="search" type="search" placeholder="\u691c\u7d22">
        <select id="typeFilter" aria-label="filter">
          <option value="all">All</option>
          <option value="node">Nodes</option>
          <option value="decision">Decisions</option>
          <option value="open">Open Questions</option>
          <option value="action">Next Actions</option>
          <option value="timeline">Timeline</option>
        </select>
      </div>
      <div class="item-list" id="itemList"></div>
      <div class="detail" id="detail"></div>
    </aside>
  </main>
  <section class="sections" id="sections"></section>
  <script id="chatmap-data" type="application/json">{data_json}</script>
  <script>
    const chatmap = JSON.parse(document.getElementById("chatmap-data").textContent);
    const byId = new Map(chatmap.items.map(item => [item.id, item]));
    let selectedId = chatmap.items[0]?.id || null;

    function escapeHtml(value) {{
      return String(value ?? "").replace(/[&<>"']/g, char => ({{
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }}[char]));
    }}

    function renderMetrics() {{
      const labels = [
        ["Nodes", chatmap.metrics.nodes],
        ["Edges", chatmap.metrics.edges],
        ["Decisions", chatmap.metrics.decisions],
        ["Open", chatmap.metrics.openQuestions],
        ["Actions", chatmap.metrics.nextActions],
      ];
      document.getElementById("summary").textContent = chatmap.summary || "No summary.";
      document.getElementById("metrics").innerHTML = labels
        .map(([label, value]) => `<span class="metric">${{label}}: ${{value}}</span>`)
        .join("");
    }}

    function matches(item, query, type) {{
      const text = `${{item.title}} ${{item.body}} ${{item.typeLabel}}`.toLowerCase();
      const typeMatch = type === "all" || item.type === type;
      return typeMatch && text.includes(query);
    }}

    function renderList() {{
      const query = document.getElementById("search").value.trim().toLowerCase();
      const type = document.getElementById("typeFilter").value;
      const visible = chatmap.items.filter(item => matches(item, query, type));
      if (!visible.some(item => item.id === selectedId)) selectedId = visible[0]?.id || null;
      document.getElementById("itemList").innerHTML = visible.length
        ? visible.map(item => `
          <button class="item ${{item.id === selectedId ? "active" : ""}}" data-id="${{escapeHtml(item.id)}}">
            <span class="tag ${{escapeHtml(item.type)}}">${{escapeHtml(item.typeLabel)}}</span>
            <span class="item-title">${{escapeHtml(item.title)}}</span>
          </button>`).join("")
        : `<div class="empty">No matches.</div>`;
      renderDetail();
    }}

    function renderDetail() {{
      const item = byId.get(selectedId);
      const detail = document.getElementById("detail");
      if (!item) {{
        detail.innerHTML = `<div class="empty">Select an item.</div>`;
        return;
      }}
      const connections = item.connections?.length
        ? `<div class="connections">${{item.connections.map(conn => `
            <div class="connection">
              ${{conn.direction === "out" ? "To" : "From"}}: ${{escapeHtml(conn.target || "Unknown")}}
              ${{conn.label ? ` / ${{escapeHtml(conn.label)}}` : ""}}
            </div>`).join("")}}</div>`
        : "";
      detail.innerHTML = `
        <span class="tag ${{escapeHtml(item.type)}}">${{escapeHtml(item.typeLabel)}}</span>
        <h2>${{escapeHtml(item.title)}}</h2>
        <p>${{escapeHtml(item.body)}}</p>
        ${{connections}}
      `;
    }}

    function renderSections() {{
      const sectionDefs = [
        ["Timeline", chatmap.sections.timeline],
        ["Decisions", chatmap.sections.decisions],
        ["Open Questions", chatmap.sections.openQuestions],
        ["Next Actions", chatmap.sections.nextActions],
      ];
      document.getElementById("sections").innerHTML = sectionDefs.map(([title, values]) => `
        <section class="panel section">
          <h2>${{escapeHtml(title)}}</h2>
          ${{values.length ? `<ul>${{values.map(value => `<li>${{escapeHtml(value)}}</li>`).join("")}}</ul>` : `<div class="empty">None</div>`}}
        </section>
      `).join("");
    }}

    document.getElementById("itemList").addEventListener("click", event => {{
      const button = event.target.closest(".item");
      if (!button) return;
      selectedId = button.dataset.id;
      renderList();
    }});
    document.getElementById("search").addEventListener("input", renderList);
    document.getElementById("typeFilter").addEventListener("change", renderList);

    window.selectNode = function(graphId) {{
      selectedId = graphId;
      document.getElementById("typeFilter").value = "all";
      document.getElementById("search").value = "";
      renderList();
      document.getElementById("detail").scrollIntoView({{ block: "nearest" }});
    }};

    renderMetrics();
    renderSections();
    renderList();
  </script>
  <script type="module">
    import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";
    mermaid.initialize({{ startOnLoad: true, securityLevel: "loose", theme: "default" }});
  </script>
</body>
</html>
"""


def make_markdown_report(data: dict, mermaid: str) -> str:
    lines = []
    lines.append(f"# {data.get('title', 'ChatMap Report')}\n")
    lines.append("## 要約\n")
    lines.append(data.get("summary", "") + "\n")

    def section(title: str, items: list[str]):
        lines.append(f"## {title}\n")
        if not items:
            lines.append("- なし\n")
        else:
            for item in items:
                lines.append(f"- {item}")
            lines.append("")

    section("時系列", data.get("timeline", []))
    section("決まったこと", data.get("decisions", []))
    section("未解決の疑問", data.get("open_questions", []))
    section("次にやること", data.get("next_actions", []))

    lines.append("## Mermaid\n")
    lines.append("```mermaid")
    lines.append(mermaid)
    lines.append("```")

    return "\n".join(lines)


# -----------------------------
# Tkinter helpers
# -----------------------------

class ScrolledTextBox(Frame):
    def __init__(self, master, height=10, width=60, **kwargs):
        super().__init__(master)
        self.text = Text(self, height=height, width=width, wrap="word", **kwargs)
        self.scrollbar = Scrollbar(self, command=self.text.yview)
        self.text.configure(yscrollcommand=self.scrollbar.set)
        self.text.pack(side=LEFT, fill=BOTH, expand=True)
        self.scrollbar.pack(side=RIGHT, fill=Y)

    def get_text(self) -> str:
        return self.text.get("1.0", END).strip()

    def set_text(self, value: str):
        self.text.delete("1.0", END)
        self.text.insert("1.0", value)

    def clear(self):
        self.text.delete("1.0", END)


class ChatMapTkApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1200x780")

        self.api_key_var = StringVar(value=os.getenv("GEMINI_API_KEY", ""))
        self.model_var = StringVar(value=DEFAULT_MODEL)
        self.max_chars_var = IntVar(value=6000)

        self.remove_ui_noise_var = BooleanVar(value=True)
        self.replace_urls_var = BooleanVar(value=True)
        self.compress_code_var = BooleanVar(value=True)

        self.global_map = None
        self.chunk_summaries = None
        self.mermaid = ""
        self.report_md = ""
        self.mermaid_html = ""

        self._build_ui()

    def _build_ui(self):
        top = Frame(self.root, padx=8, pady=8)
        top.pack(side=TOP, fill=X)

        Label(top, text="Gemini API Key").pack(side=LEFT)
        Entry(top, textvariable=self.api_key_var, width=42, show="*").pack(side=LEFT, padx=6)

        Label(top, text="Model").pack(side=LEFT, padx=(12, 0))
        Entry(top, textvariable=self.model_var, width=22).pack(side=LEFT, padx=6)

        Label(top, text="Chunk chars").pack(side=LEFT, padx=(12, 0))
        Entry(top, textvariable=self.max_chars_var, width=8).pack(side=LEFT, padx=6)

        Button(top, text="ログファイルを開く", command=self.open_log_file).pack(side=LEFT, padx=(12, 4))
        Button(top, text="解析する", command=self.start_analysis).pack(side=LEFT, padx=4)
        Button(top, text="Markdown保存", command=self.save_markdown).pack(side=LEFT, padx=4)
        Button(top, text="ChatMap HTML保存", command=self.save_mermaid_html).pack(side=LEFT, padx=4)

        options = Frame(self.root, padx=8, pady=2)
        options.pack(side=TOP, fill=X)

        ttk.Checkbutton(options, text="UI文言を削除", variable=self.remove_ui_noise_var).pack(side=LEFT)
        ttk.Checkbutton(options, text="URLを短縮", variable=self.replace_urls_var).pack(side=LEFT, padx=12)
        ttk.Checkbutton(options, text="長いコードを圧縮", variable=self.compress_code_var).pack(side=LEFT)

        self.status_var = StringVar(value="会話ログを貼って「解析する」を押してください。")
        Label(self.root, textvariable=self.status_var, anchor="w", padx=8).pack(side=TOP, fill=X)

        main = ttk.PanedWindow(self.root, orient="horizontal")
        main.pack(fill=BOTH, expand=True, padx=8, pady=8)

        left = Frame(main)
        right = Frame(main)

        main.add(left, weight=1)
        main.add(right, weight=1)

        Label(left, text="Raw conversation log").pack(anchor="w")
        self.raw_box = ScrolledTextBox(left, height=26)
        self.raw_box.pack(fill=BOTH, expand=True)

        preview_frame = Frame(left)
        preview_frame.pack(fill=BOTH, expand=True, pady=(8, 0))

        Label(preview_frame, text="Cleaned preview").pack(anchor="w")
        self.cleaned_box = ScrolledTextBox(preview_frame, height=12)
        self.cleaned_box.pack(fill=BOTH, expand=True)

        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill=BOTH, expand=True)

        self.summary_tab = Frame(self.notebook)
        self.mermaid_tab = Frame(self.notebook)
        self.json_tab = Frame(self.notebook)
        self.chunks_tab = Frame(self.notebook)

        self.notebook.add(self.summary_tab, text="概要")
        self.notebook.add(self.mermaid_tab, text="Mermaid")
        self.notebook.add(self.json_tab, text="JSON")
        self.notebook.add(self.chunks_tab, text="Chunks")

        self.summary_box = ScrolledTextBox(self.summary_tab)
        self.summary_box.pack(fill=BOTH, expand=True)

        self.mermaid_box = ScrolledTextBox(self.mermaid_tab)
        self.mermaid_box.pack(fill=BOTH, expand=True)

        self.json_box = ScrolledTextBox(self.json_tab)
        self.json_box.pack(fill=BOTH, expand=True)

        self.chunks_box = ScrolledTextBox(self.chunks_tab)
        self.chunks_box.pack(fill=BOTH, expand=True)

    def open_log_file(self):
        path = filedialog.askopenfilename(
            title="会話ログを開く",
            filetypes=[
                ("Text files", "*.txt"),
                ("Markdown files", "*.md"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = Path(path).read_text(encoding="cp932", errors="ignore")
        self.raw_box.set_text(text)
        self.preview_cleaned()

    def preview_cleaned(self):
        raw = self.raw_box.get_text()
        cleaned = clean_chat_log(
            raw,
            remove_ui_noise=self.remove_ui_noise_var.get(),
            replace_urls=self.replace_urls_var.get(),
            compress_code=self.compress_code_var.get(),
        )
        chunks = chunk_text(cleaned, max_chars=int(self.max_chars_var.get()))
        self.cleaned_box.set_text(cleaned)
        self.chunks_box.set_text("\n\n".join(
            [f"--- Chunk {i + 1}/{len(chunks)} ({len(c)} chars) ---\n{c[:2500]}{'...' if len(c) > 2500 else ''}" for i, c in enumerate(chunks)]
        ))
        self.status_var.set(f"Raw: {len(raw)} chars / Cleaned: {len(cleaned)} chars / Chunks: {len(chunks)}")

    def start_analysis(self):
        raw = self.raw_box.get_text()
        if not raw:
            messagebox.showwarning("入力なし", "会話ログを貼ってください。")
            return

        self.preview_cleaned()

        api_key = self.api_key_var.get().strip()
        if not api_key and not os.getenv("GEMINI_API_KEY"):
            messagebox.showwarning("APIキーなし", "Gemini APIキーを入力するか、.env に GEMINI_API_KEY を設定してください。")
            return

        thread = threading.Thread(target=self._run_analysis_worker, daemon=True)
        thread.start()

    def _set_status(self, value: str):
        self.root.after(0, lambda: self.status_var.set(value))

    def _set_text_async(self, box: ScrolledTextBox, value: str):
        self.root.after(0, lambda: box.set_text(value))

    def _run_analysis_worker(self):
        try:
            raw = self.raw_box.get_text()
            api_key = self.api_key_var.get().strip()
            model = self.model_var.get().strip() or DEFAULT_MODEL
            max_chars = int(self.max_chars_var.get())

            cleaned = clean_chat_log(
                raw,
                remove_ui_noise=self.remove_ui_noise_var.get(),
                replace_urls=self.replace_urls_var.get(),
                compress_code=self.compress_code_var.get(),
            )
            chunks = chunk_text(cleaned, max_chars=max_chars)

            if not chunks:
                raise RuntimeError("チャンクが空です。入力ログを確認してください。")

            self._set_status("解析中: チャンクごとにGeminiへ送信しています...")
            chunk_summaries = []

            for i, chunk in enumerate(chunks, start=1):
                self._set_status(f"解析中: Chunk {i}/{len(chunks)} を要約しています...")
                summary = summarize_chunk(chunk, i, len(chunks), model=model, api_key=api_key)
                chunk_summaries.append(summary)

            self._set_status("解析中: 全体マップを生成しています...")
            global_map = build_global_map(chunk_summaries, model=model, api_key=api_key)
            mermaid = to_mermaid(global_map)
            report_md = make_markdown_report(global_map, mermaid)
            mermaid_html = make_mermaid_html(global_map, mermaid)

            self.global_map = global_map
            self.chunk_summaries = chunk_summaries
            self.mermaid = mermaid
            self.report_md = report_md
            self.mermaid_html = mermaid_html

            summary_text = self.make_summary_text(global_map)
            json_text = json.dumps(
                {
                    "global_map": global_map,
                    "chunk_summaries": chunk_summaries,
                },
                ensure_ascii=False,
                indent=2,
            )

            self._set_text_async(self.summary_box, summary_text)
            self._set_text_async(self.mermaid_box, mermaid)
            self._set_text_async(self.json_box, json_text)
            self._set_status("完了: 結果を表示しました。Markdown保存とChatMap HTML保存ができます。")

        except Exception as e:
            err_msg = str(e)
            self._set_status("エラーが発生しました。")
            self.root.after(0, lambda msg=err_msg: messagebox.showerror("エラー", msg))

    def make_summary_text(self, data: dict) -> str:
        lines = []
        lines.append(f"タイトル: {data.get('title', '')}")
        lines.append("")
        lines.append("要約:")
        lines.append(data.get("summary", ""))
        lines.append("")

        def add_section(title: str, items: list[str]):
            lines.append(title + ":")
            if not items:
                lines.append("- なし")
            else:
                for item in items:
                    lines.append(f"- {item}")
            lines.append("")

        add_section("時系列", data.get("timeline", []))
        add_section("決まったこと", data.get("decisions", []))
        add_section("未解決の疑問", data.get("open_questions", []))
        add_section("次にやること", data.get("next_actions", []))

        return "\n".join(lines)

    def save_markdown(self):
        if not self.report_md:
            messagebox.showinfo("保存できません", "先に解析を実行してください。")
            return

        path = filedialog.asksaveasfilename(
            title="Markdownレポートを保存",
            defaultextension=".md",
            filetypes=[
                ("Markdown files", "*.md"),
                ("Text files", "*.txt"),
                ("All files", "*.*"),
            ],
            initialfile="chatmap_report.md",
        )
        if not path:
            return

        Path(path).write_text(self.report_md, encoding="utf-8")
        messagebox.showinfo("保存しました", f"保存しました:\n{path}")


    def save_mermaid_html(self):
        if not self.mermaid_html:
            messagebox.showinfo("保存できません", "先に解析を実行してください。")
            return

        path = filedialog.asksaveasfilename(
            title="ChatMap HTMLを保存",
            defaultextension=".html",
            filetypes=[
                ("HTML files", "*.html"),
                ("All files", "*.*"),
            ],
            initialfile="chatmap_viewer.html",
        )
        if not path:
            return

        Path(path).write_text(self.mermaid_html, encoding="utf-8")
        if messagebox.askyesno("保存しました", f"保存しました:\n{path}\n\nブラウザで開きますか？"):
            os.startfile(path)


def main():
    root = Tk()
    app = ChatMapTkApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
