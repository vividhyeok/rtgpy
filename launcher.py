"""
launcher.py  v3  —  라이브러리 기반 런처
-----------------------------------------
레이아웃:
  왼쪽: 곡 라이브러리 목록 (플레이 / 재생성 / 삭제)
  오른쪽: 새 곡 추가 (YouTube / 로컬 파일)

저장 구조:
  downloads/  ─ 오디오 파일
  notes/      ─ 노트맵 JSON 캐시  (notes/<id>.json)
  library.json ─ 곡 카탈로그
"""

from __future__ import annotations
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import subprocess
import sys
import os
import re
import shutil
import json
import uuid
import time

# ── 경로 ──────────────────────────────────────────────────────────────────────
ROOT         = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(ROOT, "downloads")
NOTES_DIR    = os.path.join(ROOT, "notes")
LIBRARY_FILE = os.path.join(ROOT, "library.json")
for d in (DOWNLOAD_DIR, NOTES_DIR):
    os.makedirs(d, exist_ok=True)


# ── Python 자동 선택 ───────────────────────────────────────────────────────────
def _find_best_python() -> str:
    """pygame 이 설치된 Python 을 우선 선택 (3.11 > 3.12 > 3.10 > 현재)."""
    py_launcher = shutil.which("py")
    if py_launcher:
        for ver in ["3.11", "3.12", "3.10", "3.9"]:
            try:
                r = subprocess.run(
                    [py_launcher, f"-{ver}", "-c", "import pygame; print('ok')"],
                    capture_output=True, text=True, timeout=5
                )
                if r.returncode == 0 and "ok" in r.stdout:
                    print(f"[launcher] Python {ver} + pygame OK")
                    return f"{py_launcher} -{ver}"
            except Exception:
                continue
    return sys.executable


PYTHON_CMD = _find_best_python()     # e.g. "py -3.11"


def _popen(args: list, **kw) -> subprocess.Popen:
    return subprocess.Popen(PYTHON_CMD.split() + args, **kw)


# ── 라이브러리 I/O ──────────────────────────────────────────────────────────────
def lib_load() -> list[dict]:
    if not os.path.exists(LIBRARY_FILE):
        return []
    try:
        with open(LIBRARY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def lib_save(items: list[dict]):
    with open(LIBRARY_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def lib_add(title: str, audio_path: str, sensitivity: float,
            note_count: int, notes_path: str) -> dict:
    """새 항목을 라이브러리에 추가하고 저장."""
    items = lib_load()
    entry = {
        "id":          str(uuid.uuid4())[:8],
        "title":       title,
        "audio_path":  audio_path,
        "notes_path":  notes_path,
        "sensitivity": sensitivity,
        "note_count":  note_count,
        "added":       time.strftime("%Y-%m-%d %H:%M"),
        "scores":      [],
    }
    items.append(entry)
    lib_save(items)
    return entry


def lib_remove(entry_id: str):
    items = [i for i in lib_load() if i["id"] != entry_id]
    lib_save(items)


def lib_update(entry_id: str, **fields):
    items = lib_load()
    for item in items:
        if item["id"] == entry_id:
            item.update(fields)
    lib_save(items)


# ── 유틸 ───────────────────────────────────────────────────────────────────────
def parse_time(s: str):
    s = s.strip()
    if not s:
        return None
    m = re.match(r'^(\d+):(\d{1,2})$', s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    try:
        return float(s)
    except ValueError:
        return None


def sec_to_str(s) -> str:
    if s is None:
        return ""
    return f"{int(s)//60}:{int(s)%60:02d}"


# ══════════════════════════════════════════════════════════════════════════════
# 색상 팔레트
# ══════════════════════════════════════════════════════════════════════════════
BG       = "#0d0d1a"
BG2      = "#14142b"
BG3      = "#1a1a32"
ACCENT   = "#7c5cfc"
ACCENT2  = "#5ce0fc"
TEXT     = "#e8e8f0"
SUBTEXT  = "#7777aa"
ENTRY_BG = "#1e1e38"
SUCCESS  = "#4cfca0"
ERROR    = "#fc5c5c"
WARN     = "#fcb85c"

BTN_PLAY   = "#1a6b3a"
BTN_PLAY_H = "#27a05a"
BTN_REMAP  = "#1a3a6b"
BTN_REMAP_H= "#275aa0"
BTN_DEL    = "#6b1a1a"
BTN_DEL_H  = "#a02727"
BTN_ADD    = "#3a1a6b"
BTN_ADD_H  = "#5a27a0"
BTN_DL     = "#5c3afc"
BTN_DL_H   = "#7c5cfc"


# ══════════════════════════════════════════════════════════════════════════════
# 메인 앱
# ══════════════════════════════════════════════════════════════════════════════
class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("🎵 Rhythm Game Launcher")
        self.geometry("1000x700")
        self.minsize(860, 600)
        self.configure(bg=BG)

        self._dl_thread: threading.Thread | None = None
        self._remap_thread: threading.Thread | None = None

        self._build_ui()
        self._refresh_library()
        self._log(f"[{PYTHON_CMD}] 준비 완료. 곡을 추가하거나 목록에서 플레이하세요.", "ok")

    # ── UI 빌드 ─────────────────────────────────────────────────────────────
    def _build_ui(self):
        # 최상단 헤더
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", padx=18, pady=(14, 6))
        tk.Label(hdr, text="🎵", font=("Segoe UI Emoji", 28), bg=BG,
                 fg=ACCENT).pack(side="left")
        tk.Label(hdr, text="  Rhythm Game Launcher",
                 font=("Segoe UI", 18, "bold"), bg=BG, fg=TEXT).pack(side="left")
        tk.Label(hdr, text="YouTube → 자동 매핑 → 4레인 리듬게임",
                 font=("Segoe UI", 10), bg=BG, fg=SUBTEXT).pack(side="left", padx=18)

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=14, pady=4)

        # 좌우 2패널
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        # ── 왼쪽: 라이브러리 ──
        self._build_library_panel(body)

        # ── 오른쪽: 추가 패널 ──
        self._build_add_panel(body)

    # ── 라이브러리 패널 ─────────────────────────────────────────────────────
    def _build_library_panel(self, parent):
        lf = tk.Frame(parent, bg=BG2,
                      highlightbackground=ACCENT, highlightthickness=1)
        lf.grid(row=0, column=0, sticky="nsew", padx=(4, 6), pady=4)
        lf.rowconfigure(1, weight=1)
        lf.columnconfigure(0, weight=1)

        # 헤더
        hdr = tk.Frame(lf, bg=BG2)
        hdr.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        tk.Label(hdr, text="📂 곡 라이브러리", font=("Segoe UI", 13, "bold"),
                 bg=BG2, fg=ACCENT).pack(side="left")
        self._btn(hdr, "↻ 새로고침", self._refresh_library,
                  BG3, ACCENT, font_size=9).pack(side="right")

        # 스크롤 캔버스
        canvas_frame = tk.Frame(lf, bg=BG2)
        canvas_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)

        self.lib_canvas = tk.Canvas(canvas_frame, bg=BG2, highlightthickness=0)
        vbar = tk.Scrollbar(canvas_frame, orient="vertical",
                            command=self.lib_canvas.yview)
        vbar.grid(row=0, column=1, sticky="ns")
        self.lib_canvas.grid(row=0, column=0, sticky="nsew")
        self.lib_canvas.configure(yscrollcommand=vbar.set)

        self.lib_inner = tk.Frame(self.lib_canvas, bg=BG2)
        self.lib_canvas_window = self.lib_canvas.create_window(
            (0, 0), window=self.lib_inner, anchor="nw")

        self.lib_inner.bind("<Configure>", self._on_lib_configure)
        self.lib_canvas.bind("<Configure>", self._on_canvas_resize)
        self.lib_canvas.bind("<MouseWheel>", lambda e: self.lib_canvas.yview_scroll(
            -1 if e.delta > 0 else 1, "units"))

        # 빈 상태 레이블
        self.empty_label = tk.Label(
            self.lib_inner,
            text="아직 추가된 곡이 없습니다.\n오른쪽에서 유튜브 URL을 입력하거나\n로컬 파일을 선택하세요.",
            font=("Segoe UI", 11), bg=BG2, fg=SUBTEXT, justify="center")

        # 로그 (하단)
        ttk.Separator(lf, orient="horizontal").grid(
            row=2, column=0, sticky="ew", padx=6)
        log_frm = tk.Frame(lf, bg=BG2)
        log_frm.grid(row=3, column=0, sticky="ew", padx=8, pady=(4, 8))
        log_frm.columnconfigure(0, weight=1)
        sb = tk.Scrollbar(log_frm)
        sb.grid(row=0, column=1, sticky="ns")
        self.log_box = tk.Text(log_frm, height=5, font=("Consolas", 9),
                               bg=BG, fg=TEXT, bd=0, relief="flat",
                               yscrollcommand=sb.set,
                               state="disabled", wrap="word")
        self.log_box.grid(row=0, column=0, sticky="ew")
        sb.config(command=self.log_box.yview)
        self.log_box.tag_config("ok",   foreground=SUCCESS)
        self.log_box.tag_config("err",  foreground=ERROR)
        self.log_box.tag_config("warn", foreground=WARN)

    def _on_lib_configure(self, event):
        self.lib_canvas.configure(scrollregion=self.lib_canvas.bbox("all"))

    def _on_canvas_resize(self, event):
        self.lib_canvas.itemconfig(self.lib_canvas_window, width=event.width)

    # ── 추가 패널 ───────────────────────────────────────────────────────────
    def _build_add_panel(self, parent):
        rf = tk.Frame(parent, bg=BG2,
                      highlightbackground=ACCENT2, highlightthickness=1)
        rf.grid(row=0, column=1, sticky="nsew", padx=(2, 4), pady=4)

        tk.Label(rf, text="➕ 새 곡 추가", font=("Segoe UI", 13, "bold"),
                 bg=BG2, fg=ACCENT2).pack(anchor="w", padx=12, pady=(12, 6))
        ttk.Separator(rf, orient="horizontal").pack(fill="x", padx=8)

        # YouTube
        self._sub_label(rf, "🎬 YouTube")
        url_frm = tk.Frame(rf, bg=BG2)
        url_frm.pack(fill="x", padx=10, pady=4)
        tk.Label(url_frm, text="URL", font=("Segoe UI", 9), bg=BG2,
                 fg=SUBTEXT, width=6, anchor="w").grid(row=0, column=0)
        self.url_var = tk.StringVar()
        tk.Entry(url_frm, textvariable=self.url_var,
                 font=("Segoe UI", 10), bg=ENTRY_BG, fg=TEXT,
                 insertbackground=TEXT, relief="flat", bd=4
                 ).grid(row=0, column=1, sticky="ew", padx=4)
        url_frm.columnconfigure(1, weight=1)

        time_frm = tk.Frame(rf, bg=BG2)
        time_frm.pack(fill="x", padx=10, pady=2)
        tk.Label(time_frm, text="시작", font=("Segoe UI", 9), bg=BG2,
                 fg=SUBTEXT, width=4, anchor="w").grid(row=0, column=0)
        self.start_var = tk.StringVar(value="0:00")
        tk.Entry(time_frm, textvariable=self.start_var,
                 font=("Segoe UI", 10), bg=ENTRY_BG, fg=TEXT,
                 insertbackground=TEXT, relief="flat", bd=4, width=7
                 ).grid(row=0, column=1, padx=4)
        tk.Label(time_frm, text="끝", font=("Segoe UI", 9), bg=BG2,
                 fg=SUBTEXT, width=3, anchor="w").grid(row=0, column=2)
        self.end_var = tk.StringVar(value="")
        tk.Entry(time_frm, textvariable=self.end_var,
                 font=("Segoe UI", 10), bg=ENTRY_BG, fg=TEXT,
                 insertbackground=TEXT, relief="flat", bd=4, width=7
                 ).grid(row=0, column=3, padx=4)
        tk.Label(time_frm, text="(비우면 전체)", font=("Segoe UI", 8),
                 bg=BG2, fg=SUBTEXT).grid(row=0, column=4, padx=4)

        self._btn(rf, "⬇  YouTube 다운로드 & 추가", self._download,
                  BTN_DL, BTN_DL_H).pack(fill="x", padx=10, pady=(6, 4))

        ttk.Separator(rf, orient="horizontal").pack(fill="x", padx=8, pady=6)

        # 로컬 파일
        self._sub_label(rf, "📁 로컬 파일")
        self.local_label = tk.Label(rf, text="선택된 파일 없음",
                                    font=("Segoe UI", 9), bg=BG2, fg=SUBTEXT)
        self.local_label.pack(anchor="w", padx=12)
        btn_row = tk.Frame(rf, bg=BG2)
        btn_row.pack(fill="x", padx=10, pady=4)
        self._btn(btn_row, "📂 파일 선택", self._pick_local,
                  "#1e3a5c", "#2a5575", font_size=10).pack(side="left", fill="x",
                                                            expand=True, padx=(0, 4))
        self._btn(btn_row, "▶ 추가 & 분석", self._add_local,
                  BTN_ADD, BTN_ADD_H, font_size=10).pack(side="left", fill="x",
                                                           expand=True)

        ttk.Separator(rf, orient="horizontal").pack(fill="x", padx=8, pady=6)

        # 사용 중인 Python 표시
        tk.Frame(rf, bg=BG2).pack(fill="y", expand=True)
        ttk.Separator(rf, orient="horizontal").pack(fill="x", padx=8, pady=4)
        tk.Label(rf, text=f"Python: {PYTHON_CMD}",
                 font=("Consolas", 8), bg=BG2, fg=SUBTEXT
                 ).pack(anchor="w", padx=10, pady=(0, 8))

    def _sub_label(self, parent, text):
        tk.Label(parent, text=text, font=("Segoe UI", 10, "bold"),
                 bg=BG2, fg=TEXT).pack(anchor="w", padx=12, pady=(8, 2))

    def _btn(self, parent, text, cmd, bg_c, hov_c,
             font_size=11, **pack_kw) -> tk.Button:
        b = tk.Button(parent, text=text,
                      font=("Segoe UI", font_size, "bold"),
                      bg=bg_c, fg=TEXT,
                      activebackground=hov_c, activeforeground=TEXT,
                      relief="flat", bd=0, cursor="hand2", command=cmd)
        b.bind("<Enter>", lambda e: b.config(bg=hov_c))
        b.bind("<Leave>", lambda e: b.config(bg=bg_c))
        return b

    # ── 라이브러리 렌더링 ────────────────────────────────────────────────────
    def _refresh_library(self):
        for w in self.lib_inner.winfo_children():
            w.destroy()

        items = lib_load()
        if not items:
            self.empty_label = tk.Label(
                self.lib_inner,
                text="아직 추가된 곡이 없습니다.\n오른쪽에서 유튜브 URL 입력 또는\n로컬 파일을 선택하세요.",
                font=("Segoe UI", 11), bg=BG2, fg=SUBTEXT, justify="center")
            self.empty_label.pack(expand=True, pady=60)
            return

        for idx, entry in enumerate(items):
            self._render_entry(entry, idx)

    def _render_entry(self, entry: dict, idx: int):
        row_bg = BG3 if idx % 2 == 0 else BG2

        card = tk.Frame(self.lib_inner, bg=row_bg,
                        highlightbackground="#2a2a50", highlightthickness=1)
        card.pack(fill="x", padx=6, pady=3)

        # 왼쪽: 곡 정보
        info = tk.Frame(card, bg=row_bg)
        info.pack(side="left", fill="both", expand=True, padx=10, pady=8)

        # 제목 (길면 truncate)
        title = entry.get("title", "Unknown")
        if len(title) > 42:
            title = title[:39] + "…"
        tk.Label(info, text=title, font=("Segoe UI", 11, "bold"),
                 bg=row_bg, fg=TEXT, anchor="w").pack(anchor="w")

        note_count = entry.get("note_count", "?")
        sens       = entry.get("sensitivity", "?")
        added      = entry.get("added", "")
        has_notes  = os.path.exists(entry.get("notes_path", ""))
        has_audio  = os.path.exists(entry.get("audio_path", ""))

        status_col = SUCCESS if (has_notes and has_audio) else WARN
        status_txt = f"♪ {note_count}노트  ·  {added}"
        tk.Label(info, text=status_txt, font=("Segoe UI", 9),
                 bg=row_bg, fg=status_col).pack(anchor="w")

        scores = entry.get("scores", [])
        if scores:
            top_scores = sorted(scores, reverse=True)[:3]
            rank_str = "   |   ".join(f"{i+1}위: {sc:,}" for i, sc in enumerate(top_scores))
            tk.Label(info, text=f"🏆 {rank_str}", font=("Segoe UI", 9, "bold"),
                     bg=row_bg, fg=ACCENT2).pack(anchor="w", pady=(2,0))

        if not has_audio:
            tk.Label(info, text="⚠ 오디오 파일 없음", font=("Segoe UI", 8),
                     bg=row_bg, fg=ERROR).pack(anchor="w")

        # 오른쪽: 버튼들
        btns = tk.Frame(card, bg=row_bg)
        btns.pack(side="right", padx=8, pady=8)

        eid = entry["id"]

        btn_row = tk.Frame(btns, bg=row_bg)
        btn_row.pack(side="top")

        play_state = "normal" if (has_audio and has_notes) else "disabled"

        pb = self._btn(btn_row, "▶ 플레이",
                       lambda e=entry: self._play(e),
                       BTN_PLAY, BTN_PLAY_H, font_size=9)
        pb.config(state=play_state)
        pb.pack(side="left", padx=2)

        rb = self._btn(btn_row, "🔄 재생성",
                       lambda e=entry: self._remap(e, 1.0),
                       BTN_REMAP, BTN_REMAP_H, font_size=9)
        rb.config(state="normal" if has_audio else "disabled")
        rb.pack(side="left", padx=2)

        db = self._btn(btn_row, "🗑 삭제",
                       lambda e=entry: self._delete(e),
                       BTN_DEL, BTN_DEL_H, font_size=9)
        db.pack(side="left", padx=2)

    # ── 액션: 플레이 ────────────────────────────────────────────────────────
    def _play(self, entry: dict):
        if not os.path.exists(entry["audio_path"]):
            self._log(f"오디오 파일 없음: {entry['audio_path']}", "err")
            return
        notes_arg = entry.get("notes_path", "")
        game_script = os.path.join(ROOT, "game.py")
        args = [game_script, entry["audio_path"], "1.0"]  # dummy sensitivity for backwards compat
        if notes_arg and os.path.exists(notes_arg):
            args.append(notes_arg)
        args.append(entry["id"])  # Pass Library ID!
        self._log(f"▶ 플레이: {entry['title']}")
        try:
            _popen(args)
        except Exception as ex:
            self._log(f"실행 오류: {ex}", "err")

    # ── 액션: 재생성 ────────────────────────────────────────────────────────
    def _remap(self, entry: dict, new_sens: float):
        if self._remap_thread and self._remap_thread.is_alive():
            self._log("재생성 작업이 이미 진행 중입니다.", "warn")
            return

        ans = messagebox.askyesno(
            "재생성 확인",
            f"'{entry['title']}' 곡의 노트를 재생성하시겠습니까?\n\n"
            "※ 주의: 노트 배열이 바뀌므로 이전 플레이 최고 점수 기록이 모두 초기화됩니다!"
        )
        if not ans:
            return

        self._log(f"🔄 재생성 시작: {entry['title']} (민감도={new_sens})")
        self._remap_thread = threading.Thread(
            target=self._remap_worker, args=(entry, new_sens), daemon=True)
        self._remap_thread.start()

    def _remap_worker(self, entry: dict, new_sens: float):
        try:
            from mapper import generate_notes
            notes = generate_notes(entry["audio_path"], new_sens)
        except Exception as ex:
            msg = str(ex)
            self.after(0, lambda m=msg: self._log(f"재생성 오류: {m}", "err"))
            return

        notes_path = os.path.join(NOTES_DIR, entry["id"] + ".json")
        with open(notes_path, "w", encoding="utf-8") as f:
            json.dump(notes, f)

        lib_update(entry["id"],
                   notes_path=notes_path,
                   sensitivity=new_sens,
                   note_count=len(notes),
                   scores=[])

        self.after(0, lambda: self._log(
            f"✅ 재생성 완료: {entry['title']}  →  {len(notes)}노트 (민감도={new_sens})", "ok"))
        self.after(0, self._refresh_library)

    # ── 액션: 삭제 ──────────────────────────────────────────────────────────
    def _delete(self, entry: dict):
        ans = messagebox.askyesnocancel(
            "삭제 확인",
            f"'{entry['title']}' 삭제\n\n"
            "예(Y) : 목록에서 완전히 삭제 (유튜브 다운로드한 오디오 파일 포함)\n"
            "아니오(N) : 매핑 기록(노트)만 삭제하고 곡은 유지\n"
            "취소 : 취소"
        )
        if ans is None:
            return

        notes_p = entry.get("notes_path", "")
        if ans is False:  # 아니오: 매핑만 삭제하고 리스트에서도 제거
            if notes_p and os.path.exists(notes_p):
                try: os.remove(notes_p)
                except Exception: pass
            lib_remove(entry["id"])
            self._log(f"🗑 매핑 삭제 및 리스트 제거됨: {entry['title']}", "warn")
        else:  # 예: 완전히 삭제
            audio_p = entry.get("audio_path", "")
            if notes_p and os.path.exists(notes_p):
                try: os.remove(notes_p)
                except Exception: pass
            
            # 오디오 파일 삭제 (DOWNLOAD_DIR 안에 있는 경우에만 삭제! 로컬 원본 파일 보호)
            if audio_p and os.path.exists(audio_p):
                if os.path.abspath(audio_p).startswith(os.path.abspath(DOWNLOAD_DIR)):
                    try: os.remove(audio_p)
                    except Exception: pass

            lib_remove(entry["id"])
            self._log(f"🗑 곡 삭제됨: {entry['title']}", "warn")

        self._refresh_library()

    # ── 로컬 파일 선택 ──────────────────────────────────────────────────────
    def _pick_local(self):
        path = filedialog.askopenfilename(
            title="오디오 파일 선택",
            filetypes=[("Audio", "*.wav *.mp3 *.ogg *.flac *.m4a"), ("All", "*.*")])
        if path:
            self._local_path = path
            self.local_label.config(text=f"✅ {os.path.basename(path)}", fg=SUCCESS)

    def _add_local(self):
        path = getattr(self, "_local_path", None)
        if not path or not os.path.exists(path):
            messagebox.showwarning("파일 없음", "먼저 파일을 선택하세요.")
            return
        sens = 1.0
        title = os.path.splitext(os.path.basename(path))[0]
        self._log(f"분석 시작: {title}")
        # 분석은 별도 스레드
        t = threading.Thread(
            target=self._analyze_and_register,
            args=(path, title, sens), daemon=True)
        t.start()

    # ── YouTube 다운로드 ─────────────────────────────────────────────────────
    def _download(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("URL 없음", "YouTube URL을 입력하세요.")
            return
        if self._dl_thread and self._dl_thread.is_alive():
            self._log("이미 다운로드 중입니다.", "warn")
            return
        start = parse_time(self.start_var.get())
        end   = parse_time(self.end_var.get())
        sens  = 1.0
        self._log(f"다운로드 시작: {url}")
        self._dl_thread = threading.Thread(
            target=self._dl_worker, args=(url, start, end, sens), daemon=True)
        self._dl_thread.start()

    def _dl_worker(self, url: str, start, end, sens: float):
        import uuid as uuid_mod
        uid      = str(uuid_mod.uuid4())[:8]
        out_tmpl = os.path.join(DOWNLOAD_DIR, uid)

        # yt-dlp 로 제목 취득
        title = uid
        try:
            r = subprocess.run(
                PYTHON_CMD.split() + ["-m", "yt_dlp", "--get-title",
                                      "--no-playlist", url],
                capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                title = r.stdout.strip().split("\n")[0][:80] or uid
        except Exception:
            pass

        cmd = PYTHON_CMD.split() + [
            "-m", "yt_dlp",
            "-x", "--audio-format", "wav",
            "--audio-quality", "0",
            "-o", out_tmpl + ".%(ext)s",
            "--no-playlist",
        ]
        if start is not None or end is not None:
            ss      = f"-ss {start or 0}"
            to_part = f"-to {end}" if end else ""
            cmd += ["--postprocessor-args", f"ffmpeg:{ss} {to_part}".strip()]
        cmd.append(url)

        self.after(0, lambda: self._log(f"  yt-dlp 실행 중…"))
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace")
            for line in proc.stdout:
                line = line.rstrip()
                if line and (
                        "[download]" in line or "Destination" in line
                        or "ERROR" in line or "%" in line):
                    self.after(0, lambda l=line: self._log(f"  {l}"))
            proc.wait()
        except Exception as ex:
            msg = str(ex)
            self.after(0, lambda m=msg: self._log(f"다운로드 오류: {m}", "err"))
            return

        # 결과 파일 찾기
        audio_path = None
        import glob
        for ext in ("wav", "mp3", "ogg", "m4a", "webm"):
            c = out_tmpl + f".{ext}"
            if os.path.exists(c):
                audio_path = c
                break
        if not audio_path:
            found = glob.glob(out_tmpl + "*")
            if found:
                audio_path = found[0]

        if not audio_path:
            self.after(0, lambda: self._log("다운로드 실패: 파일을 찾을 수 없음", "err"))
            return

        self.after(0, lambda: self._log(f"다운로드 완료 → 분석 시작…", "ok"))
        self._analyze_and_register(audio_path, title, sens)

    # ── 분석 & 라이브러리 등록 ───────────────────────────────────────────────
    def _analyze_and_register(self, audio_path: str, title: str, sens: float):
        """오디오 파일 분석 후 라이브러리에 등록. 메인/서브 스레드 모두 OK."""
        import uuid as uuid_mod
        eid = str(uuid_mod.uuid4())[:8]

        try:
            from mapper import generate_notes
            notes = generate_notes(audio_path, sens)
        except Exception as ex:
            msg = str(ex)
            self.after(0, lambda m=msg: self._log(f"분석 오류: {m}", "err"))
            return

        notes_path = os.path.join(NOTES_DIR, eid + ".json")
        with open(notes_path, "w", encoding="utf-8") as f:
            json.dump(notes, f)

        entry = lib_add(title, audio_path, sens, len(notes), notes_path)
        # id가 uuid 겹치지 않게 notes 파일명도 맞춤
        proper_notes = os.path.join(NOTES_DIR, entry["id"] + ".json")
        if proper_notes != notes_path:
            shutil.move(notes_path, proper_notes)
            lib_update(entry["id"], notes_path=proper_notes)

        self.after(0, lambda: self._log(
            f"✅ 추가 완료: {title}  →  {len(notes)}노트", "ok"))
        self.after(0, self._refresh_library)

    # ── 로그 ──────────────────────────────────────────────────────────────
    def _log(self, msg: str, tag: str = ""):
        def _do():
            self.log_box.config(state="normal")
            self.log_box.insert("end", msg + "\n", tag)
            self.log_box.see("end")
            self.log_box.config(state="disabled")
        self.after(0, _do)


# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = Launcher()
    app.mainloop()
