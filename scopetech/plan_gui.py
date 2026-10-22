"""계획 모드 결과·설정 창 (tkinter). 측정은 CLI 에서만 하고, 이 창은 보기·설정·예전 기록 가져오기만 한다.

탭: 진행(항목 표 + 상세·캡처 이미지) / 요약표 / 설정(overrides.ini) / 가져오기(예전 측정)
1초마다 파일 수정 시각을 보고 바뀌었을 때만 다시 그린다. 스코프 캡처 이미지만 보여주고 파형을 새로 그리지 않는다.
"""
from __future__ import annotations

import logging
import math
import os
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import messagebox, ttk

from . import plan_import
from .plan import DONE, FAIL, SKIP, describe
from .plan_view import LEFT, PlanData, mark_gui

POLL_MS = 1000
ALIVE_MS = 2000
STATUS_TAG = {DONE: "done", FAIL: "fail", SKIP: "skip", LEFT: "left"}
log = logging.getLogger("plan_gui")


def open_path(path: Path) -> None:
    if hasattr(os, "startfile"):
        os.startfile(str(path))  # Windows: 탐색기/엑셀로 열기
    else:
        messagebox.showinfo("열기", str(path))


class ImageBox(ttk.Frame):
    """스코프 캡처 PNG 를 칸 크기에 맞게 정수배로 줄여 보여준다 (Tk 8.6 PNG)."""

    def __init__(self, master, max_w=640, max_h=400):
        super().__init__(master)
        self.max_w, self.max_h = max_w, max_h
        self.label = ttk.Label(self, text="캡처 이미지 없음", anchor="center")
        self.label.pack(fill="both", expand=True)
        self.photo = None
        self.path = None

    def show(self, path: Path | None) -> None:
        if path == self.path:
            return
        self.path = path
        self.photo = None
        if path is None:
            self.label.configure(image="", text="캡처 이미지 없음")
            return
        try:
            img = tk.PhotoImage(file=str(path))
            k = max(1, math.ceil(img.width() / self.max_w), math.ceil(img.height() / self.max_h))
            self.photo = img.subsample(k, k) if k > 1 else img
            self.label.configure(image=self.photo, text="")
        except (tk.TclError, OSError) as e:
            self.label.configure(image="", text=f"이미지를 열 수 없음: {path.name}\n{e}")


def tree(master, columns: list[tuple[str, str, int]], height=12) -> ttk.Treeview:
    frame = ttk.Frame(master)
    tv = ttk.Treeview(frame, columns=[c for c, _, _ in columns], show="headings", height=height, selectmode="browse")
    for cid, title, width in columns:
        tv.heading(cid, text=title)
        tv.column(cid, width=width, stretch=width >= 200, anchor="w")
    ys = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
    tv.configure(yscrollcommand=ys.set)
    tv.grid(row=0, column=0, sticky="nsew")
    ys.grid(row=0, column=1, sticky="ns")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    tv.frame = frame
    tv.tag_configure("done", foreground="#1b5e20")
    tv.tag_configure("fail", foreground="#b71c1c")
    tv.tag_configure("skip", foreground="#757575")
    tv.tag_configure("left", foreground="#000000")
    tv.tag_configure("current", background="#fff59d")
    tv.tag_configure("blank", background="#ffebee")
    return tv


class PlanWindow:
    def __init__(self, root: tk.Tk, data: PlanData):
        self.root, self.data = root, data
        self.sig = None
        self.temp = tk.StringVar()
        self.follow = tk.BooleanVar(value=True)
        root.title("시험 계획 — 결과·설정")
        root.geometry("1280x800")
        root.minsize(900, 600)
        root.protocol("WM_DELETE_WINDOW", self.close)

        top = ttk.Frame(root, padding=(8, 6))
        top.pack(fill="x")
        self.cli = ttk.Label(top, text="", font=("TkDefaultFont", 10, "bold"))
        self.cli.pack(side="left")
        ttk.Checkbutton(top, text="CLI 온도 따라가기", variable=self.follow).pack(side="right")
        self.temp_box = ttk.Combobox(top, textvariable=self.temp, width=8, state="readonly")
        self.temp_box.pack(side="right", padx=4)
        self.temp_box.bind("<<ComboboxSelected>>", lambda _: self._pick_temp())
        ttk.Label(top, text="온도(°C)").pack(side="right")

        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self._build_progress()
        self._build_summary()
        self._build_settings()
        self._build_import()

        self._timers: dict[str, str] = {}
        self.refresh(force=True)
        self._timers["poll"] = self.root.after(POLL_MS, self._poll)
        self._alive()

    # ------------------------------------------------------------ 공통
    def close(self) -> None:
        for t in self._timers.values():
            self.root.after_cancel(t)
        self._timers.clear()
        mark_gui(self.data.store.dir, closing=True)
        self.root.destroy()

    def _alive(self) -> None:
        mark_gui(self.data.store.dir)
        self._timers["alive"] = self.root.after(ALIVE_MS, self._alive)

    def _poll(self) -> None:
        try:
            self.refresh()
        finally:
            self._timers["poll"] = self.root.after(POLL_MS, self._poll)

    def _pick_temp(self) -> None:
        self.follow.set(False)  # 직접 고르면 따라가기 끔
        self.refresh(force=True)

    def refresh(self, force: bool = False) -> None:
        state = self.data.state()
        temps = self.data.temps()
        if list(self.temp_box["values"]) != temps:
            self.temp_box["values"] = temps
        if self.follow.get() and self.data.running(state) and str(state.get("temp")) in temps:
            self.temp.set(str(state["temp"]))
        if self.temp.get() not in temps:
            self.temp.set(temps[0] if temps else "")
        self.cli.configure(text=self.data.cli_text(state))
        self._import_guard(state)
        sig = self.data.signature(self.temp.get(), state)
        if not force and sig == self.sig:
            return
        self.sig = sig
        self._fill_progress(state)
        self._fill_summary()
        self._reload_settings_if_clean()

    # ------------------------------------------------------------ 진행 탭
    def _build_progress(self) -> None:
        tab = ttk.Frame(self.tabs, padding=6)
        self.tabs.add(tab, text="진행")
        self.counts = ttk.Label(tab, text="")
        self.counts.pack(anchor="w")
        pane = ttk.PanedWindow(tab, orient="horizontal")
        pane.pack(fill="both", expand=True, pady=(4, 0))
        self.ptree = tree(pane, [("no", "순서", 56), ("kind", "항목", 50), ("pins", "핀", 80), ("type", "출력", 44),
                                 ("status", "상태", 60), ("result", "결과", 330), ("warn", "주의", 200)], height=20)
        self.ptree.bind("<<TreeviewSelect>>", lambda _: self._show_detail())
        pane.add(self.ptree.frame, weight=3)
        right = ttk.Frame(pane, padding=(6, 0))
        pane.add(right, weight=2)
        self.image = ImageBox(right, 560, 340)
        self.image.pack(fill="x")
        self.detail = tk.Text(right, height=14, wrap="word", relief="flat")
        self.detail.pack(fill="both", expand=True, pady=4)
        self.detail.configure(state="disabled")
        btns = ttk.Frame(right)
        btns.pack(fill="x")
        self.open_folder = ttk.Button(btns, text="측정 폴더 열기", command=self._open_folder, state="disabled")
        self.open_folder.pack(side="left")
        self.open_image = ttk.Button(btns, text="이미지 크게 보기", command=lambda: open_path(self.image.path),
                                     state="disabled")
        self.open_image.pack(side="left", padx=4)
        self.folder = None

    def _fill_progress(self, state: dict) -> None:
        temp = self.temp.get()
        keep = self.ptree.selection()
        self.ptree.delete(*self.ptree.get_children())
        if not temp:
            self.counts.configure(text="진행 기록이 아직 없습니다. CLI(run_plan.bat)에서 온도를 고르면 시작됩니다.")
            return
        rows = self.data.progress_rows(temp, state)
        current = None
        for r in rows:
            tags = [STATUS_TAG.get(r["status"], "left")] + (["current"] if r["current"] else [])
            status = "측정 중" if r["measuring"] else r["status"]
            src = " (가져옴)" if r["source"] else ""
            self.ptree.insert("", "end", iid=r["key"], tags=tags,
                              values=(("▶ " if r["current"] else "") + r["no"], r["kind"], r["pins"], r["type"], status + src, r["result"], r["warnings"]))
            if r["current"]:
                current = r["key"]
        self.counts.configure(text=f"{temp}°C  " + self.data.counts(rows))
        target = keep[0] if keep and self.ptree.exists(keep[0]) else current
        if current and self.follow.get():
            target = current
        if target:
            self.ptree.selection_set(target)
            self.ptree.see(target)
        self._show_detail()

    def _show_detail(self) -> None:
        sel = self.ptree.selection()
        if not sel or not self.temp.get():
            return
        d = self.data.detail(self.temp.get(), sel[0])
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("end", "\n".join(d["lines"]))
        self.detail.configure(state="disabled")
        self.image.show(d["image"])
        self.folder = d["folder"]
        self.open_folder.configure(state="normal" if self.folder else "disabled")
        self.open_image.configure(state="normal" if d["image"] else "disabled")

    def _open_folder(self) -> None:
        if self.folder:
            open_path(self.folder)

    # ------------------------------------------------------------ 요약표 탭
    def _build_summary(self) -> None:
        tab = ttk.Frame(self.tabs, padding=6)
        self.tabs.add(tab, text="요약표")
        bar = ttk.Frame(tab)
        bar.pack(fill="x")
        self.sum_info = ttk.Label(bar, text="")
        self.sum_info.pack(side="left")
        ttk.Button(bar, text="plan_summary.csv 열기", command=self._open_summary).pack(side="right")
        self.sum_frame = ttk.Frame(tab)
        self.sum_frame.pack(fill="both", expand=True, pady=(4, 0))
        self.stree = None

    def _fill_summary(self) -> None:
        temp = self.temp.get()
        head, rows, blank = self.data.summary(temp)
        if self.stree is None or list(self.stree["columns"]) != [f"c{i}" for i in range(len(head))]:
            if self.stree is not None:
                self.stree.frame.destroy()
            cols = [(f"c{i}", h, 90 if i >= 4 else (70 if i else 80)) for i, h in enumerate(head)]
            self.stree = tree(self.sum_frame, cols, height=30)
            for i in range(4, len(head)):
                self.stree.column(f"c{i}", anchor="e")
            self.stree.frame.pack(fill="both", expand=True)
        self.stree.delete(*self.stree.get_children())
        for r, b in zip(rows, blank):
            self.stree.insert("", "end", values=r, tags=("blank",) if b else ())
        filled = sum(1 for r in rows for c in r[4:] if c)
        total = len(rows) * max(0, len(head) - 4)
        self.sum_info.configure(text=f"채운 칸 {filled} / {total}  ({temp}°C 칸이 빈 줄은 붉은 배경, "
                                     "같은 값이 여러 측정에서 나오면 최악값)")

    def _open_summary(self) -> None:
        p = self.data.summary_csv()
        if p.exists():
            open_path(p)
        else:
            messagebox.showinfo("요약표", f"아직 {p.name} 가 없습니다. CLI 가 측정을 끝내거나 run_plan_summary.bat 으로 만듭니다.")

    # ------------------------------------------------------------ 설정 탭
    def _build_settings(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="설정")
        ttk.Label(tab, text="저장하면 CLI 가 다음 측정 직전에 반영합니다(측정 중인 값은 바뀌지 않음). "
                            "config.ini 는 바꾸지 않고 plan/overrides.ini 에 저장합니다. 온도는 CLI 에서 정합니다.",
                  wraplength=900).pack(anchor="w", pady=(0, 8))
        body = ttk.Frame(tab)
        body.pack(fill="x")
        self.vars: dict[str, tk.Variable] = {}
        self.base_labels: dict[str, ttk.Label] = {}
        groups: dict[str, ttk.LabelFrame] = {}
        for item in self.data.settings_form():
            s = item["setting"]
            if s.group not in groups:
                groups[s.group] = ttk.LabelFrame(body, text=s.group, padding=6)
                groups[s.group].pack(fill="x", pady=3)
            g = groups[s.group]
            r = len(g.grid_slaves(column=0))
            ttk.Label(g, text=s.label, width=22).grid(row=r, column=0, sticky="w")
            if s.kind == "bool":
                var = tk.BooleanVar()
                ttk.Checkbutton(g, variable=var, text="켬").grid(row=r, column=1, sticky="w")
            else:
                var = tk.StringVar()
                ttk.Entry(g, textvariable=var, width=24).grid(row=r, column=1, sticky="w")
            ttk.Label(g, text=s.unit, width=24).grid(row=r, column=2, sticky="w", padx=4)
            self.base_labels[s.id] = ttk.Label(g, text="", foreground="#616161")
            self.base_labels[s.id].grid(row=r, column=3, sticky="w")
            var.trace_add("write", lambda *_: self._dirty(True))
            self.vars[s.id] = var
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=8)
        ttk.Button(bar, text="저장", command=self._save_settings).pack(side="left")
        ttk.Button(bar, text="config.ini 값으로", command=self._reset_settings).pack(side="left", padx=4)
        ttk.Button(bar, text="다시 읽기", command=lambda: self._load_settings()).pack(side="left")
        self.set_info = ttk.Label(bar, text="")
        self.set_info.pack(side="left", padx=10)
        self.dirty = False
        self._load_settings()

    def _dirty(self, on: bool) -> None:
        if getattr(self, "_loading", False):
            return
        self.dirty = on
        if on:
            self.set_info.configure(text="저장하지 않은 변경 있음", foreground="#e65100")

    def _load_settings(self) -> None:
        self._loading = True
        try:
            for item in self.data.settings_form():
                s, var = item["setting"], self.vars[item["id"]]
                if s.kind == "bool":
                    var.set(item["value"].lower() in ("1", "yes", "true", "on", "y"))
                else:
                    var.set(item["value"])
                base = item["base"] if s.kind != "bool" else ("켬" if item["base"].lower() in ("1", "yes", "true", "on", "y") else "끔")
                self.base_labels[item["id"]].configure(
                    text=f"config.ini: {base}" + ("  (바꿈)" if item["override"] is not None and item["id"] != "session.name" else ""))
        finally:
            self._loading = False
        self.dirty = False
        self.set_info.configure(text="", foreground="")

    def _reload_settings_if_clean(self) -> None:
        if not self.dirty:
            self._load_settings()

    def _values(self) -> dict[str, str]:
        out = {}
        for sid, var in self.vars.items():
            v = var.get()
            out[sid] = ("yes" if v else "no") if isinstance(var, tk.BooleanVar) else str(v)
        return out

    def _save_settings(self) -> None:
        try:
            errors = self.data.save_settings(self._values())
        except OSError as e:
            errors = [f"저장하지 못했습니다: {e}"]
        if errors:
            messagebox.showerror("설정", "\n".join(errors))
            return
        self.dirty = False
        self._load_settings()
        self.set_info.configure(text="저장됨 — 다음 측정부터 반영", foreground="#1b5e20")

    def _reset_settings(self) -> None:
        if not messagebox.askyesno("설정", "계산·분석·진행 값을 config.ini 값으로 되돌릴까요? (시험 이름은 그대로)"):
            return
        try:
            self.data.reset_settings()
        except OSError as e:
            messagebox.showerror("설정", f"저장하지 못했습니다: {e}")
            return
        self.dirty = False
        self._load_settings()
        self.set_info.configure(text="config.ini 값으로 되돌림 — 다음 측정부터 반영", foreground="#1b5e20")

    # ------------------------------------------------------------ 가져오기 탭
    def _build_import(self) -> None:
        tab = ttk.Frame(self.tabs, padding=6)
        self.tabs.add(tab, text="가져오기")
        bar = ttk.Frame(tab)
        bar.pack(fill="x")
        self.prev_btn = ttk.Button(bar, text="미리보기", command=self._import_preview)
        self.prev_btn.pack(side="left")
        self.apply_btn = ttk.Button(bar, text="적용", command=self._import_apply, state="disabled")
        self.apply_btn.pack(side="left", padx=4)
        self.imp_info = ttk.Label(bar, text="결과 폴더의 예전 측정(VIH / MANUAL / POR)을 계획 항목에 연결합니다. "
                                            "미리보기 -> 체크(☑ 가져옴) -> 적용")
        self.imp_info.pack(side="left", padx=8)
        inner = ttk.Notebook(tab)
        inner.pack(fill="both", expand=True, pady=(4, 0))
        top = ttk.Frame(inner)
        inner.add(top, text="계획 항목")
        pane = ttk.PanedWindow(top, orient="horizontal")
        pane.pack(fill="both", expand=True)
        self.itree = tree(pane, [("on", "", 30), ("temp", "온도", 50), ("no", "순서", 40), ("kind", "항목", 44),
                                 ("pins", "핀", 70), ("state", "처리", 110), ("result", "결과", 300),
                                 ("rec", "기록", 260)], height=18)
        self.itree.bind("<<TreeviewSelect>>", lambda _: self._import_detail())
        self.itree.bind("<Button-1>", self._import_click, add="+")
        self.itree.bind("<space>", lambda _: self._toggle_selected())
        pane.add(self.itree.frame, weight=3)
        right = ttk.Frame(pane, padding=(6, 0))
        pane.add(right, weight=2)
        ttk.Label(right, text="같은 항목의 후보 기록 (최근 것부터)").pack(anchor="w")
        self.ctree = tree(right, [("time", "측정 시각", 140), ("rec", "기록", 240), ("val", "저장된 값", 260)], height=6)
        self.ctree.frame.pack(fill="x")
        self.ctree.bind("<<TreeviewSelect>>", lambda _: self._candidate_image())
        cb = ttk.Frame(right)
        cb.pack(fill="x", pady=2)
        self.use_btn = ttk.Button(cb, text="이 기록 사용", command=self._use_candidate, state="disabled")
        self.use_btn.pack(side="left")
        self.cimage = ImageBox(right, 520, 260)
        self.cimage.pack(fill="x")
        self.inotes = tk.Text(right, height=8, wrap="word", relief="flat")
        self.inotes.pack(fill="both", expand=True)
        bottom = ttk.Frame(inner)
        inner.add(bottom, text="연결 안 된 기록")
        self.utree = tree(bottom, [("rec", "기록", 420), ("temp", "온도", 50), ("kind", "종류", 60), ("pin", "핀", 50),
                                   ("why", "이유", 420)], height=20)
        self.utree.frame.pack(fill="both", expand=True)
        self.picks, self.unmatched, self.icfg = [], [], None
        self._job = None

    def _import_guard(self, state: dict) -> None:
        running = self.data.running(state)
        busy = self._job is not None
        self.prev_btn.configure(state="disabled" if busy else "normal")
        can = bool(self.picks) and not running and not busy and any(p.state == plan_import.SOURCE for p in self.picks)
        self.apply_btn.configure(state="normal" if can else "disabled")
        if running and self.picks:
            self.imp_info.configure(text="CLI 가 계획을 진행 중이라 적용할 수 없습니다(미리보기만). CLI 를 끝낸(q) 뒤 적용하세요.",
                                    foreground="#b71c1c")

    def _import_preview(self) -> None:
        self.imp_info.configure(text="기록을 읽는 중... (파형으로 값을 채우는 기록이 많으면 오래 걸릴 수 있음)", foreground="")
        box: dict = {}

        def work():
            try:
                box["r"] = self.data.import_preview()
            except Exception as e:  # noqa: BLE001 - 작업 스레드 오류를 화면에 보여준다
                box["e"] = e
                log.exception("가져오기 미리보기 실패")
        self._job = threading.Thread(target=work, daemon=True)
        self._job.start()
        self._wait(box)

    def _wait(self, box: dict) -> None:
        if self._job and self._job.is_alive():
            self._timers["import"] = self.root.after(200, lambda: self._wait(box))
            return
        self._timers.pop("import", None)
        self._job = None
        if "e" in box:
            self.imp_info.configure(text=f"미리보기 실패: {box['e']}", foreground="#b71c1c")
        else:
            self.picks, self.unmatched, self.icfg = box["r"]
            self._fill_import()
            new = sum(p.state == plan_import.SOURCE for p in self.picks)
            self.imp_info.configure(text=f"연결된 항목 {len(self.picks)}개, 가져올 항목 {new}개, 연결 안 된 기록 {len(self.unmatched)}개",
                                    foreground="")
        self._import_guard(self.data.state())

    def _fill_import(self) -> None:
        keep = self.itree.selection()
        self.itree.delete(*self.itree.get_children())
        for n, p in enumerate(self.picks):
            mark = "☑" if p.state == plan_import.SOURCE else ("☐" if p.state in ("유지", "제외") else "")
            tag = {plan_import.SOURCE: "done", "없음": "fail", "같음": "skip"}.get(p.state, "left")
            self.itree.insert("", "end", iid=str(n), tags=(tag,), values=(
                mark, p.temp, p.row.no, p.row.kind, plan_import._pins(p.row), plan_import.STATE_TEXT[p.state],
                describe(p.row, p.result) if p.result else "", p.found.rel if p.found else ""))
        self.utree.delete(*self.utree.get_children())
        for f in self.unmatched:
            self.utree.insert("", "end", values=(f.rel, f.temp, f.kind or f.item, f.pin, f.problem))
        if keep and self.itree.exists(keep[0]):
            self.itree.selection_set(keep[0])

    def _pick(self):
        sel = self.itree.selection()
        return self.picks[int(sel[0])] if sel else None

    def _import_click(self, event) -> None:
        if self.itree.identify_column(event.x) == "#1" and self.itree.identify_region(event.x, event.y) == "cell":
            row = self.itree.identify_row(event.y)
            if row:
                self.itree.selection_set(row)
                self._toggle_selected()

    def _toggle_selected(self) -> None:
        p = self._pick()
        if p is None:
            return
        plan_import.include(p, p.state != plan_import.SOURCE)
        self._fill_import()
        self._import_guard(self.data.state())

    def _import_detail(self) -> None:
        p = self._pick()
        self.ctree.delete(*self.ctree.get_children())
        self.inotes.delete("1.0", "end")
        if p is None:
            return
        for n, f in enumerate(p.options):
            self.ctree.insert("", "end", iid=str(n), values=(f.time, f.rel, describe(p.row, f.result)),
                              tags=("current",) if f is p.found else ())
        e = p.entry
        lines = [f"{p.temp}°C  {p.row.no}. {p.row.title()}",
                 f"처리: {plan_import.STATE_TEXT[p.state]}",
                 "지금 진행 기록: " + (f"{e.get('status')} {e.get('folder', '')}" + (" (가져옴)" if e.get("source") else "")
                                   if e else "없음")]
        lines += [f"- {n}" for n in p.notes]
        self.inotes.insert("end", "\n".join(lines))
        self.use_btn.configure(state="disabled")
        self.cimage.show(PlanData.image_of(p.found.folder) if p.found else None)

    def _candidate(self):
        p, sel = self._pick(), self.ctree.selection()
        return (p, p.options[int(sel[0])]) if p and sel else (p, None)

    def _candidate_image(self) -> None:
        p, f = self._candidate()
        self.use_btn.configure(state="normal" if f is not None and f is not p.found else "disabled")
        if f is not None:
            self.cimage.show(PlanData.image_of(f.folder))

    def _use_candidate(self) -> None:
        p, f = self._candidate()
        if f is None:
            return
        try:
            plan_import.choose(p, f, self.icfg)
        except ValueError as e:
            messagebox.showerror("가져오기", f"이 기록은 쓸 수 없습니다: {e}")
            return
        self._fill_import()
        self._import_detail()
        self._import_guard(self.data.state())

    def _import_apply(self) -> None:
        todo = [p for p in self.picks if p.state == plan_import.SOURCE]
        if not todo:
            return
        rewrite = sum(p.changed for p in todo)
        msg = f"{len(todo)}개 항목을 진행 기록에 넣고 요약표를 다시 만듭니다."
        if rewrite:
            msg += f"\n그중 {rewrite}개는 파형으로 값을 채워 result.json 을 고쳐 씁니다(원본은 result.orig.json)."
        if not messagebox.askyesno("가져오기 적용", msg + "\n적용할까요?"):
            return
        try:
            written, logf, summary = self.data.import_apply(self.picks, self.unmatched)
        except (RuntimeError, OSError) as e:
            messagebox.showerror("가져오기", str(e))
            return
        self.picks, self.unmatched = [], []
        self._fill_import()
        self.imp_info.configure(text=f"적용함: 항목 {len(todo)}개, 내역 {logf.name}. 미리보기로 다시 확인할 수 있습니다.",
                                foreground="#1b5e20")
        self.refresh(force=True)


def run(data: PlanData, log_path: Path | None = None) -> None:
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=str(log_path), level=logging.INFO, encoding="utf-8",
                            format="%(asctime)s %(levelname)s %(message)s")
    root = tk.Tk()

    def report(exc, val, tb):
        log.error("GUI 오류\n%s", "".join(traceback.format_exception(exc, val, tb)))
        messagebox.showerror("오류", f"{val}\n(자세한 내용: {log_path or '로그 없음'})")
    root.report_callback_exception = report
    PlanWindow(root, data)
    root.mainloop()
