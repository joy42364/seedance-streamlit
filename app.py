"""Seedance 2.0 video generation UI — MyElcDesign redesign. 

Prompt-first composer at the top, masonry/feed-style gallery below.
Dark tech aesthetic, Streamlit-native components + light CSS injection.

Local files are exposed to the Seedance API through a Cloudflare Quick Tunnel
(see tunnel.py). Generated videos are archived to ./videos/{task_id}.mp4 so
they survive Ark's 24h URL expiry.
"""

from __future__ import annotations

import html
import json
import os
import re
import threading
import time
import urllib.request
from io import BytesIO
from pathlib import Path
from typing import Any

import av
import streamlit as st
from volcenginesdkarkruntime import Ark

import tunnel

# 平台在售视频模型（dev 库 cloud_llm_model PUBLISHED，透传给上游原样使用）
MODELS: dict[str, str] = {
    "2.5": "doubao-seedance-2.5",
    "2.0": "doubao-seedance-2.0",
}
MODEL_DESC = {
    "2.5": ("平台主推", "~60s", "最新版，时长最大 30 秒"),
    "2.0": ("多模态", "~90s", "支持图/视频/音频参考输入"),
}
RATIOS = ["16:9", "9:16", "1:1", "21:9", "4:3", "3:4"]
# 4k 为预置档（260922）：上游暂未开放，选了会被上游拒；档名以平台模型配置为准（大小写敏感）
RESOLUTION_CHOICES = ["(服务端默认)", "1080p", "720p", "480p", "4k"]
# 平台网关透传基址：{网关}/cloud/video/api + 上游 /api/v3（所以有两段 api）。
# 本地联调网关默认 8080；dev/test/prod 网关地址由运维提供，页面侧边栏可改。
DEFAULT_BASE_URL = "http://localhost:8080/cloud/video/api/api/v3"
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
BASE_DIR = Path(__file__).resolve().parent
HISTORY_FILE = BASE_DIR / "tasks_history.json"
API_KEYS_FILE = BASE_DIR / "api_keys.json"
SUBMIT_ERRORS_FILE = BASE_DIR / "submit_errors.json"
VIDEOS_DIR = BASE_DIR / "videos"


def _rel_to_base(p: Path) -> str:
    """Return a POSIX-style path relative to BASE_DIR so history JSON is
    portable across machines and OSes. Falls back to absolute POSIX if the
    file sits outside BASE_DIR (shouldn't happen for archived videos)."""
    try:
        return p.resolve().relative_to(BASE_DIR).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def _resolve_local_path(stored: str | None) -> Path | None:
    """Inverse of _rel_to_base: expand a stored path (relative or absolute,
    POSIX or native) back into an absolute Path for disk access. Old history
    entries with absolute paths still resolve correctly."""
    if not stored:
        return None
    path = Path(stored)
    return path if path.is_absolute() else BASE_DIR / path
MAX_ERROR_LOG = 50
POLL_INTERVAL_SECONDS = 15
VIDEO_URL_TTL_SECONDS = 24 * 3600
ACCEPT_IMAGE = ["png", "jpg", "jpeg", "webp", "bmp", "tiff", "gif"]
ACCEPT_VIDEO = ["mp4", "mov"]
ACCEPT_AUDIO = ["mp3", "wav"]
MAX_AUDIO_COUNT = 3
MAX_AUDIO_SIZE_MB = 15
MAX_IMAGE_SIZE_MB = 30
MAX_REFERENCE_IMAGES = 9
MAX_VIDEO_COUNT = 3
MAX_VIDEO_SIZE_MB = 50
VIDEO_DURATION_RANGE = (2.0, 15.0)
VIDEO_TOTAL_DURATION_MAX = 15.0
VIDEO_SIDE_RANGE = (300, 6000)
VIDEO_ASPECT_RANGE = (0.4, 2.5)
VIDEO_PIXELS_RANGE = (409_600, 2_086_876)
VIDEO_FPS_RANGE = (24, 60)

PROMPT_INSPIRATIONS = [
    "电影级镜头",
    "慢动作",
    "微距特写",
    "赛博朋克",
    "油画质感",
    "航拍俯瞰",
    "黑白胶片",
    "柔光晨曦",
]


# ═══════════════════════════════════════════════════════════════════
# Styling — dark tech aesthetic, MyElcDesign
# ═══════════════════════════════════════════════════════════════════

CSS = """
<style>
/* ── Root palette ── */
:root {
  --bg-0: #08090b;
  --bg-1: #0d0f13;
  --bg-2: #12151a;
  --bg-3: #191d24;
  --bg-4: #232833;
  --line-1: rgba(255,255,255,0.06);
  --line-2: rgba(255,255,255,0.10);
  --line-3: rgba(255,255,255,0.16);
  --fg-0: #f5f7fa;
  --fg-1: #d8dde5;
  --fg-2: #9aa3b2;
  --fg-3: #6b7382;
  --fg-4: #4a5160;
  --accent: oklch(0.72 0.18 175);
  --accent-2: oklch(0.72 0.18 295);
  --ok: oklch(0.78 0.16 155);
  --err: oklch(0.70 0.20 25);
  --mono: "JetBrains Mono", "SF Mono", ui-monospace, Menlo, monospace;
}

/* ── Global background ── */
html, body, [data-testid="stAppViewContainer"], .stApp {
  background: var(--bg-0) !important;
  color: var(--fg-1) !important;
}
body, .stApp {
  font-family: "Inter", -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
}
.block-container {
  padding-top: 1.2rem !important;
  padding-bottom: 2rem !important;
  max-width: 1280px !important;
}
[data-testid="stHeader"] { background: transparent !important; }
#MainMenu, footer { visibility: hidden; }
[data-testid="stAppDeployButton"] { display: none !important; }

/* ── Sidebar ── */
[data-testid="stSidebar"] {
  background: var(--bg-1) !important;
  border-right: 1px solid var(--line-1);
}
[data-testid="stSidebar"] * { color: var(--fg-1); }
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
  color: var(--fg-0) !important;
  font-weight: 600 !important;
  letter-spacing: -0.01em;
}

/* ── Headings ── */
h1, h2, h3, h4 { color: var(--fg-0) !important; letter-spacing: -0.015em; font-weight: 600 !important; }
h1 { font-size: 1.8rem !important; }
h2 { font-size: 1.1rem !important; }

/* ── Inputs ── */
.stTextArea > div > div,
.stNumberInput > div > div,
.stSelectbox > div > div,
[data-testid="stTextInputRootElement"],
[data-baseweb="select"] > div {
  background: var(--bg-2) !important;
  border: 1px solid var(--line-2) !important;
  border-radius: 8px !important;
  color: var(--fg-1) !important;
}
/* Neutralize the inner BaseWeb wrapper so text inputs only render one border */
[data-testid="stTextInputRootElement"] [data-baseweb="base-input"] {
  background: transparent !important;
  border: none !important;
  border-radius: 0 !important;
}
.stTextInput input, .stTextArea textarea, .stNumberInput input {
  background: transparent !important;
  color: var(--fg-0) !important;
  font-size: 14px !important;
}
.stTextArea textarea { line-height: 1.55 !important; padding: 14px 16px !important; }
.stTextArea > div > div:focus-within,
[data-testid="stTextInputRootElement"]:focus-within {
  border-color: color-mix(in oklch, var(--accent) 55%, transparent) !important;
  box-shadow: 0 0 0 3px color-mix(in oklch, var(--accent) 15%, transparent) !important;
}

/* Hero composer textarea */
.hero-composer .stTextArea > div > div {
  background: var(--bg-1) !important;
  border: 1px solid var(--line-2) !important;
  border-radius: 14px !important;
  box-shadow: 0 10px 40px -12px rgba(0,0,0,.5);
}
.hero-composer .stTextArea textarea {
  min-height: 120px !important;
  font-size: 15px !important;
  padding: 18px 20px !important;
  color: var(--fg-0) !important;
}
.hero-composer .stTextArea textarea::placeholder { color: var(--fg-3) !important; }

/* ── Buttons ── */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
  background: var(--bg-2) !important;
  border: 1px solid var(--line-2) !important;
  color: var(--fg-1) !important;
  border-radius: 8px !important;
  font-weight: 500 !important;
  font-size: 13px !important;
  padding: 6px 14px !important;
  min-height: 34px !important;
  transition: border-color .15s, background .15s;
}
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {
  border-color: var(--line-3) !important;
  background: var(--bg-3) !important;
  color: var(--fg-0) !important;
}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
  background: var(--accent) !important;
  color: #001a18 !important;
  border: 1px solid color-mix(in oklch, var(--accent) 70%, transparent) !important;
  font-weight: 600 !important;
  box-shadow: 0 6px 24px -8px color-mix(in oklch, var(--accent) 50%, transparent);
}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover {
  filter: brightness(1.08);
  background: var(--accent) !important;
  color: #001a18 !important;
}
/* Popover trigger button (st.popover) — Streamlit's default is a white-bg
   pill that's invisible on the dark theme. Align it with the other buttons. */
button[data-testid="stPopoverButton"] {
  background: var(--bg-2) !important;
  border: 1px solid var(--line-2) !important;
  color: var(--fg-1) !important;
  border-radius: 8px !important;
  font-weight: 500 !important;
  font-size: 13px !important;
  min-height: 34px !important;
  transition: border-color .15s, background .15s;
}
button[data-testid="stPopoverButton"]:hover {
  border-color: var(--line-3) !important;
  background: var(--bg-3) !important;
  color: var(--fg-0) !important;
}

/* Checkbox/toggle */
.stCheckbox label, .stRadio label, label { color: var(--fg-1) !important; }

/* Slider */
.stSlider [data-baseweb="slider"] div[role="slider"] {
  background: var(--fg-0) !important;
  box-shadow: 0 2px 8px rgba(0,0,0,.4);
}
.stSlider [data-baseweb="slider"] > div:nth-child(3) > div:first-child {
  background: linear-gradient(90deg, var(--accent), var(--accent-2)) !important;
}

/* Segmented control — override Streamlit's default red (#FF4B4B) which is
   hard to read on our dark theme, with our own palette. */
button[data-testid="stBaseButton-segmented_control"],
button[data-testid="stBaseButton-segmented_control"] * {
  background: var(--bg-2) !important;
  color: var(--fg-1) !important;
  border-color: var(--line-2) !important;
}
button[data-testid="stBaseButton-segmented_control"]:hover,
button[data-testid="stBaseButton-segmented_control"]:hover * {
  color: var(--fg-0) !important;
  border-color: var(--line-3) !important;
  background: var(--bg-3) !important;
}
button[data-testid="stBaseButton-segmented_controlActive"],
button[data-testid="stBaseButton-segmented_controlActive"] * {
  background: color-mix(in oklch, var(--accent) 18%, var(--bg-2)) !important;
  color: var(--accent) !important;
  border-color: color-mix(in oklch, var(--accent) 55%, transparent) !important;
}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {
  background: var(--bg-2);
  border: 1px solid var(--line-1);
  border-radius: 8px;
  padding: 4px;
  gap: 2px;
}
.stTabs [data-baseweb="tab"] {
  background: transparent !important;
  color: var(--fg-3) !important;
  border: none !important;
  border-radius: 6px !important;
  font-size: 12.5px !important;
  padding: 6px 14px !important;
  font-weight: 500 !important;
}
.stTabs [aria-selected="true"] {
  background: var(--bg-4) !important;
  color: var(--fg-0) !important;
}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] { display: none !important; }

/* Expander — used for task cards */
.stExpander, [data-testid="stExpander"] {
  background: var(--bg-1) !important;
  border: 1px solid var(--line-1) !important;
  border-radius: 10px !important;
  overflow: hidden;
}
/* Streamlit renders an inner <details> with its own border/radius — flatten it
   so the outer stExpander is the only visible frame. */
.stExpander details, [data-testid="stExpander"] details {
  background: transparent !important;
  border: none !important;
  border-radius: 0 !important;
}
.stExpander summary, [data-testid="stExpander"] summary {
  background: var(--bg-1) !important;
  padding: 12px 16px !important;
  font-size: 13px !important;
  color: var(--fg-1) !important;
}
.stExpander summary:hover, [data-testid="stExpander"] summary:hover {
  background: var(--bg-2) !important;
}
.stExpander [data-testid="stExpanderDetails"] {
  background: var(--bg-1) !important;
  padding: 4px 16px 16px !important;
}

/* File uploader */
[data-testid="stFileUploader"] section {
  background: var(--bg-2) !important;
  border: 1px dashed var(--line-2) !important;
  border-radius: 10px !important;
  padding: 18px !important;
}
[data-testid="stFileUploader"] section:hover { border-color: var(--line-3) !important; }
[data-testid="stFileUploader"] small, [data-testid="stFileUploader"] button { color: var(--fg-2) !important; }

/* Code block */
code, pre, .stCode {
  background: var(--bg-2) !important;
  border: 1px solid var(--line-1) !important;
  border-radius: 6px !important;
  font-family: var(--mono) !important;
  font-size: 11.5px !important;
  color: var(--fg-1) !important;
}

/* Alerts */
.stAlert, [data-testid="stAlert"] {
  background: var(--bg-2) !important;
  border: 1px solid var(--line-1) !important;
  border-radius: 8px !important;
  color: var(--fg-1) !important;
}

/* Video player */
[data-testid="stVideo"] video, video {
  border-radius: 10px !important;
  background: #000 !important;
  box-shadow: 0 4px 20px rgba(0,0,0,.4);
}

/* Caption */
.caption, [data-testid="stCaptionContainer"], small {
  color: var(--fg-3) !important;
  font-size: 11.5px !important;
}

/* ── Custom MyElcDesign components ── */
.myelc-brand {
  display: flex; align-items: center; gap: 10px;
  padding: 14px 0 20px; margin-bottom: 6px;
  border-bottom: 1px solid var(--line-1);
}
.myelc-logo {
  width: 30px; height: 30px; border-radius: 8px;
  background: linear-gradient(135deg, oklch(0.72 0.18 175), oklch(0.70 0.20 295));
  display: flex; align-items: center; justify-content: center;
  color: #001a18; font-weight: 700;
}
.myelc-brand-text {
  font-size: 14px; font-weight: 600; color: var(--fg-0); letter-spacing: -0.01em;
}
.myelc-brand-sub {
  font-family: var(--mono); font-size: 10px; color: var(--fg-3);
  padding: 2px 7px; border: 1px solid var(--line-2); border-radius: 4px;
}

.myelc-hero {
  text-align: center; padding: 24px 0 18px;
}
.myelc-hero .label-xs {
  font-family: var(--mono); font-size: 10px; color: var(--fg-3);
  letter-spacing: 0.12em; text-transform: uppercase;
}
.myelc-hero h1 {
  font-size: 30px !important; font-weight: 600 !important; color: var(--fg-0);
  margin: 8px 0 6px !important; letter-spacing: -0.02em;
}
.myelc-hero p {
  font-size: 13px; color: var(--fg-3); margin: 0;
}

.chip-row { display: flex; gap: 6px; flex-wrap: wrap; margin: 12px 0; }
.chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 4px 10px; font-size: 11px; color: var(--fg-2);
  background: var(--bg-2); border: 1px solid var(--line-1);
  border-radius: 11px; font-family: var(--mono);
}
.chip.on {
  color: var(--accent); border-color: color-mix(in oklch, var(--accent) 40%, transparent);
  background: color-mix(in oklch, var(--accent) 15%, var(--bg-2));
}
.chip.err {
  color: var(--err); border-color: color-mix(in oklch, var(--err) 40%, transparent);
  background: color-mix(in oklch, var(--err) 15%, var(--bg-2));
}

.status-dot {
  width: 6px; height: 6px; border-radius: 50%; display: inline-block; margin-right: 6px;
}
.status-dot.ok { background: var(--ok); box-shadow: 0 0 8px oklch(0.78 0.16 155 / .6); }
.status-dot.run { background: var(--accent); animation: pulse 1.4s ease-in-out infinite; }
.status-dot.err { background: var(--err); }
.status-dot.off { background: var(--fg-4); }
@keyframes pulse { 0%,100% { opacity: 1 } 50% { opacity: .4 } }

.section-label {
  font-family: var(--mono); font-size: 10.5px; color: var(--fg-3);
  letter-spacing: 0.1em; text-transform: uppercase;
  margin: 24px 0 10px; display: flex; align-items: baseline; gap: 10px;
}
.section-label .count { color: var(--fg-2); font-weight: 500; }
.section-label .line { flex: 1; height: 1px; background: var(--line-1); }

.feed-card-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 6px; font-size: 11px;
}
.feed-prompt {
  font-size: 12.5px; color: var(--fg-1); line-height: 1.5;
  margin: 8px 0 6px; display: -webkit-box; -webkit-line-clamp: 2;
  -webkit-box-orient: vertical; overflow: hidden;
}
.feed-meta {
  display: flex; gap: 8px; font-family: var(--mono); font-size: 10.5px;
  color: var(--fg-4);
}
.feed-meta .accent-pro { color: var(--accent); }
.feed-meta .accent-fast { color: oklch(0.74 0.17 55); }

.tunnel-pill {
  display: inline-flex; align-items: center; gap: 6px;
  margin-bottom: 12px;
  padding: 5px 12px; border-radius: 999px;
  background: var(--bg-2); border: 1px solid var(--line-1);
  font-size: 11px; color: var(--fg-2);
  font-family: var(--mono);
}
.tunnel-pill.err { border-color: color-mix(in oklch, var(--err) 30%, transparent); color: var(--err); }

.kv-row {
  display: grid; grid-template-columns: 80px 1fr;
  gap: 6px 14px; font-size: 11.5px; padding: 2px 0;
}
.kv-row .k { color: var(--fg-3); }
.kv-row .v { color: var(--fg-1); font-family: var(--mono); }

.ph-thumb {
  width: 100%; aspect-ratio: 16/9; border-radius: 8px;
  background: radial-gradient(120% 90% at 20% 10%,
    oklch(0.42 0.12 var(--h, 195)) 0%,
    oklch(0.25 0.09 var(--h, 195)) 50%,
    #07080a 100%);
  position: relative; overflow: hidden;
}
.ph-thumb::after {
  content: ""; position: absolute; inset: 0;
  background: linear-gradient(180deg, transparent 60%, rgba(0,0,0,.5));
}
.ph-thumb .ph-label {
  position: absolute; bottom: 10px; left: 12px; right: 12px;
  font-family: var(--mono); font-size: 11px; color: rgba(255,255,255,.85);
  display: flex; justify-content: space-between;
}

.divider-thin { height: 1px; background: var(--line-1); margin: 14px 0; }

/* ── Composer frame: unified border around cards + textarea ── */
.st-key-composer_frame {
  border: 1px solid var(--line-2);
  border-radius: 14px;
  padding: 14px;
  background: var(--bg-1);
  box-shadow: 0 10px 40px -12px rgba(0,0,0,.5);
  margin-bottom: 10px;
}
.st-key-composer_frame .hero-composer .stTextArea > div > div {
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
}
.st-key-composer_frame .hero-composer .stTextArea textarea {
  min-height: 140px !important;
  font-size: 15px !important;
  padding: 8px 10px !important;
}

/* In-frame divider line between composer sections */
.st-key-composer_frame .frame-divider {
  height: 1px;
  background: var(--line-1);
  margin: 8px -14px 10px;
}
/* Neutralize tab styling when nested inside the composer frame so the frame
   remains the dominant visual container. */
.st-key-composer_frame .stTabs [data-baseweb="tab-list"] {
  background: transparent !important;
  border: none !important;
  padding: 0 !important;
  gap: 8px !important;
}
.st-key-composer_frame .stTabs [data-baseweb="tab"] {
  padding: 4px 12px !important;
}
.st-key-composer_frame .stTabs [aria-selected="true"] {
  background: var(--bg-3) !important;
}
/* File uploader inside the frame — trim padding so it breathes evenly. */
.st-key-composer_frame [data-testid="stFileUploader"] section {
  padding: 12px !important;
}

/* Pill segmented control for input_mode */
.stSegmentedControl { margin: 0 !important; }
.stSegmentedControl [data-baseweb="button-group"] {
  background: var(--bg-2);
  border: 1px solid var(--line-1);
  border-radius: 999px;
  padding: 3px;
  gap: 2px;
}
.stSegmentedControl [data-baseweb="button-group"] button {
  border: none !important;
  background: transparent !important;
  border-radius: 999px !important;
  padding: 6px 18px !important;
  font-size: 12.5px !important;
  color: var(--fg-3) !important;
  font-weight: 500 !important;
  min-height: 30px !important;
}
.stSegmentedControl [data-baseweb="button-group"] button[aria-pressed="true"],
.stSegmentedControl [data-baseweb="button-group"] button[kind="primaryFormSubmit"] {
  background: var(--bg-4) !important;
  color: var(--fg-0) !important;
}

/* ——— Empty state (feed) ——— */
.empty-state {
  position: relative;
  margin: 16px 0 32px;
  padding: 44px 28px 40px;
  border: 1px solid var(--line-1);
  border-radius: 14px;
  background:
    radial-gradient(ellipse 120% 80% at 50% -10%,
      color-mix(in oklch, var(--accent) 7%, transparent) 0%,
      transparent 60%),
    var(--bg-1);
  overflow: hidden;
  text-align: center;
}
.empty-state::before {
  content: "";
  position: absolute; inset: 0;
  background-image:
    linear-gradient(var(--line-1) 1px, transparent 1px),
    linear-gradient(90deg, var(--line-1) 1px, transparent 1px);
  background-size: 28px 28px;
  mask-image: radial-gradient(ellipse 70% 55% at 50% 40%, #000 20%, transparent 75%);
  -webkit-mask-image: radial-gradient(ellipse 70% 55% at 50% 40%, #000 20%, transparent 75%);
  opacity: 0.35;
  pointer-events: none;
}
.empty-state-inner {
  position: relative;
  display: flex; flex-direction: column; align-items: center;
  gap: 14px;
}
.empty-icon {
  width: 56px; height: 56px;
  display: flex; align-items: center; justify-content: center;
  border-radius: 14px;
  background: color-mix(in oklch, var(--accent) 14%, var(--bg-2));
  border: 1px solid color-mix(in oklch, var(--accent) 30%, transparent);
  color: var(--accent);
  box-shadow:
    0 0 0 4px color-mix(in oklch, var(--accent) 6%, transparent),
    0 6px 22px -8px color-mix(in oklch, var(--accent) 40%, transparent);
}
.empty-icon svg { width: 26px; height: 26px; }
.empty-title {
  font-size: 15px; font-weight: 600; color: var(--fg-0);
  letter-spacing: -0.01em;
}
.empty-sub {
  font-size: 12.5px; color: var(--fg-3);
  max-width: 420px; line-height: 1.55;
  margin: 0;
}
.empty-hints {
  display: flex; align-items: center; gap: 12px;
  margin-top: 10px;
  flex-wrap: wrap; justify-content: center;
}
.empty-hint {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 6px 12px;
  border: 1px solid var(--line-2);
  border-radius: 999px;
  background: var(--bg-2);
  font-size: 11.5px;
  color: var(--fg-2);
  font-weight: 500;
  transition: border-color .15s, color .15s, background .15s;
}
.empty-hint:hover {
  border-color: color-mix(in oklch, var(--accent) 40%, var(--line-3));
  color: var(--fg-0);
  background: var(--bg-3);
}
.empty-hint svg { width: 13px; height: 13px; opacity: 0.85; }
.empty-hint .kbd {
  display: inline-flex; align-items: center; justify-content: center;
  height: 17px; min-width: 17px; padding: 0 5px;
  border: 1px solid var(--line-2);
  border-radius: 4px;
  background: var(--bg-1);
  font-family: var(--mono); font-size: 10px;
  color: var(--fg-2);
}
.empty-divider {
  font-size: 10.5px;
  color: var(--fg-4);
  font-family: var(--mono);
  text-transform: uppercase;
  letter-spacing: 0.15em;
}
</style>
"""


# ═══════════════════════════════════════════════════════════════════
# Backend (unchanged logic from original)
# ═══════════════════════════════════════════════════════════════════

def resolve_source(
    role_key: str,
    accept: list[str],
    max_size_mb: float | None = None,
) -> str | None:
    uploader_kwargs: dict[str, Any] = {
        "type": accept,
        "key": f"{role_key}_upload",
        "label_visibility": "collapsed",
    }
    if max_size_mb is not None:
        uploader_kwargs["max_upload_size"] = int(max_size_mb)
    upload = st.file_uploader("选择本地文件", **uploader_kwargs)
    if upload is None:
        return None
    if max_size_mb is not None and upload.size > max_size_mb * 1024 * 1024:
        st.error(
            f"文件超过 {max_size_mb:.0f} MB 上限"
            f"（当前 {upload.size / 1024 / 1024:.1f} MB）"
        )
        return None
    try:
        url = tunnel.get_tunnel().publish_bytes(
            upload.getvalue(),
            Path(upload.name).suffix,
        )
    except tunnel.TunnelError as e:
        st.error(f"Cloudflare Tunnel 不可用：{e}")
        return None
    st.success("已通过 Tunnel 暴露为公网 URL")
    st.code(url, language=None)
    return url


def probe_video(data: bytes, suffix: str) -> dict | None:
    """Decode mp4/mov metadata via PyAV. Returns None on decode failure."""
    fmt = suffix.lstrip(".").lower() or "mp4"
    try:
        with av.open(BytesIO(data), format=fmt) as c:
            duration_s = (c.duration or 0) / av.time_base
            stream = c.streams.video[0]
            ctx = stream.codec_context
            w, h = int(ctx.width), int(ctx.height)
            fps = float(stream.average_rate) if stream.average_rate else 0.0
    except (av.FFmpegError, IndexError, AttributeError, ValueError):
        return None
    return {
        "duration": float(duration_s),
        "width": w,
        "height": h,
        "fps": fps,
    }


def validate_video(meta: dict) -> list[str]:
    """Return list of human-readable errors; empty if video satisfies all constraints."""
    errs: list[str] = []
    d = meta["duration"]
    lo, hi = VIDEO_DURATION_RANGE
    if not (lo <= d <= hi):
        errs.append(f"单个时长 {d:.1f}s 不在 [{lo:.0f}, {hi:.0f}]s")
    w, h = meta["width"], meta["height"]
    s_lo, s_hi = VIDEO_SIDE_RANGE
    if not (s_lo <= w <= s_hi):
        errs.append(f"宽 {w}px 不在 [{s_lo}, {s_hi}]")
    if not (s_lo <= h <= s_hi):
        errs.append(f"高 {h}px 不在 [{s_lo}, {s_hi}]")
    if w and h:
        ar = w / h
        ar_lo, ar_hi = VIDEO_ASPECT_RANGE
        if not (ar_lo <= ar <= ar_hi):
            errs.append(f"宽高比 {ar:.2f} 不在 [{ar_lo}, {ar_hi}]")
        px = w * h
        p_lo, p_hi = VIDEO_PIXELS_RANGE
        if not (p_lo <= px <= p_hi):
            errs.append(f"总像素 {px} 不在 [{p_lo}, {p_hi}]")
    fps = meta["fps"]
    f_lo, f_hi = VIDEO_FPS_RANGE
    if not (f_lo <= fps <= f_hi):
        errs.append(f"帧率 {fps:.1f}fps 不在 [{f_lo}, {f_hi}]")
    return errs


def resolve_video_slot(role_key: str) -> tuple[str | None, float]:
    """Upload + validate one reference video. Returns (tunnel_url, duration_seconds).

    Any failure (size / decode / constraint) returns (None, 0.0) with a red
    error rendered inline.
    """
    upload = st.file_uploader(
        "选择本地视频",
        type=ACCEPT_VIDEO,
        key=f"{role_key}_upload",
        label_visibility="collapsed",
        max_upload_size=MAX_VIDEO_SIZE_MB,
    )
    if upload is None:
        return None, 0.0
    if upload.size > MAX_VIDEO_SIZE_MB * 1024 * 1024:
        st.error(
            f"视频超过 {MAX_VIDEO_SIZE_MB} MB 上限"
            f"（当前 {upload.size / 1024 / 1024:.1f} MB）",
            icon=":material/error:",
        )
        return None, 0.0
    data = upload.getvalue()
    suffix = Path(upload.name).suffix
    meta = probe_video(data, suffix)
    if meta is None:
        st.error("视频解码失败，可能文件损坏或不是 mp4/mov", icon=":material/error:")
        return None, 0.0
    errs = validate_video(meta)
    if errs:
        st.error("视频不符合 API 要求：\n- " + "\n- ".join(errs), icon=":material/error:")
        return None, 0.0
    st.caption(
        f"✓ {meta['width']}×{meta['height']} · "
        f"{meta['fps']:.0f}fps · {meta['duration']:.1f}s"
    )
    try:
        url = tunnel.get_tunnel().publish_bytes(data, suffix)
    except tunnel.TunnelError as e:
        st.error(f"Cloudflare Tunnel 不可用：{e}")
        return None, 0.0
    st.success("已通过 Tunnel 暴露为公网 URL")
    st.code(url, language=None)
    return url, meta["duration"]


def _fetch_video_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return resp.read()


def archive_video_locally(task: dict[str, Any]) -> dict[str, Any]:
    """Persist the mp4 and (if returned) the last-frame png into VIDEOS_DIR.

    Idempotent: already-archived artifacts are skipped. Ark URLs expire in
    24h so both the video and the last frame need to be pulled locally
    before that window closes.
    """
    tid = task["task_id"]
    out = dict(task)
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

    # Video
    mp4_resolved = _resolve_local_path(out.get("local_path"))
    if not (mp4_resolved and mp4_resolved.is_file()):
        src_url = out.get("video_url")
        if not src_url:
            return dict(task, archive_error="no_video_url")
        dst = VIDEOS_DIR / f"{tid}.mp4"
        try:
            data = _fetch_video_bytes(src_url)
        except Exception as e:
            return dict(task, archive_error=f"fetch_failed: {e}")
        try:
            tmp = dst.with_suffix(".mp4.tmp")
            tmp.write_bytes(data)
            tmp.replace(dst)
        except Exception as e:
            return dict(task, archive_error=f"write_failed: {e}")
        out["local_path"] = _rel_to_base(dst)
        out["archived_ts"] = int(time.time())
        out.pop("archive_error", None)

    # Last frame (only present when user toggled return_last_frame)
    frame_url = out.get("last_frame_url")
    frame_resolved = _resolve_local_path(out.get("last_frame_local_path"))
    if frame_url and not (frame_resolved and frame_resolved.is_file()):
        frame_dst = VIDEOS_DIR / f"{tid}_last.png"
        try:
            frame_data = _fetch_video_bytes(frame_url)
            tmp = frame_dst.with_suffix(".png.tmp")
            tmp.write_bytes(frame_data)
            tmp.replace(frame_dst)
            out["last_frame_local_path"] = _rel_to_base(frame_dst)
            out.pop("last_frame_archive_error", None)
        except Exception as e:
            out["last_frame_archive_error"] = f"fetch_or_write: {e}"

    return out


_archive_lock = threading.Lock()
_archive_in_progress: set[str] = set()
_archive_results: dict[str, dict[str, Any]] = {}


def _archive_worker(task: dict[str, Any]) -> None:
    tid = task["task_id"]
    try:
        updated = archive_video_locally(task)
    except Exception as e:
        updated = dict(task, archive_error=f"worker_exception: {e}")
    with _archive_lock:
        _archive_results[tid] = updated
        _archive_in_progress.discard(tid)


def start_archive_bg(task: dict[str, Any]) -> bool:
    tid = task["task_id"]
    with _archive_lock:
        if tid in _archive_in_progress or tid in _archive_results:
            return False
        _archive_in_progress.add(tid)
    threading.Thread(target=_archive_worker, args=(task,), daemon=True).start()
    return True


def is_archive_running(tid: str) -> bool:
    with _archive_lock:
        return tid in _archive_in_progress


def load_history() -> list[dict[str, Any]]:
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_history(history: list[dict[str, Any]]) -> None:
    tmp = HISTORY_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, HISTORY_FILE)


def load_saved_keys() -> list[str]:
    if not API_KEYS_FILE.exists():
        return []
    try:
        data = json.loads(API_KEYS_FILE.read_text(encoding="utf-8"))
        return [k for k in data if isinstance(k, str) and k.strip()] if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def remember_api_key(key: str) -> None:
    key = (key or "").strip()
    if not key:
        return
    keys = load_saved_keys()
    if key in keys:
        keys.remove(key)
    keys.insert(0, key)
    keys = keys[:10]
    tmp = API_KEYS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(keys, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, API_KEYS_FILE)


def forget_api_key(key: str) -> None:
    key = (key or "").strip()
    if not key:
        return
    keys = [k for k in load_saved_keys() if k != key]
    tmp = API_KEYS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(keys, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, API_KEYS_FILE)


def mask_api_key(key: str) -> str:
    key = (key or "").strip()
    if len(key) <= 8:
        return "••••"
    return f"{key[:4]}…{key[-4:]}"


ERROR_EXPLAIN: dict[str, dict[str, str]] = {
    "InputImageSensitiveContentDetected.PrivacyInformation": {
        "title": "输入图片疑似包含真人人脸",
        "reason": "seedance 2.0 系列模型不支持直接上传含真人人脸的参考图/首尾帧/视频，"
                  "提交阶段被方舟的内容安全策略拦截。",
        "suggestion": "① 换成不含真人的图（动物/物品/风景/卡通/AI 生成的虚构人像都可以）"
                      " ② 或使用本账号近 30 天内 seedance 生成的含人脸产物作为输入"
                      " ③ 或改用素材库中的预置虚拟人像（asset://<ASSET_ID>）/已授权真人素材",
    },
    "InputImageSensitiveContentDetected": {
        "title": "输入图片触发内容安全校验",
        "reason": "参考图中检测到疑似敏感/违规内容（真人、敏感画面、违规元素等）。",
        "suggestion": "更换参考图；确认图片来源合规；若确认是误判，可尝试换角度或裁剪后重试（判定是概率性的）。",
    },
    "InputTextSensitiveContentDetected": {
        "title": "提示词触发内容安全校验",
        "reason": "prompt 文本中含有违反使用政策的内容（涉政/色情/暴力/违法等）。",
        "suggestion": "修改提示词措辞，去除或替换触发关键词后重试。",
    },
    "SensitiveContentDetected": {
        "title": "输入触发内容安全校验",
        "reason": "输入内容（文本或图片）中检测到疑似违规内容。",
        "suggestion": "检查 prompt 和所有参考素材，移除可能违规的部分。",
    },
    "AuthenticationError": {
        "title": "API Key 无效或已失效",
        "reason": "鉴权未通过——Key 拼写错误、被禁用或已过期。",
        "suggestion": "到火山方舟控制台 → API Key 页面核对，或重新生成并在侧边栏更新。",
    },
    "AccountOverdueError": {
        "title": "账户余额不足",
        "reason": "账户余额低于使用门槛（seedance 2.0 需要 ≥200 元余额或已购买资源包）。",
        "suggestion": "到火山方舟控制台前往充值或购买资源包。",
    },
    "InsufficientQuota": {
        "title": "配额不足",
        "reason": "当前账号或资源包的剩余配额不足以支付本次任务。",
        "suggestion": "检查剩余额度；购买新资源包或等待配额周期重置。",
    },
    "QuotaExceeded": {
        "title": "用量配额已超",
        "reason": "token/次数配额已达上限。",
        "suggestion": "等待周期重置或购买新额度。",
    },
    "RateLimitExceeded": {
        "title": "请求超频（RPM/并发超限）",
        "reason": "短时间内提交任务过多，触发 RPM 或并发数配额限制。",
        "suggestion": "稍等几秒再重试；长期触达可申请配额提升或切换到 flex（离线推理）服务等级。",
    },
    "ModelNotOpen": {
        "title": "模型未开通",
        "reason": "当前账号尚未开通所选模型的服务。",
        "suggestion": "到火山方舟控制台 → 开通模型 页面开通 seedance 2.0 / 2.0 fast。",
    },
    "ResourceNotFound": {
        "title": "模型 ID 不存在或无权限访问",
        "reason": "传入的 model 字段对应的模型/Endpoint 不存在，或当前账号不可用。",
        "suggestion": "核对 model 字段值；确认对应模型已开通。",
    },
    "InvalidParameter": {
        "title": "参数校验失败",
        "reason": "请求体中某个参数值不合法或组合冲突（如分辨率/宽高比/时长超范围、ratio+模型不兼容等）。",
        "suggestion": "查看下方原始消息的 param 字段提示，对应调整侧边栏或 prompt 后重试。",
    },
    "InvalidEndpoint.NotFound": {
        "title": "Endpoint 不存在",
        "reason": "Endpoint ID 拼写错误或已被删除。",
        "suggestion": "到控制台核对 Endpoint ID。",
    },
    "InputImage.InvalidFormat": {
        "title": "输入图片格式不支持",
        "reason": "图片格式不在允许列表内（支持 jpeg/png/webp/bmp/tiff/gif，seedance 1.5 pro 另支持 heic/heif）。",
        "suggestion": "转换为支持的格式后重试。",
    },
    "InputImage.SizeExceeded": {
        "title": "输入图片超出尺寸限制",
        "reason": "图片分辨率或文件大小超限（px 需在 300~6000 之间、宽高比 0.4~2.5、单张 < 30MB）。",
        "suggestion": "压缩或裁剪图片后重试。",
    },
    "InputImage.AspectRatioInvalid": {
        "title": "输入图片宽高比不在允许范围",
        "reason": "图片宽高比不在 [0.4, 2.5]。",
        "suggestion": "裁剪后再上传。",
    },
    "InternalServiceError": {
        "title": "服务端内部错误",
        "reason": "方舟服务端出现未知异常（5xx）。",
        "suggestion": "稍后重试；如持续出现，带上 request_id 提工单。",
    },
    "ServiceUnavailable": {
        "title": "服务暂时不可用",
        "reason": "方舟服务繁忙或正在维护。",
        "suggestion": "稍后重试；或切换到 flex 离线推理等级减压。",
    },
    "RequestTimeout": {
        "title": "请求超时",
        "reason": "客户端或网络层超时，本次请求未到达服务端。",
        "suggestion": "检查本地网络/隧道状态，重新提交。",
    },
}


ERROR_MESSAGE_PATTERNS: list[tuple[str, dict[str, str]]] = [
    ("timeout while fetching resource", {
        "title": "Ark 服务器拉取资源超时",
        "reason": "方舟服务端在超时时间内没能下载到你提交的图片/视频 URL。"
                  "通常是 Cloudflare 隧道慢/抖动、文件过大，或外链源站响应慢。",
        "suggestion": "① 侧边栏确认 Tunnel 状态，若「已断开」则重启应用让隧道重建"
                      " ② 压缩参考图（建议单张 <5MB、<2048px）"
                      " ③ 若用的是外部 URL，换个稳定且 Ark 能访问到的源"
                      " ④ 稍后重试——网络链路可能短暂波动",
    }),
    ("invalid image url", {
        "title": "图片 URL 无法访问",
        "reason": "方舟服务端访问你提交的图片 URL 失败（404 / 403 / DNS 解析不到等）。",
        "suggestion": "检查 URL 是否可被公网访问；本地文件应通过 tunnel.py 暴露，且 tunnel 状态正常。",
    }),
    ("image size", {
        "title": "输入图片尺寸不合规",
        "reason": "图片分辨率或文件大小超出限制（像素 300~6000、宽高比 0.4~2.5、单张 <30MB）。",
        "suggestion": "裁剪或压缩后重试。",
    }),
    ("real person", {
        "title": "输入图片疑似包含真人人脸",
        "reason": "seedance 2.0 系列不支持直接上传含真人人脸的参考图/首尾帧/视频。",
        "suggestion": "换非真人图片，或使用已授权真人素材 / 预置虚拟人像（asset://<ASSET_ID>）。",
    }),
]


_ERR_CODE_RE = re.compile(r"['\"]code['\"]\s*:\s*['\"]([^'\"]+)['\"]")
_ERR_MSG_RE = re.compile(r"['\"]message['\"]\s*:\s*['\"]([^'\"]+)['\"]")
_ERR_REQ_RE = re.compile(r"request[_ ]?id[:=]\s*([A-Za-z0-9_\-]+)")
_ERR_STATUS_RE = re.compile(r"Error code:\s*(\d+)")


def parse_api_error(exc: Exception) -> dict[str, Any]:
    text = str(exc)
    code_m = _ERR_CODE_RE.search(text)
    msg_m = _ERR_MSG_RE.search(text)
    req_m = _ERR_REQ_RE.search(text)
    status_m = _ERR_STATUS_RE.search(text)
    return {
        "code": code_m.group(1) if code_m else "",
        "message": msg_m.group(1) if msg_m else text[:500],
        "request_id": req_m.group(1) if req_m else "",
        "http_status": int(status_m.group(1)) if status_m else None,
        "raw": text,
    }


def explain_error(entry: dict[str, Any]) -> dict[str, str]:
    """Pick the most specific explanation for a failure entry.

    Message-keyword patterns win over code-based lookup because codes like
    ``InvalidParameter`` are umbrella buckets — the real cause lives in the
    human-readable message.
    """
    msg = (entry.get("message") or "").lower()
    for keyword, info in ERROR_MESSAGE_PATTERNS:
        if keyword.lower() in msg:
            return info
    return explain_error_code(entry.get("code") or "")


def explain_error_code(code: str) -> dict[str, str]:
    if not code:
        return {
            "title": "未知错误（未能从异常中解析出错误码）",
            "reason": "这通常是网络/本地异常，而非方舟返回的结构化错误。",
            "suggestion": "检查网络、API Key 与参数；查看原始消息定位。",
        }
    if code in ERROR_EXPLAIN:
        return ERROR_EXPLAIN[code]
    # prefix match: longest matching prefix wins
    best_prefix = ""
    for prefix in ERROR_EXPLAIN:
        if (code == prefix or code.startswith(prefix + ".")) and len(prefix) > len(best_prefix):
            best_prefix = prefix
    if best_prefix:
        return ERROR_EXPLAIN[best_prefix]
    return {
        "title": f"未收录的错误码：{code}",
        "reason": "本地知识库尚未收录此错误码。",
        "suggestion": "查看下方原始消息；带上 request_id 到方舟工单查询。",
    }


def load_submit_errors() -> list[dict[str, Any]]:
    if not SUBMIT_ERRORS_FILE.exists():
        return []
    try:
        data = json.loads(SUBMIT_ERRORS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def log_submit_error(model: str, prompt: str, exc: Exception) -> dict[str, Any]:
    parsed = parse_api_error(exc)
    entry = {
        "ts": int(time.time()),
        "model": model or "",
        "prompt_excerpt": (prompt or "")[:120],
        **parsed,
    }
    errors = load_submit_errors()
    errors.insert(0, entry)
    errors = errors[:MAX_ERROR_LOG]
    tmp = SUBMIT_ERRORS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SUBMIT_ERRORS_FILE)
    return entry


def clear_submit_errors() -> None:
    if SUBMIT_ERRORS_FILE.exists():
        SUBMIT_ERRORS_FILE.unlink()


@st.cache_resource
def get_client(api_key: str, base_url: str) -> Ark:
    # base_url 必须进缓存键：切换网关（本地/dev）时若只看 api_key 会命中旧客户端
    return Ark(api_key=api_key, base_url=base_url)


def _current_base_url() -> str:
    """侧边栏 Base URL 输入框当前值（未渲染侧边栏的代码路径回落默认值）。"""
    return st.session_state.get("base_url") or DEFAULT_BASE_URL


def new_task_record(task_id: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "submitted_ts": int(time.time()),
        "last_polled_ts": None,
        "status": "queued",
        "video_url": None,
        "last_frame_url": None,
        "error_code": None,
        "error_message": None,
        "revised_prompt": None,
        "usage": None,
        "params": params,
    }


def merge_server_task(record: dict[str, Any], server_task: Any) -> dict[str, Any]:
    dumped = server_task.model_dump(exclude_none=False)
    out = dict(record)
    out["status"] = dumped.get("status") or out["status"]
    out["last_polled_ts"] = int(time.time())
    if dumped.get("created_at"):
        out["created_at"] = int(dumped["created_at"])
    if dumped.get("seed") is not None:
        out["seed"] = int(dumped["seed"])
    content = dumped.get("content") or {}
    if content.get("video_url"):
        out["video_url"] = content["video_url"]
    if content.get("last_frame_url"):
        out["last_frame_url"] = content["last_frame_url"]
    error = dumped.get("error") or {}
    if error.get("message"):
        out["error_message"] = error["message"]
        out["error_code"] = error.get("code")
    if dumped.get("revised_prompt"):
        out["revised_prompt"] = dumped["revised_prompt"]
    if dumped.get("usage"):
        out["usage"] = dumped["usage"]
    return out


def init_session_state() -> None:
    defaults = {
        "tasks": None,
        "ref_image_count": 1,
        "ref_video_count": 1,
        "ref_audio_count": 1,
        "prompt_text": "",
        "model_key": "Pro",
        "ratio": "16:9",
        "resolution_choice": "720p",
        "duration": 10,
        "seed_text": "-1",
        "generate_audio": True,
        "camera_fixed": False,
        "return_last_frame": False,
        "web_search": False,
        "watermark": False,
        "show_refs": False,
        "show_advanced": False,
        "input_mode": "first_last_frame",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    if st.session_state.tasks is None:
        st.session_state.tasks = load_history()


# ═══════════════════════════════════════════════════════════════════
# UI — MyElcDesign redesigned layout
# ═══════════════════════════════════════════════════════════════════

def render_brand_header() -> None:
    st.markdown(
        """
        <div class="myelc-brand">
          <div class="myelc-logo">◆</div>
          <div class="myelc-brand-text">MyElcDesign</div>
          <div class="myelc-brand-sub">Seedance 2.0</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar(tun: tunnel.Tunnel) -> dict[str, Any]:
    render_brand_header()

    st.sidebar.markdown('<div class="section-label">API</div>', unsafe_allow_html=True)

    if "api_key" not in st.session_state:
        st.session_state.api_key = os.environ.get("ARK_API_KEY", "")

    saved_keys = load_saved_keys()
    if saved_keys:
        MANUAL = "（手动输入）"
        options = [MANUAL] + [mask_api_key(k) for k in saved_keys]
        try:
            default_idx = saved_keys.index(st.session_state.api_key) + 1
        except ValueError:
            default_idx = 0
        pick_col, del_col = st.sidebar.columns([5, 1])
        choice = pick_col.selectbox(
            "已保存 API Key",
            options=options,
            index=default_idx,
            label_visibility="collapsed",
            key="_saved_key_picker",
        )
        if choice != MANUAL:
            picked = saved_keys[options.index(choice) - 1]
            if st.session_state.api_key != picked:
                st.session_state.api_key = picked
                st.rerun()
            if del_col.button(
                "",
                icon=":material/delete:",
                key="_forget_key_btn",
                help="删除这条已保存的 Key",
            ):
                forget_api_key(picked)
                st.session_state.api_key = ""
                st.rerun()

    api_key = st.sidebar.text_input(
        "API Key",
        type="password",
        label_visibility="collapsed",
        placeholder="sk-wm-... 平台控制台签发的业务 Key",
        key="api_key",
    )

    if "base_url" not in st.session_state:
        st.session_state.base_url = os.environ.get("ARK_BASE_URL", DEFAULT_BASE_URL)
    st.sidebar.text_input(
        "API Base URL",
        label_visibility="collapsed",
        help="平台网关基址：{网关}/cloud/video/api/api/v3。本地=http://localhost:8080；dev 由运维提供",
        key="base_url",
    )

    st.sidebar.markdown('<div class="section-label">模型</div>', unsafe_allow_html=True)
    model_key = st.session_state.get("model_key", "2.5")
    if model_key not in MODELS:
        # 模型表换代后，旧会话里残留的 Pro/Fast 等值已不在表中，回落默认
        model_key = "2.5"
        st.session_state.model_key = model_key
    model_key = st.sidebar.radio(
        "模型",
        list(MODELS.keys()),
        index=list(MODELS.keys()).index(model_key),
        label_visibility="collapsed",
        horizontal=True,
        key="model_key",
    )
    sub, eta, desc = MODEL_DESC[model_key]
    st.sidebar.caption(f"**{sub}** · 预计 {eta} · {desc}")

    st.sidebar.markdown('<div class="section-label">画面</div>', unsafe_allow_html=True)
    c1, c2 = st.sidebar.columns(2)
    ratio = c1.selectbox(
        "比例", RATIOS,
        index=RATIOS.index(st.session_state.get("ratio", "16:9")),
        key="ratio",
    )
    # 会话残留值可能已不在选项表（如档名字符串随上游调整），不在时回落 720p。
    # 回落直接写 session_state（widget 声明之前），selectbox 不再传 index——
    # key 绑定与显式 index 并存会触发 Streamlit "default value ... Session State API" 警告。
    if st.session_state.get("resolution_choice") not in RESOLUTION_CHOICES:
        st.session_state.resolution_choice = RESOLUTION_CHOICES[1]
    resolution_choice = c2.selectbox("分辨率", RESOLUTION_CHOICES, key="resolution_choice")
    resolution = None if resolution_choice == RESOLUTION_CHOICES[0] else resolution_choice
    # 时长上限按模型联动：2.5 最大 30 秒、2.0 最大 15 秒（上游 V1.2：4–15，seedance-2.5 最大 30）。
    # 旧会话值超上限时先钳位写 session_state（widget 声明之前），slider 不传 value 避免同类警告。
    _max_duration = 30 if model_key == "2.5" else 15
    _cur_duration = st.session_state.get("duration", 10)
    if not (4 <= _cur_duration <= _max_duration):
        st.session_state.duration = min(max(_cur_duration, 4), _max_duration)
    duration = st.sidebar.slider("时长 (秒)", 4, _max_duration, key="duration")

    st.sidebar.markdown('<div class="section-label">选项</div>', unsafe_allow_html=True)
    generate_audio = st.sidebar.toggle(
        "生成音频", value=st.session_state.get("generate_audio", True),
        key="generate_audio", help="为视频合成环境声/音乐",
    )
    camera_fixed = st.sidebar.toggle(
        "固定镜头", value=st.session_state.get("camera_fixed", False),
        key="camera_fixed", help="camera_fixed",
    )
    return_last_frame = st.sidebar.toggle(
        "返回尾帧", value=st.session_state.get("return_last_frame", False),
        key="return_last_frame", help="生成结束输出尾帧图片",
    )
    web_search = st.sidebar.toggle(
        "联网搜索增强", value=st.session_state.get("web_search", False),
        key="web_search", help="prompt 增强",
    )
    watermark = st.sidebar.toggle(
        "水印", value=st.session_state.get("watermark", False),
        key="watermark", help="在生成视频右下角添加 Seedance 水印",
    )

    with st.sidebar.expander("高级 · Seed", expanded=False):
        seed_input = st.text_input(
            "Seed（-1 = 随机）",
            value=st.session_state.get("seed_text", "-1"),
            key="seed_text",
        )
    seed: int | None = None
    seed_val = None
    if seed_input.strip():
        try:
            parsed_seed = int(seed_input.strip())
            seed_val = parsed_seed
        except ValueError:
            st.sidebar.error("seed 必须是整数")
        else:
            seed = None if parsed_seed == -1 else parsed_seed

    st.sidebar.markdown('<div class="divider-thin"></div>', unsafe_allow_html=True)

    # Tunnel status pill
    alive = tun.is_alive()
    if alive and tun.public_url:
        short = tun.public_url.replace("https://", "").replace("http://", "")[:36]
        st.sidebar.markdown(
            f'<div class="tunnel-pill"><span class="status-dot ok"></span>Tunnel · {short}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.sidebar.markdown(
            '<div class="tunnel-pill err"><span class="status-dot err"></span>Tunnel 已断开</div>',
            unsafe_allow_html=True,
        )

    if st.sidebar.button("从服务端同步历史", width="stretch"):
        sync_from_server(api_key)

    return {
        "api_key": api_key,
        "base_url": _current_base_url(),
        "model": MODELS[model_key],
        "model_key": model_key,
        "ratio": ratio,
        "resolution": resolution,
        "resolution_choice": resolution_choice,
        "duration": duration,
        "seed": seed,
        "seed_display": "随机" if seed is None else str(seed_val),
        "generate_audio": generate_audio,
        "camera_fixed": camera_fixed,
        "return_last_frame": return_last_frame,
        "web_search": web_search,
        "watermark": watermark,
    }


def sync_from_server(api_key: str) -> None:
    if not api_key:
        st.sidebar.error("先填 API Key")
        return
    client = get_client(api_key, _current_base_url())
    try:
        with st.spinner("拉取服务端历史..."):
            resp = client.content_generation.tasks.list(page_size=50)
    except Exception as e:
        st.sidebar.error(f"拉取失败: {e}")
        return

    existing_by_id = {t["task_id"]: i for i, t in enumerate(st.session_state.tasks)}
    new_count = 0
    updated_count = 0
    for server_task in resp.items:
        tid = server_task.id
        if tid in existing_by_id:
            idx = existing_by_id[tid]
            st.session_state.tasks[idx] = merge_server_task(
                st.session_state.tasks[idx], server_task
            )
            updated_count += 1
        else:
            record = new_task_record(tid, params={})
            st.session_state.tasks.append(merge_server_task(record, server_task))
            new_count += 1

    save_history(st.session_state.tasks)
    st.sidebar.success(
        f"新增 {new_count}，更新 {updated_count}，服务端总计 {resp.total}"
    )


def render_hero_composer(settings: dict[str, Any]) -> tuple[str, bool]:
    """Centered hero composer. Returns (prompt, submit_clicked).

    The composer renders a full-width prompt textarea, a mode switcher, and a
    reference-materials panel below. Both modes (``first_last_frame`` and
    ``omni_reference``) delegate their upload UI to ``render_reference_materials``.
    """
    # Apply any pending mutations BEFORE the text_area widget is instantiated —
    # Streamlit forbids mutating a widget-bound session_state key after its
    # widget has been created in the same run.
    if st.session_state.pop("prompt_text_pending_clear", False):
        st.session_state.prompt_text = ""
    if "prompt_text_pending_append" in st.session_state:
        _kw = st.session_state.pop("prompt_text_pending_append")
        _cur = st.session_state.get("prompt_text", "")
        _sep = "，" if _cur.strip() and not _cur.rstrip().endswith(("，", ",", "。", ".")) else ""
        st.session_state.prompt_text = _cur + _sep + _kw

    st.markdown(
        """
        <div class="myelc-hero">
          <div class="label-xs">SEEDANCE 2.0 · MYELCDESIGN</div>
          <h1>实现你极具想象和创造力的画面</h1>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Summary chips row — reflects current sidebar settings
    chips = [
        ("on", settings["model_key"]),
        ("", settings["ratio"]),
        ("", f"{settings['duration']}s"),
        ("", settings["resolution_choice"] if settings["resolution"] else "默认"),
        ("", f"seed · {settings['seed_display']}"),
    ]
    if settings["generate_audio"]:
        chips.append(("on", "音频"))
    if settings["camera_fixed"]:
        chips.append(("on", "固定镜头"))
    if settings["return_last_frame"]:
        chips.append(("on", "尾帧"))
    if settings["web_search"]:
        chips.append(("on", "联网"))
    if settings["watermark"]:
        chips.append(("on", "水印"))

    chips_html = "".join(
        f'<span class="chip {cls}">{txt}</span>' for cls, txt in chips
    )
    st.markdown(
        f'<div class="chip-row" style="justify-content:center">{chips_html}</div>',
        unsafe_allow_html=True,
    )

    mode = st.session_state.get("input_mode", "first_last_frame")

    # Unified frame: textarea, mode switcher, action row, and reference
    # materials all live here. The container key produces an `st-key-*` class
    # on the wrapper, which scopes our CSS.
    with st.container(key="composer_frame"):
        prompt = _render_prompt_textarea()

        st.markdown('<div class="frame-divider"></div>', unsafe_allow_html=True)

        # Row: mode pill switcher on the left, inspiration + submit on the right
        mode_labels = {"first_last_frame": "首尾帧", "omni_reference": "全能参考模式"}
        action_cols = st.columns([2, 1, 1.6], vertical_alignment="center")
        with action_cols[0]:
            choice = st.segmented_control(
                "输入模式",
                options=list(mode_labels.values()),
                default=mode_labels[mode],
                label_visibility="collapsed",
                key="input_mode_display",
            )
        with action_cols[1].popover("灵感", icon=":material/auto_awesome:", width="stretch"):
            st.caption("点击追加到 prompt")
            for kw in PROMPT_INSPIRATIONS:
                if st.button(f"+ {kw}", key=f"insp_{kw}", width="stretch"):
                    st.session_state.prompt_text_pending_append = kw
                    st.rerun()
        is_submitting = st.session_state.get("_submitting", False)
        submit = action_cols[2].button(
            "提交任务中..." if is_submitting else "生成视频",
            icon=":material/send:",
            type="primary",
            width="stretch",
            disabled=is_submitting
            or not prompt.strip()
            or not settings["api_key"],
        )

        new_mode = next((k for k, v in mode_labels.items() if v == choice), mode)
        if new_mode != mode:
            st.session_state.input_mode = new_mode
            st.rerun()

        # Reference materials live inside the same frame. In first_last_frame
        # mode it renders two image slots (首帧/尾帧); in omni_reference mode
        # it renders the tabbed image/video/audio panel.
        reference_contents = render_reference_materials()

    return prompt, submit, reference_contents


def _render_prompt_textarea() -> str:
    """Renders the main prompt textarea. Returns the prompt text."""
    st.markdown('<div class="hero-composer">', unsafe_allow_html=True)
    prompt = st.text_area(
        "Prompt",
        value=st.session_state.get("prompt_text", ""),
        placeholder="输入文字，描述你想创作的画面内容、运动方式…",
        label_visibility="collapsed",
        key="prompt_text",
        height=130,
    )
    st.markdown("</div>", unsafe_allow_html=True)
    return prompt


def render_reference_materials() -> list[dict[str, Any]]:
    """Returns the list of reference-content entries for the API payload.

    The content depends on the current ``input_mode``:

    - ``first_last_frame`` — renders two image slots (首帧 / 尾帧) using the
      same layout as the omni reference image tab.
    - ``omni_reference`` — renders a tabbed panel for reference image/video/
      audio uploads.
    """
    contents: list[dict[str, Any]] = []
    mode = st.session_state.get("input_mode", "first_last_frame")

    if mode == "first_last_frame":
        st.markdown('<div class="section-label">首尾帧<div class="line"></div></div>', unsafe_allow_html=True)
        st.markdown(
            f"""
<div style="font-size:12.5px; color:var(--fg-2); line-height:1.6; margin-bottom:8px;">
  <div style="color:var(--fg-3); font-size:11.5px;">
    仅上传首帧 = <b style="color:var(--fg-1);">图生视频-首帧</b>（单图驱动）；同时上传首尾帧 = <b style="color:var(--fg-1);">图生视频-首尾帧</b>。
    尾帧宽高比与首帧不一致时，以首帧为主，尾帧会自动居中裁剪适配。
  </div>
  <div style="color:var(--fg-4); font-size:11px; margin-top:4px;">
    单张 ≤ {MAX_IMAGE_SIZE_MB} MB · 支持 jpeg/png/webp/bmp/tiff/gif · 宽高比 0.4–2.5 · 边长 300–6000 px
  </div>
</div>
            """,
            unsafe_allow_html=True,
        )
        slots = [("first_frame", "首帧"), ("last_frame", "尾帧")]
        cols = st.columns(2, vertical_alignment="top")
        for col, (role, label) in zip(cols, slots):
            with col:
                st.markdown(
                    f"""
<div style="display:flex; gap:8px; align-items:baseline; margin:6px 0 4px;">
  <span style="font-size:13px; font-weight:600; color:var(--fg-0);">{label}</span>
  <span style="color:var(--fg-4); font-size:11px;">→</span>
  <code style="font-family:var(--mono); font-size:11px;
               background:rgba(120,160,255,.1); padding:2px 7px; border-radius:3px;
               color:var(--fg-0);">{role}</code>
</div>
                    """,
                    unsafe_allow_html=True,
                )
                url = resolve_source(role, ACCEPT_IMAGE, max_size_mb=MAX_IMAGE_SIZE_MB)
                if url:
                    try:
                        st.image(url, width=280)
                    except Exception:
                        st.warning("预览失败（URL 仍会照常提交）")
                    contents.append({
                        "type": "image_url",
                        "image_url": {"url": url},
                        "role": role,
                    })
        return contents

    # omni_reference mode
    st.markdown('<div class="section-label">参考素材<div class="line"></div></div>', unsafe_allow_html=True)

    tabs = st.tabs([
        ":material/image: 参考图",
        ":material/movie: 参考视频",
        ":material/music_note: 参考音频",
    ])

    with tabs[0]:
        st.session_state.ref_image_count = min(
            st.session_state.ref_image_count, MAX_REFERENCE_IMAGES
        )
        count = st.session_state.ref_image_count
        tokens_html = " ".join(
            f'<code style="font-family:var(--mono); font-size:10.5px; '
            f'background:rgba(120,160,255,.08); padding:1px 5px; border-radius:3px; '
            f'color:var(--fg-1);">[图{n}]</code>'
            for n in range(1, count + 1)
        )
        st.markdown(
            f"""
<div style="font-size:12.5px; color:var(--fg-2); line-height:1.6; margin-bottom:8px;">
  <div style="display:flex; gap:10px; align-items:baseline; margin-bottom:4px;">
    <span style="font-weight:600; color:var(--fg-0);">参考图</span>
    <span style="font-family:var(--mono); font-size:11px; color:var(--fg-3);">
      {count} / {MAX_REFERENCE_IMAGES}
    </span>
  </div>
  <div style="color:var(--fg-3); font-size:11.5px;">
    下面 <b style="color:var(--fg-1);">上传顺序</b> 决定编号 —— 第 1 张 = <code style="font-family:var(--mono); font-size:10.5px;">[图1]</code>，第 2 张 = <code style="font-family:var(--mono); font-size:10.5px;">[图2]</code>，依此类推。
    在提示词里写这些标签可精确指定每张图的角色。
  </div>
  <div style="margin-top:6px; padding:8px 10px; background:rgba(255,255,255,.02);
              border-left:2px solid var(--line-3); border-radius:2px;
              font-size:11.5px; color:var(--fg-2);">
    <div style="color:var(--fg-4); font-size:10.5px; margin-bottom:3px;">当前可用标签</div>
    <div style="margin-bottom:6px;">{tokens_html}</div>
    <div style="color:var(--fg-4); font-size:10.5px; margin-bottom:3px;">示例提示词</div>
    <div style="font-family:var(--mono); font-size:11px; color:var(--fg-1);">
      [图1] 的男生和 [图2] 的柯基小狗，坐在 [图3] 的草坪上，3D 卡通风格
    </div>
  </div>
  <div style="color:var(--fg-4); font-size:11px; margin-top:6px;">
    单张 ≤ {MAX_IMAGE_SIZE_MB} MB · 支持 jpeg/png/webp/bmp/tiff/gif · 宽高比 0.4–2.5 · 边长 300–6000 px
  </div>
</div>
            """,
            unsafe_allow_html=True,
        )
        c1, c2, _ = st.columns([1, 1, 6])
        if c1.button(
            "添加",
            icon=":material/add:",
            disabled=st.session_state.ref_image_count >= MAX_REFERENCE_IMAGES,
        ):
            st.session_state.ref_image_count += 1
            st.rerun()
        if c2.button(
            "移除",
            icon=":material/remove:",
            disabled=st.session_state.ref_image_count <= 1,
        ):
            st.session_state.ref_image_count = max(1, st.session_state.ref_image_count - 1)
            st.rerun()
        for i in range(st.session_state.ref_image_count):
            with st.container(border=False):
                st.markdown(
                    f"""
<div style="display:flex; gap:8px; align-items:baseline; margin:6px 0 4px;">
  <span style="font-size:13px; font-weight:600; color:var(--fg-0);">参考图 {i + 1}</span>
  <span style="color:var(--fg-4); font-size:11px;">→</span>
  <code style="font-family:var(--mono); font-size:11px;
               background:rgba(120,160,255,.1); padding:2px 7px; border-radius:3px;
               color:var(--fg-0);">[图{i + 1}]</code>
  <span style="color:var(--fg-4); font-size:10.5px;">提示词里引用这张图时使用</span>
</div>
                    """,
                    unsafe_allow_html=True,
                )
                url = resolve_source(
                    f"ref_image_{i}",
                    ACCEPT_IMAGE,
                    max_size_mb=MAX_IMAGE_SIZE_MB,
                )
                if url:
                    try:
                        st.image(url, width=280)
                    except Exception:
                        st.warning("预览失败（URL 仍会照常提交）")
                    contents.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": url},
                            "role": "reference_image",
                        }
                    )

    with tabs[1]:
        st.caption(
            f"mp4/mov · 最多 {MAX_VIDEO_COUNT} 个 · 单文件 ≤ {MAX_VIDEO_SIZE_MB} MB · "
            f"单个 [{VIDEO_DURATION_RANGE[0]:.0f},{VIDEO_DURATION_RANGE[1]:.0f}]s · "
            f"总时长 ≤ {VIDEO_TOTAL_DURATION_MAX:.0f}s · "
            f"宽/高 [{VIDEO_SIDE_RANGE[0]},{VIDEO_SIDE_RANGE[1]}]px · "
            f"宽高比 [{VIDEO_ASPECT_RANGE[0]},{VIDEO_ASPECT_RANGE[1]}] · "
            f"总像素 [{VIDEO_PIXELS_RANGE[0]},{VIDEO_PIXELS_RANGE[1]}] · "
            f"{VIDEO_FPS_RANGE[0]}–{VIDEO_FPS_RANGE[1]}fps"
        )
        c1, c2, _ = st.columns([1, 1, 6])
        if c1.button(
            "添加",
            icon=":material/add:",
            key="ref_video_add",
            disabled=st.session_state.ref_video_count >= MAX_VIDEO_COUNT,
        ):
            st.session_state.ref_video_count += 1
            st.rerun()
        if c2.button(
            "移除",
            icon=":material/remove:",
            key="ref_video_remove",
            disabled=st.session_state.ref_video_count <= 1,
        ):
            st.session_state.ref_video_count = max(1, st.session_state.ref_video_count - 1)
            st.rerun()

        video_slots: list[tuple[str, float]] = []
        for i in range(st.session_state.ref_video_count):
            with st.container(border=False):
                st.caption(f"参考视频 {i + 1}")
                url, duration = resolve_video_slot(f"ref_video_{i}")
                if url:
                    try:
                        st.video(url)
                    except Exception:
                        st.warning("预览失败（URL 仍会照常提交）")
                    video_slots.append((url, duration))

        total_duration = sum(d for _, d in video_slots)
        if total_duration > 0:
            color = "var(--ok)" if total_duration <= VIDEO_TOTAL_DURATION_MAX else "var(--err)"
            st.markdown(
                f'<div style="color:{color};font-family:var(--mono);font-size:0.85rem">'
                f"已用总时长 {total_duration:.1f}s / {VIDEO_TOTAL_DURATION_MAX:.0f}s"
                "</div>",
                unsafe_allow_html=True,
            )
        if total_duration > VIDEO_TOTAL_DURATION_MAX:
            st.error(
                f"所有参考视频总时长 {total_duration:.1f}s 超过 "
                f"{VIDEO_TOTAL_DURATION_MAX:.0f}s 上限，请移除或替换",
                icon=":material/error:",
            )
        else:
            for url, _ in video_slots:
                contents.append(
                    {
                        "type": "video_url",
                        "video_url": {"url": url},
                        "role": "reference_video",
                    }
                )

    with tabs[2]:
        st.caption(
            f"格式 mp3/wav · 最多 {MAX_AUDIO_COUNT} 段 · "
            f"单个 ≤ {MAX_AUDIO_SIZE_MB} MB · 单段 2–15s、总时长 ≤ 15s"
        )
        c1, c2, _ = st.columns([1, 1, 6])
        if c1.button(
            "添加",
            icon=":material/add:",
            key="ref_audio_add",
            disabled=st.session_state.ref_audio_count >= MAX_AUDIO_COUNT,
        ):
            st.session_state.ref_audio_count += 1
            st.rerun()
        if c2.button(
            "移除",
            icon=":material/remove:",
            key="ref_audio_remove",
            disabled=st.session_state.ref_audio_count <= 1,
        ):
            st.session_state.ref_audio_count = max(1, st.session_state.ref_audio_count - 1)
            st.rerun()
        for i in range(st.session_state.ref_audio_count):
            with st.container(border=False):
                st.caption(f"参考音频 {i + 1}")
                url = resolve_source(
                    f"ref_audio_{i}",
                    ACCEPT_AUDIO,
                    max_size_mb=MAX_AUDIO_SIZE_MB,
                )
                if url:
                    try:
                        st.audio(url)
                    except Exception:
                        st.warning("预览失败（URL 仍会照常提交）")
                    contents.append(
                        {
                            "type": "audio_url",
                            "audio_url": {"url": url},
                            "role": "reference_audio",
                        }
                    )

    return contents


def submit_task(
    settings: dict[str, Any],
    prompt: str,
    reference_contents: list[dict[str, Any]],
) -> None:
    if not settings["api_key"]:
        st.error("请在侧边栏填 API Key")
        return
    if not prompt.strip():
        st.error("请填写 prompt")
        return

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt.strip()}]
    content.extend(reference_contents)

    tools = [{"type": "web_search"}] if settings["web_search"] else None

    client = get_client(settings["api_key"], settings["base_url"])
    try:
        resp = client.content_generation.tasks.create(
            model=settings["model"],
            content=content,
            ratio=settings["ratio"],
            resolution=settings["resolution"],
            duration=settings["duration"],
            seed=settings["seed"],
            generate_audio=settings["generate_audio"],
            camera_fixed=settings["camera_fixed"],
            return_last_frame=settings["return_last_frame"],
            tools=tools,
            watermark=settings["watermark"],
        )
    except Exception as e:
        entry = log_submit_error(settings.get("model", ""), prompt, e)
        st.session_state._submit_error = entry
        return

    record = new_task_record(
        resp.id,
        params={
            "model": settings["model"],
            "model_key": settings["model_key"],
            "prompt": prompt.strip(),
            "ratio": settings["ratio"],
            "resolution": settings["resolution"],
            "duration": settings["duration"],
            "seed": settings["seed"],
            "generate_audio": settings["generate_audio"],
            "camera_fixed": settings["camera_fixed"],
            "return_last_frame": settings["return_last_frame"],
            "web_search": settings["web_search"],
            "watermark": settings["watermark"],
            "reference_contents": reference_contents,
        },
    )
    st.session_state.tasks.append(record)
    save_history(st.session_state.tasks)
    remember_api_key(settings["api_key"])
    st.toast(f"任务已创建  ·  {resp.id[:16]}…", icon=":material/rocket_launch:")


def render_download(task: dict[str, Any]) -> None:
    tid = task["task_id"]
    local = _resolve_local_path(task.get("local_path"))
    if local and local.is_file():
        st.download_button(
            "下载 MP4",
            icon=":material/download:",
            data=local.read_bytes(),
            file_name=f"seedance_{tid[:8]}.mp4",
            mime="video/mp4",
            key=f"dl_local_{tid}",
            width="stretch",
        )
        return

    url = task["video_url"]
    cache = st.session_state.setdefault("_dl_bytes", {})
    if tid in cache:
        st.download_button(
            "点击保存",
            icon=":material/save:",
            data=cache[tid],
            file_name=f"seedance_{tid[:8]}.mp4",
            mime="video/mp4",
            key=f"dl_save_{tid}",
            width="stretch",
        )
        return
    if st.button("准备下载", icon=":material/download:", key=f"dl_prep_{tid}", width="stretch"):
        try:
            with st.spinner("拉取 mp4..."):
                cache[tid] = _fetch_video_bytes(url)
            st.rerun()
        except Exception as e:
            st.error(f"下载失败：{e}")


def render_last_frame(task: dict[str, Any]) -> None:
    """Inline preview + download for the returned last frame.

    Prefers the locally-archived png (Ark URLs expire in 24h). If not yet
    archived, falls back to the remote URL with an inline archive button.
    """
    url = task.get("last_frame_url")
    local = _resolve_local_path(task.get("last_frame_local_path"))
    local_ok = bool(local) and local.is_file()
    if not url and not local_ok:
        return
    tid = task["task_id"]

    st.markdown(
        '<div style="color:var(--fg-4); font-family:var(--mono); '
        'font-size:10.5px; margin:6px 0 2px">尾帧 · png</div>',
        unsafe_allow_html=True,
    )

    if local_ok:
        frame_bytes = local.read_bytes()
        st.image(frame_bytes, width="stretch")
        st.download_button(
            "下载尾帧",
            icon=":material/download:",
            data=frame_bytes,
            file_name=f"seedance_{tid[:8]}_last.png",
            mime="image/png",
            key=f"dl_frame_local_{tid}",
            width="stretch",
        )
        return

    # Not archived yet (or archiving failed). Preview via remote URL.
    try:
        st.image(url, width="stretch")
    except Exception:
        st.caption("尾帧预览失败（URL 可能已过期，超过 24h）")

    err = task.get("last_frame_archive_error")
    if is_archive_running(tid):
        st.caption(":material/sync: 尾帧归档中…")
    elif err:
        st.caption(f":material/error: 尾帧归档失败: {err}")
        if st.button(
            "重试归档",
            icon=":material/refresh:",
            key=f"frame_retry_{tid}",
            width="stretch",
        ):
            idx = next(
                (i for i, t in enumerate(st.session_state.tasks) if t["task_id"] == tid),
                None,
            )
            if idx is not None:
                cleared = dict(task)
                cleared.pop("last_frame_archive_error", None)
                st.session_state.tasks[idx] = cleared
            start_archive_bg(task)
            st.rerun(scope="fragment")
    else:
        st.caption("尾帧待归档（首次渲染时会自动拉取到 ./videos/）")


def _status_chip(status: str) -> str:
    mapping = {
        "queued": ("off", "排队中"),
        "running": ("run", "生成中"),
        "succeeded": ("ok", "已完成"),
        "failed": ("err", "失败"),
        "cancelled": ("off", "已取消"),
    }
    cls, txt = mapping.get(status, ("off", status))
    return f'<span class="chip {"on" if cls=="ok" else "err" if cls=="err" else ""}"><span class="status-dot {cls}"></span>{txt}</span>'


def _format_submitted(ts: int) -> str:
    now = time.time()
    delta = now - ts
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta/60)} 分钟前"
    if delta < 86400:
        return f"{int(delta/3600)} 小时前"
    if delta < 86400 * 2:
        return "昨天"
    if delta < 86400 * 7:
        return f"{int(delta/86400)} 天前"
    return time.strftime("%m-%d", time.localtime(ts))


def render_feed_card(task: dict[str, Any]) -> None:
    """A single card in the feed grid."""
    tid = task["task_id"]
    status = task["status"]
    params = task.get("params") or {}
    prompt = params.get("prompt") or "(无 prompt)"
    model_key = params.get("model_key") or ("Pro" if "fast" not in (params.get("model") or "") else "Fast")
    ratio = params.get("ratio") or "16:9"
    duration = params.get("duration") or "—"
    ago = _format_submitted(task["submitted_ts"])

    with st.container(border=True):
        # Thumbnail / video
        if status == "succeeded" and task.get("video_url"):
            local = _resolve_local_path(task.get("local_path"))
            local_ok = bool(local) and local.is_file()
            if local_ok:
                st.video(local.read_bytes())
            else:
                st.video(task["video_url"])
        elif status == "running":
            hue = 195
            st.markdown(
                f'<div class="ph-thumb" style="--h:{hue}">'
                f'<div class="ph-label"><span>● 生成中</span><span>{duration}s · {ratio}</span></div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        elif status == "failed":
            st.markdown(
                f'<div class="ph-thumb" style="--h:25">'
                f'<div class="ph-label"><span style="color:#ff9080; display:inline-flex; align-items:center; gap:4px">'
                f'<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>'
                f'失败</span><span>{duration}s · {ratio}</span></div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="ph-thumb" style="--h:220">'
                f'<div class="ph-label"><span>○ 排队中</span><span>{duration}s · {ratio}</span></div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # Header row: status + ago
        st.markdown(
            f'<div class="feed-card-header">'
            f'{_status_chip(status)}'
            f'<span style="color:var(--fg-4); font-family:var(--mono); font-size:10.5px">{ago}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Prompt — escape before injection (prompts can contain `"` / `<` /
        # `>` that would otherwise break the surrounding HTML) and collapse
        # newlines so the 2-line CSS clamp behaves. Long prompts get an
        # inline popover to reveal the full text without breaking the grid.
        safe_prompt = html.escape(prompt).replace("\n", " ")
        st.markdown(
            f'<div class="feed-prompt" title="{safe_prompt}">{safe_prompt}</div>',
            unsafe_allow_html=True,
        )
        if len(prompt) > 80 or "\n" in prompt:
            with st.popover(
                "查看完整 prompt",
                icon=":material/unfold_more:",
                width="stretch",
            ):
                st.markdown(html.escape(prompt).replace("\n", "  \n"))

        # Meta
        model_cls = "accent-pro" if model_key == "Pro" else "accent-fast"
        seed_meta = ""
        if status == "succeeded" and task.get("seed") is not None:
            seed_meta = (
                f'<span>·</span><span title="实际使用的 seed">seed {task["seed"]}</span>'
            )
        st.markdown(
            f'<div class="feed-meta">'
            f'<span>{ratio}</span><span>·</span>'
            f'<span>{duration}s</span><span>·</span>'
            f'<span class="{model_cls}">{model_key}</span>'
            f'{seed_meta}'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Actions — compact row
        if status == "succeeded" and task.get("video_url"):
            render_download(task)
            render_last_frame(task)
            with st.expander("详情", expanded=False):
                render_task_details(task)
        elif status == "failed":
            st.error(
                f"`{task.get('error_code') or ''}` — {task.get('error_message') or '(无错误信息)'}",
                icon=":material/error:",
            )
            with st.expander("参数", expanded=False):
                st.json({k: v for k, v in params.items() if k != "reference_contents"})
        else:
            with st.expander("参数", expanded=False):
                st.json({k: v for k, v in params.items() if k != "reference_contents"})


def render_task_details(task: dict[str, Any]) -> None:
    params = task.get("params") or {}
    tid = task["task_id"]
    actual_seed = task.get("seed")
    requested_seed = params.get("seed")
    if actual_seed is not None and requested_seed is None:
        seed_display = f"{actual_seed}（随机）"
    elif actual_seed is not None:
        seed_display = str(actual_seed)
    elif requested_seed is None:
        seed_display = "随机"
    else:
        seed_display = str(requested_seed)

    rows = [
        ("Task ID", f"`{tid[:24]}…`"),
        ("模型", params.get("model_key") or params.get("model", "—")),
        ("比例", params.get("ratio", "—")),
        ("分辨率", params.get("resolution") or "默认"),
        ("时长", f"{params.get('duration', '—')}s"),
        ("Seed", seed_display),
    ]
    kv = "".join(f'<div class="kv-row"><span class="k">{k}</span><span class="v">{v}</span></div>' for k, v in rows)
    st.markdown(kv, unsafe_allow_html=True)

    if task.get("revised_prompt"):
        st.caption("模型改写后的 prompt")
        st.write(task["revised_prompt"])

    # Archive controls
    local_stored = task.get("local_path")
    local = _resolve_local_path(local_stored)
    local_ok = bool(local) and local.is_file()
    if not local_ok:
        archive_running = is_archive_running(tid)
        if archive_running:
            st.caption(":material/sync: 后台存档中…")
        elif st.button("存档到 ./videos/", icon=":material/inbox:", key=f"arch_{tid}", width="stretch"):
            if task.get("archive_error"):
                idx = next(
                    (i for i, t in enumerate(st.session_state.tasks) if t["task_id"] == tid),
                    None,
                )
                if idx is not None:
                    cleared = dict(task)
                    cleared.pop("archive_error", None)
                    st.session_state.tasks[idx] = cleared
            start_archive_bg(task)
            st.rerun(scope="fragment")
        if task.get("archive_error"):
            st.caption(f":material/error: {task['archive_error']}")
    else:
        st.caption(f":material/check_circle: `{local_stored}`")

    if params.get("reference_contents"):
        with st.expander("参考素材 JSON", expanded=False):
            st.json(params["reference_contents"])


def _is_task_live(task: dict[str, Any]) -> bool:
    if task["status"] not in TERMINAL_STATUSES:
        return True
    if task.get("status") == "succeeded":
        if task.get("video_url") and not task.get("local_path"):
            return True
        if task.get("last_frame_url") and not task.get("last_frame_local_path"):
            return True
    return False


@st.fragment(run_every=POLL_INTERVAL_SECONDS)
def render_live_feed_card(task_id: str, api_key: str) -> None:
    idx = next(
        (i for i, t in enumerate(st.session_state.tasks) if t["task_id"] == task_id),
        None,
    )
    if idx is None:
        return
    task = st.session_state.tasks[idx]

    if task["status"] not in TERMINAL_STATUSES and api_key:
        meaningful_fields = (
            "status", "video_url", "last_frame_url", "error_message", "revised_prompt",
        )
        try:
            client = get_client(api_key, _current_base_url())
            resp = client.content_generation.tasks.get(task_id=task_id)
            updated = merge_server_task(task, resp)
            if any(updated.get(f) != task.get(f) for f in meaningful_fields):
                st.session_state.tasks[idx] = updated
                save_history(st.session_state.tasks)
                task = updated
            else:
                st.session_state.tasks[idx] = updated
                task = updated
        except Exception:
            pass

    with _archive_lock:
        drained = _archive_results.pop(task_id, None)
    if drained is not None:
        st.session_state.tasks[idx] = drained
        save_history(st.session_state.tasks)
        task = drained

    local = _resolve_local_path(task.get("local_path"))
    local_ok = bool(local) and local.is_file()
    frame_local = _resolve_local_path(task.get("last_frame_local_path"))
    frame_local_ok = bool(frame_local) and frame_local.is_file()
    mp4_missing = bool(task.get("video_url")) and not local_ok
    frame_missing = bool(task.get("last_frame_url")) and not frame_local_ok
    if (task.get("status") == "succeeded"
            and (mp4_missing or frame_missing)
            and not task.get("archive_error")):
        start_archive_bg(task)

    render_feed_card(task)


def render_submit_error(entry: dict[str, Any]) -> None:
    """Render an inline, user-friendly explanation for the most recent
    submission failure (banner right under the composer)."""
    explained = explain_error(entry)
    code = entry.get("code") or ""
    http_status = entry.get("http_status")
    head_bits = []
    if http_status:
        head_bits.append(f"HTTP {http_status}")
    if code:
        head_bits.append(code)
    subtitle = " · ".join(head_bits) if head_bits else "未解析出错误码"

    with st.container(border=True):
        st.markdown(
            f"<div style='font-weight:600;color:#ff6b6b;font-size:14.5px'>"
            f"❌ {explained['title']}</div>"
            f"<div style='color:var(--fg-4);font-size:11px;font-family:var(--mono);"
            f"margin-top:2px'>{subtitle}</div>",
            unsafe_allow_html=True,
        )
        st.caption(explained["reason"])
        st.markdown(f"💡 **建议：** {explained['suggestion']}")
        with st.expander("原始消息 / request_id", expanded=False):
            if entry.get("message"):
                st.markdown("**message**")
                st.code(entry["message"], language="text")
            if entry.get("request_id"):
                st.markdown(f"**request_id**  `{entry['request_id']}`")
            st.markdown("**raw**")
            st.code(entry.get("raw", ""), language="text")


def render_error_log_panel() -> None:
    errors = load_submit_errors()
    if not errors:
        return
    with st.expander(
        f"诊断 · 最近提交失败 ({len(errors)})", expanded=False
    ):
        cols = st.columns([5, 1])
        cols[0].caption(
            "仅记录提交阶段（调用 tasks.create）失败的请求。服务端任务失败（status=failed）在下方创作流中查看。"
        )
        if cols[1].button("清空", key="_clear_err_log", width="stretch"):
            clear_submit_errors()
            st.rerun()

        for i, entry in enumerate(errors):
            explained = explain_error(entry)
            when = time.strftime("%m-%d %H:%M:%S", time.localtime(entry.get("ts", 0)))
            code = entry.get("code") or "（无错误码）"
            http = f" · HTTP {entry['http_status']}" if entry.get("http_status") else ""
            with st.container(border=True):
                st.markdown(
                    f"<div style='font-weight:600'>❌ {explained['title']}</div>"
                    f"<div style='color:var(--fg-4);font-size:11px;"
                    f"font-family:var(--mono);margin-top:2px'>"
                    f"{when} · {code}{http} · {entry.get('model','')}</div>",
                    unsafe_allow_html=True,
                )
                if entry.get("prompt_excerpt"):
                    st.caption(f"prompt: {entry['prompt_excerpt']}")
                st.caption(explained["reason"])
                st.markdown(f"💡 {explained['suggestion']}")
                with st.expander("原始消息 / request_id", expanded=False):
                    if entry.get("message"):
                        st.code(entry["message"], language="text")
                    if entry.get("request_id"):
                        st.markdown(f"**request_id**  `{entry['request_id']}`")
                    st.code(entry.get("raw", ""), language="text")


def render_feed(api_key: str) -> None:
    tasks = sorted(
        st.session_state.tasks,
        key=lambda t: t.get("created_at") or t.get("submitted_ts") or 0,
        reverse=True,
    )
    counts = {
        "all": len(tasks),
        "running": sum(1 for t in tasks if t["status"] in ("queued", "running")),
        "succeeded": sum(1 for t in tasks if t["status"] == "succeeded"),
        "failed": sum(1 for t in tasks if t["status"] == "failed"),
    }

    # Section header
    st.markdown(
        f'<div class="section-label">'
        f'最近的创作 <span class="count">{counts["all"]} 个</span>'
        f'<div class="line"></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if not tasks:
        st.markdown(
            """
            <div class="empty-state">
              <div class="empty-state-inner">
                <div class="empty-icon">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"
                       stroke-linecap="round" stroke-linejoin="round">
                    <path d="M4 6.5a2.5 2.5 0 0 1 2.5-2.5h11A2.5 2.5 0 0 1 20 6.5v11a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5v-11Z"/>
                    <path d="m10 9.5 5 2.5-5 2.5v-5Z" fill="currentColor" stroke="none"/>
                  </svg>
                </div>
                <div class="empty-title">还没有创作</div>
                <p class="empty-sub">
                  输入 prompt 生成你的第一段视频，或从服务端同步已有的历史任务。
                </p>
                <div class="empty-hints">
                  <span class="empty-hint">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
                         stroke-linecap="round" stroke-linejoin="round">
                      <path d="M12 19V5"/>
                      <path d="m5 12 7-7 7 7"/>
                    </svg>
                    在上方输入 prompt
                  </span>
                  <span class="empty-divider">或</span>
                  <span class="empty-hint">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
                         stroke-linecap="round" stroke-linejoin="round">
                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                      <path d="M7 10l5 5 5-5"/>
                      <path d="M12 15V3"/>
                    </svg>
                    从侧边栏同步历史
                  </span>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    # Filter
    filter_cols = st.columns([1, 1, 1, 1, 4])
    filters = [
        ("all", f"全部 · {counts['all']}"),
        ("running", f"进行中 · {counts['running']}"),
        ("succeeded", f"已完成 · {counts['succeeded']}"),
        ("failed", f"失败 · {counts['failed']}"),
    ]
    current = st.session_state.get("feed_filter", "all")
    for i, (key, label) in enumerate(filters):
        if filter_cols[i].button(
            label,
            key=f"flt_{key}",
            width="stretch",
            type="primary" if current == key else "secondary",
        ):
            st.session_state.feed_filter = key
            st.rerun()

    # Apply filter
    if current == "running":
        tasks = [t for t in tasks if t["status"] in ("queued", "running")]
    elif current == "succeeded":
        tasks = [t for t in tasks if t["status"] == "succeeded"]
    elif current == "failed":
        tasks = [t for t in tasks if t["status"] == "failed"]

    if not tasks:
        st.caption("此筛选下暂无任务")
        return

    # 3-column masonry-ish grid (Streamlit columns balance by row)
    cols = st.columns(3, gap="small")
    for i, task in enumerate(tasks):
        with cols[i % 3]:
            if _is_task_live(task):
                render_live_feed_card(task["task_id"], api_key)
            else:
                render_feed_card(task)


def main() -> None:
    st.set_page_config(
        page_title="Seedance · MyElcDesign",
        page_icon="◆",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(CSS, unsafe_allow_html=True)

    try:
        tun = tunnel.get_tunnel()
    except tunnel.TunnelError as e:
        st.error(f"Cloudflare Tunnel 启动失败：{e}。请检查网络后重启应用。")
        st.stop()

    init_session_state()
    settings = render_sidebar(tun)

    # Hero + composer (mode switcher & reference panel live inside the frame)
    prompt, submit, reference_contents = render_hero_composer(settings)

    if err := st.session_state.pop("_submit_error", None):
        if isinstance(err, dict):
            render_submit_error(err)
        else:
            # Legacy string-shaped error (shouldn't happen post-upgrade).
            st.error(err)

    # Submit handling: two-pass so the button itself flips to "提交任务中..."
    # while the request is in flight.
    if submit:
        st.session_state._submitting = True
        st.rerun()

    if st.session_state.get("_submitting"):
        try:
            submit_task(settings, prompt, reference_contents)
        finally:
            st.session_state._submitting = False
        st.session_state.prompt_text_pending_clear = True
        st.rerun()

    render_error_log_panel()

    # Feed
    render_feed(settings["api_key"])


if __name__ == "__main__":
    main()
