"""DevBoard Toolkit GUI (纯界面框架,未连接实际功能)

结构:
  主窗口(标题栏 + 设置按钮)
    └─ ttk.Notebook (4 个 Tab)
         ├─ TabDataProcessing   (数据处理)
         ├─ TabJenkinsBuild      (感知包编译)
         ├─ TabFeedback          (自动回灌)
         └─ TabPipeline          (组合流水线)
"""

import datetime as _dt
import os
import sys
import threading
import queue
import time
import yaml
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, scrolledtext
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# 确保 sys.path 包含项目根目录 (解决 python devboard_toolkit\gui.py 启动时
# sys.path[0] = devboard_toolkit\ 导致 from devboard_toolkit.xxx 找不到包)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) \
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
# 同时把项目根目录加到 PYTHONPATH 环境变量, 这样子进程/新线程也能找到
os.environ["PYTHONPATH"] = _PROJECT_ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")


# ---------------------------------------------------------------------------
# 通用基础设施: stdout 重定向 + 后台线程执行
# ---------------------------------------------------------------------------

class _GUILogWriter:
    """重定向 stdout 到 GUI 日志面板 (线程安全)

    通过 queue 把日志传到主线程, 由 _LogPanel.poll_log() 消费。
    支持可选 prefix, 用于多任务并发时区分 [T1]/[T2] 等输出。
    """
    def __init__(self, log_queue: queue.Queue, original_stdout, prefix: str = ""):
        self._q = log_queue
        self._orig = original_stdout
        self._buffer = ""
        self._prefix = prefix

    def write(self, text):
        if not text:
            return
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._q.put(self._prefix + line.rstrip("\r"))
        # 处理 \r 原地刷新的进度条: 取最后一行
        if "\r" in self._buffer:
            parts = self._buffer.split("\r")
            self._buffer = parts[-1]
            for p in parts[:-1]:
                if p.strip():
                    self._q.put(p.rstrip())

    def flush(self):
        pass


class _ThreadLocalStdout:
    """线程局部的 stdout 代理

    每个线程可通过 set_writer() 设置自己的重定向 writer,
    未设置的线程走原始 stdout, 实现各 tab 日志独立不串台。
    """
    _instance = None

    def __init__(self, original):
        self._original = original
        self._local = threading.local()

    def set_writer(self, writer):
        self._local.writer = writer

    def clear_writer(self):
        if hasattr(self._local, 'writer'):
            del self._local.writer

    def write(self, text):
        if not text:
            return
        writer = getattr(self._local, 'writer', None)
        if writer is not None:
            writer.write(text)
        else:
            self._original.write(text)

    def flush(self):
        writer = getattr(self._local, 'writer', None)
        if writer is not None:
            writer.flush()
        else:
            self._original.flush()

    @classmethod
    def install(cls):
        """全局安装线程局部 stdout 代理 (仅安装一次)"""
        if cls._instance is None:
            cls._instance = _ThreadLocalStdout(sys.stdout)
            sys.stdout = cls._instance
        return cls._instance


def _run_in_thread(target, log_panel, stop_event, on_done=None, prefix=""):
    """在后台线程中执行 target, stdout 重定向到 log_panel

    使用线程局部的 _ThreadLocalStdout, 每个线程的 print() 只输出到
    对应的 log_panel, 避免多个 tab 同时运行时日志串台。

    Args:
        target: 可调用对象, 接受 stop_event 参数
        log_panel: _LogPanel 实例
        stop_event: threading.Event 实例 (用于取消)
        on_done: 完成后的回调, 接受 (rc, app_name) 参数
        prefix: 日志前缀 (如 "[T1] "), 用于多任务并发时区分输出
    """
    tls_stdout = _ThreadLocalStdout.install()
    log_queue = queue.Queue()
    writer = _GUILogWriter(log_queue, tls_stdout._original, prefix=prefix)

    def _worker():
        rc = None
        app_name = None
        tls_stdout.set_writer(writer)
        try:
            result = target(stop_event)
            if isinstance(result, tuple):
                app_name, rc = result
            else:
                rc = result
        except Exception as e:
            log_queue.put(f"[ERROR] {e}")
            rc = 1
        finally:
            writer.flush()
            tls_stdout.clear_writer()
            log_queue.put(None)  # 结束标记
            if on_done:
                log_panel.after(0, lambda: on_done(rc, app_name))

    # 启动日志轮询 (每 100ms 消费队列)
    def _poll():
        try:
            while True:
                try:
                    msg = log_queue.get_nowait()
                except queue.Empty:
                    break
                if msg is None:  # 结束标记
                    return
                level = "err" if msg.startswith("[ERROR]") or msg.startswith("[!") else "info"
                log_panel.log(msg, level)
        finally:
            pass
        if threading.main_thread().is_alive():
            log_panel.after(100, _poll)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    log_panel.after(100, _poll)
    return thread


# ---------------------------------------------------------------------------
# 全局样式 (clam 主题 + 自定义配色)
# ---------------------------------------------------------------------------

def _setup_style(root: tk.Tk) -> ttk.Style:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", font=("Microsoft YaHei UI", 9))
    style.configure("Title.TLabel", font=("Microsoft YaHei UI", 14, "bold"),
                    foreground="#1F3A68")
    style.configure("SubTitle.TLabel", font=("Microsoft YaHei UI", 10, "bold"),
                    foreground="#2D5DA1")
    style.configure("Hint.TLabel", foreground="#888888", font=("Microsoft YaHei UI", 8))
    style.configure("Card.TLabelframe", padding=10, relief="solid", borderwidth=0)
    style.configure("Card.TLabelframe.Label", font=("Microsoft YaHei UI", 9, "bold"),
                    foreground="#1F3A68")

    style.configure("Primary.TButton", padding=(14, 6), font=("Microsoft YaHei UI", 9, "bold"),
                    foreground="#FFFFFF", background="#2D5DA1", borderwidth=0)
    style.map("Primary.TButton",
              background=[("active", "#1F3A68"), ("pressed", "#172B52")])

    style.configure("Danger.TButton", padding=(14, 6), font=("Microsoft YaHei UI", 9),
                    foreground="#FFFFFF", background="#C0392B", borderwidth=0)
    style.map("Danger.TButton",
              background=[("active", "#922B21"), ("pressed", "#641E16")])

    style.configure("Ghost.TButton", padding=(12, 6), font=("Microsoft YaHei UI", 9))

    style.configure("Notebook.Tab", padding=(20, 8), font=("Microsoft YaHei UI", 10))

    style.configure("TCheckbutton", font=("Microsoft YaHei UI", 9))
    style.configure("TRadiobutton", font=("Microsoft YaHei UI", 9))
    style.configure("Horizontal.TProgressbar", thickness=16)

    return style


# ---------------------------------------------------------------------------
# 基础控件 helper
# ---------------------------------------------------------------------------

class _PathRow(ttk.Frame):
    """一行: 标签 + 输入框 + 浏览按钮 (文件或目录)"""

    def __init__(self, master, label: str, *, pick: str = "file",
                 filetypes=None, default: str = "", width: int = 50,
                 initialdir: str = ""):
        super().__init__(master)
        self._pick = pick
        self._filetypes = filetypes or [("所有文件", "*.*")]
        self.var = tk.StringVar(value=default)
        self._initialdir = initialdir

        ttk.Label(self, text=label, width=12, anchor="w").pack(side="left")
        self.entry = ttk.Entry(self, textvariable=self.var, width=width)
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(self, text="浏览…", style="Ghost.TButton",
                   command=self._on_browse).pack(side="left")

    def set_initialdir(self, path: str):
        """设置浏览按钮的初始打开目录"""
        self._initialdir = path

    def set_pick_type(self, pick: str):
        """动态切换文件/目录选择模式"""
        self._pick = pick

    def _on_browse(self):
        pick_type = self._pick
        init_dir = self._initialdir if self._initialdir and os.path.isdir(self._initialdir) else None
        if pick_type == "file":
            p = filedialog.askopenfilename(filetypes=self._filetypes, initialdir=init_dir)
        elif pick_type == "dir":
            p = filedialog.askdirectory(initialdir=init_dir)
        elif pick_type == "save":
            p = filedialog.asksaveasfilename(filetypes=self._filetypes,
                                             defaultextension=self._filetypes[0][1],
                                             initialdir=init_dir)
        else:
            p = ""
        if p:
            self.var.set(p)

    def get(self) -> str:
        return self.var.get().strip()

    def set(self, v: str):
        self.var.set(v)


class _LogPanel(ttk.LabelFrame):
    """带时间戳的滚动日志框 + 底部进度条"""

    def __init__(self, master, title: str = "实时日志", show_progress: bool = True):
        super().__init__(master, text=title, style="Card.TLabelframe", padding=8)
        self._show_progress = show_progress

        self.text = scrolledtext.ScrolledText(
            self, height=10, wrap="none",
            font=("Consolas", 9), bg="#0F172A", fg="#E2E8F0",
            insertbackground="#E2E8F0", relief="flat", state="disabled",
        )
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("ok", foreground="#22C55E")
        self.text.tag_configure("warn", foreground="#FBBF24")
        self.text.tag_configure("err", foreground="#F87171")
        self.text.tag_configure("info", foreground="#93C5FD")

        if show_progress:
            pbar_frame = ttk.Frame(self)
            pbar_frame.pack(fill="x", pady=(8, 0))
            self.progress = ttk.Progressbar(pbar_frame, mode="determinate",
                                            style="Horizontal.TProgressbar")
            self.progress.pack(side="left", fill="x", expand=True)
            self.status_var = tk.StringVar(value="就绪")
            ttk.Label(pbar_frame, textvariable=self.status_var, width=28,
                      anchor="e", style="Hint.TLabel").pack(side="left", padx=(8, 0))
        else:
            self.progress = None
            self.status_var = None
        self.stop_event = None  # 由外部设置

    def log(self, msg: str, level: str = "info"):
        ts = _dt.datetime.now().strftime("%H:%M:%S")
        tag = {"ok": "ok", "success": "ok", "warn": "warn", "warning": "warn",
               "err": "err", "error": "err"}.get(level.lower(), "info")
        line = f"[{ts}] {msg}\n"
        self.text.configure(state="normal")
        self.text.insert("end", line, tag)
        self.text.see("end")
        self.text.configure(state="disabled")
        try:
            self.update_idletasks()
        except Exception:
            pass

    def set_progress(self, value: int, total: int, text: Optional[str] = None):
        if not self._show_progress or self.progress is None:
            return
        pct = (value / total * 100) if total > 0 else 0
        self.progress.configure(maximum=total, value=value)
        if self.status_var:
            if text:
                self.status_var.set(text)
            else:
                self.status_var.set(f"{value}/{total}  ({pct:.0f}%)")



# ---------------------------------------------------------------------------
# Tab 1: 数据处理
# ---------------------------------------------------------------------------

class TabDataProcessing(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=12)

        # ---- 统一输入输出 ----
        io_frame = ttk.LabelFrame(self, text="输入 / 输出", style="Card.TLabelframe")
        io_frame.pack(fill="x")
        self.txt_path = _PathRow(io_frame, "输入:", pick="file",
                                 filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        self.txt_path.pack(fill="x", pady=4)
        self.out_dir = _PathRow(io_frame, "输出目录:", pick="dir")
        self.out_dir.pack(fill="x", pady=4)

        # ---- 子部分1: Jira 数据处理 ----
        jira_frame = ttk.LabelFrame(self, text="① Jira 数据处理", style="Card.TLabelframe")
        jira_frame.pack(fill="x", pady=(8, 0))

        self.v_jira = tk.BooleanVar(value=True)
        cb_jira = ttk.Checkbutton(jira_frame, text="启用 Jira 数据处理",
                                  variable=self.v_jira,
                                  command=self._on_toggle_jira)
        cb_jira.pack(anchor="w", pady=(4, 2))

        self._jira_content = ttk.Frame(jira_frame)
        self._jira_content.pack(fill="x", padx=(24, 0))

        mode_row = ttk.Frame(self._jira_content)
        mode_row.pack(fill="x", pady=2)
        ttk.Label(mode_row, text="模式:", width=12, anchor="w").pack(side="left")
        self.mode_var = tk.StringVar(value="jira")
        ttk.Radiobutton(mode_row, text="Jira 链接",
                        variable=self.mode_var, value="jira",
                        command=self._on_mode_change).pack(side="left", padx=(0, 16))
        ttk.Radiobutton(mode_row, text="视频路径",
                        variable=self.mode_var, value="video",
                        command=self._on_mode_change).pack(side="left", padx=(0, 16))
        ttk.Radiobutton(mode_row, text="批量复制",
                        variable=self.mode_var, value="batch",
                        command=self._on_mode_change).pack(side="left")

        opt_row = ttk.Frame(self._jira_content)
        opt_row.pack(fill="x", pady=2)
        ttk.Label(opt_row, text="并发数:", width=12, anchor="w").pack(side="left")
        self.workers_var = tk.IntVar(value=5)
        ttk.Spinbox(opt_row, from_=1, to=8, width=6,
                    textvariable=self.workers_var).pack(side="left")
        self.v_create_dir = tk.BooleanVar(value=False)
        self.v_classify = tk.BooleanVar(value=True)
        self.v_file_folder = tk.BooleanVar(value=True)
        self.v_keep_largest = tk.BooleanVar(value=True)
        # 通用选项 (所有模式可见)
        ttk.Checkbutton(opt_row, text="车型分类", variable=self.v_classify).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(opt_row, text="创建同名文件夹", variable=self.v_file_folder).pack(side="left", padx=(16, 0))
        # Jira 独有选项
        self._cb_create_dir = ttk.Checkbutton(opt_row, text="创建 Jira 子目录", variable=self.v_create_dir)
        self._cb_create_dir.pack(side="left", padx=(16, 0))
        self._cb_keep_largest = ttk.Checkbutton(opt_row, text="只保留最大后缀", variable=self.v_keep_largest)
        self._cb_keep_largest.pack(side="left", padx=(16, 0))

        # ---- 子部分2: ADAS 预处理 ----
        adas_frame = ttk.LabelFrame(self, text="② ADAS 预处理", style="Card.TLabelframe")
        adas_frame.pack(fill="x", pady=(8, 0))

        self.v_adas = tk.BooleanVar(value=False)
        ttk.Checkbutton(adas_frame, text="启用 ADAS 预处理",
                        variable=self.v_adas,
                        command=self._on_toggle_adas).pack(anchor="w", pady=(4, 2))

        self._adas_content = ttk.Frame(adas_frame)
        self._adas_content.pack(fill="x", padx=(24, 0))

        car_row = ttk.Frame(self._adas_content)
        car_row.pack(fill="x", pady=2)
        ttk.Label(car_row, text="确认车型:", width=12, anchor="w").pack(side="left")
        self.car_type_var = tk.StringVar(value="3")
        car_options = [
            "0 - gl8车 (竞品EQ4为UDP传输)",
            "1 - 拿铁车 (竞品EQ4为CAN传输)",
            "2 - 理想one车 (竞品J3为UDP传输)",
            "3 - 其他/商用车 (仅支持3.1协议解码库)",
            "4 - 客车 吉利-ss21 (AEB专用)",
            "5 - 客车 wuling-f510s (AEB专用)",
            "6 - 旧版商用车 (不了解请选3)",
            "7 - 东湖真值车",
            "8 - 其他 (支持3.0协议解码库)",
            "9 - 客车 nissan-p20n (AEB专用)",
        ]
        self.car_combo = ttk.Combobox(car_row, textvariable=self.car_type_var,
                                      state="readonly", width=42)
        self.car_combo["values"] = car_options
        # 默认选第3项 (index=3)
        self.car_combo.current(3)
        self.car_combo.pack(side="left")

        mcap_row = ttk.Frame(self._adas_content)
        mcap_row.pack(fill="x", pady=2)
        ttk.Label(mcap_row, text="生成mcap:", width=12, anchor="w").pack(side="left")
        self.mcap_var = tk.StringVar(value="否")
        ttk.Combobox(mcap_row, textvariable=self.mcap_var,
                     state="readonly", width=10,
                     values=["否", "是"]).pack(side="left")

        # ---- 按钮栏 ----
        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=12)
        ttk.Button(btns, text="▶ 开始执行", style="Primary.TButton",
                   command=self._on_start).pack(side="left")
        ttk.Button(btns, text="× 取消", style="Danger.TButton",
                   command=self._on_cancel).pack(side="left", padx=8)
        ttk.Button(btns, text="📂 打开输出目录", style="Ghost.TButton",
                   command=self._on_open_dir).pack(side="left")

        # 日志
        self.log_panel = _LogPanel(self, title="执行日志")
        self.log_panel.pack(fill="both", expand=True)
        self._thread = None
        self._stop_event = threading.Event()

    def _on_mode_change(self):
        """模式切换: 动态调整输入框类型和选项显隐"""
        mode = self.mode_var.get()
        if mode == "jira":
            self.txt_path.set_pick_type("file")
            self._cb_create_dir.pack(side="left", padx=(16, 0))
            self._cb_keep_largest.pack(side="left", padx=(16, 0))
        elif mode == "video":
            self.txt_path.set_pick_type("dir")
            self._cb_create_dir.pack_forget()
            self._cb_keep_largest.pack_forget()
        else:  # batch
            self.txt_path.set_pick_type("file")
            self._cb_create_dir.pack_forget()
            self._cb_keep_largest.pack_forget()

    def _on_toggle_jira(self):
        if self.v_jira.get():
            for child in self._jira_content.winfo_children():
                try:
                    child.configure(state="normal")
                except Exception:
                    pass
        else:
            for child in self._jira_content.winfo_children():
                try:
                    child.configure(state="disabled")
                except Exception:
                    pass

    def _on_toggle_adas(self):
        if self.v_adas.get():
            for child in self._adas_content.winfo_children():
                try:
                    child.configure(state="normal")
                except Exception:
                    pass
        else:
            for child in self._adas_content.winfo_children():
                try:
                    child.configure(state="disabled")
                except Exception:
                    pass

    def _on_start(self):
        run_jira = self.v_jira.get()
        run_adas = self.v_adas.get()

        if not run_jira and not run_adas:
            messagebox.showwarning("提示", "请至少启用一个子部分")
            return
        if not self.txt_path.get():
            messagebox.showwarning("提示", "请先选择输入文件")
            return
        if not self.out_dir.get():
            messagebox.showwarning("提示", "请先选择输出目录")
            return
        if self._thread and self._thread.is_alive():
            messagebox.showwarning("提示", "任务正在运行中")
            return

        txt_path = self.txt_path.get()
        output_dir = self.out_dir.get()
        max_workers = self.workers_var.get()

        # 解析车型: 取下拉框文本的数字部分
        car_type_str = self.car_type_var.get().strip()
        car_type = int(car_type_str.split(" - ")[0]) if car_type_str else 3
        generate_mcap = self.mcap_var.get() == "是"

        self._stop_event.clear()
        self.log_panel.text.configure(state="normal")
        self.log_panel.text.delete("1.0", "end")
        self.log_panel.text.configure(state="disabled")

        def _task(stop_event):
            if stop_event.is_set():
                return 1

            # 子部分1: Jira 数据处理
            if run_jira:
                from devboard_toolkit.data_preproc.pipeline import data_preproc_main
                mode_map = {"jira": "1", "video": "2", "batch": "3"}
                mode = mode_map.get(self.mode_var.get(), "1")
                create_jira_folder = self.v_create_dir.get()
                classify_category = self.v_classify.get()
                create_file_folder = self.v_file_folder.get()
                keep_largest_suffix = self.v_keep_largest.get()
                # Jira 处理阶段不做预处理,预处理由子部分2单独负责
                print("=" * 50)
                print("  ① Jira 数据处理")
                print("=" * 50)
                rc = data_preproc_main(
                    txt_path=txt_path,
                    output_dir=output_dir,
                    mode=mode,
                    create_jira_folder=create_jira_folder,
                    classify_category=classify_category,
                    run_preprocessing_flag=False,
                    max_workers=max_workers,
                    stop_event=stop_event,
                    create_file_folder=create_file_folder,
                    keep_largest_suffix=keep_largest_suffix,
                )
                if rc != 0:
                    if rc == 2:
                        print("[!] 已取消")
                    else:
                        print("[!] Jira 数据处理失败, 终止")
                    return rc
                if stop_event.is_set():
                    return 2

            # 子部分2: ADAS 预处理
            if run_adas:
                if stop_event.is_set():
                    print("[*] 已取消,跳过 ADAS 预处理")
                    return 2
                from devboard_toolkit.data_preproc.preprocessor import run_preprocessing
                print("\n" + "=" * 50)
                print("  ② ADAS 预处理")
                print("=" * 50)
                ok, msg = run_preprocessing(output_dir, car_type=car_type,
                                            generate_mcap=generate_mcap,
                                            stop_event=stop_event)
                if ok:
                    print(f"[+] {msg}")
                else:
                    print(f"[!] {msg}")
                    if stop_event.is_set():
                        return 2

            return 0

        def _on_done(rc, app_name):
            if rc == 0:
                self.log_panel.log("========== 数据处理完成 ==========", "ok")
            elif rc == 2:
                self.log_panel.log("========== 已取消 ==========", "warn")
            else:
                self.log_panel.log("========== 数据处理失败 ==========", "err")

        self._thread = _run_in_thread(_task, self.log_panel, self._stop_event, _on_done)

    def _on_cancel(self):
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            self.log_panel.log("用户请求取消, 剩余任务将在下次迭代时终止…", "warn")
        else:
            self.log_panel.log("没有正在运行的任务", "info")

    def _on_open_dir(self):
        p = self.out_dir.get()
        if p:
            p = os.path.normpath(p)
        if not p or not os.path.isdir(p):
            messagebox.showwarning("提示", "目录不存在,请先选择输出目录")
            return
        try:
            os.startfile(p)  # type: ignore[attr-defined]
        except Exception as e:
            self.log_panel.log(f"打开失败: {e}", "err")


# ---------------------------------------------------------------------------
# Tab 2: 感知包编译 (Jenkins 拉取+编译)
# ---------------------------------------------------------------------------

class TabJenkinsBuild(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=12)

        form = ttk.LabelFrame(self, text="参数配置", style="Card.TLabelframe")
        form.pack(fill="x")

        self.sdk_zip = _PathRow(form, "SDK zip:", pick="file",
                                filetypes=[("ZIP 文件", "*.zip"), ("所有文件", "*.*")])
        self.sdk_zip.pack(fill="x", pady=4)

        self.out_dir = _PathRow(form, "输出目录:", pick="dir")
        self.out_dir.pack(fill="x", pady=4)
        ttk.Label(form, text="留空则下载到 SDK zip 同目录",
                  style="Hint.TLabel").pack(anchor="w")

        # 按钮栏
        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=12)
        ttk.Button(btns, text="▶ 编译 & 下载", style="Primary.TButton",
                   command=self._on_start).pack(side="left")
        ttk.Button(btns, text="× 取消", style="Danger.TButton",
                   command=self._on_cancel).pack(side="left", padx=8)
        ttk.Button(btns, text="📂 打开输出目录", style="Ghost.TButton",
                   command=self._on_open_dir).pack(side="left")

        # 日志
        self.log_panel = _LogPanel(self, title="编译/下载日志")
        self.log_panel.pack(fill="both", expand=True)
        self._thread = None
        self._stop_event = threading.Event()

    def _on_start(self):
        sdk_zip = self.sdk_zip.get()
        if not sdk_zip:
            messagebox.showwarning("提示", "请先选择 SDK zip 文件")
            return
        if not os.path.isfile(sdk_zip):
            messagebox.showwarning("提示", f"SDK zip 不存在: {sdk_zip}")
            return
        if self._thread and self._thread.is_alive():
            messagebox.showwarning("提示", "任务正在运行中")
            return

        out_dir = self.out_dir.get() or None
        self._stop_event.clear()
        self.log_panel.text.configure(state="normal")
        self.log_panel.text.delete("1.0", "end")
        self.log_panel.text.configure(state="disabled")

        def _task(stop_event):
            project_root = _PROJECT_ROOT
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            from jenkins_build import auto_build_main
            if stop_event.is_set():
                return (None, 1)
            app_name, rc = auto_build_main(
                sdk_zip_path=sdk_zip,
                replay_dir=out_dir,
            )
            return (app_name, rc)

        def _on_done(rc, app_name):
            if rc == 0 and app_name:
                self.log_panel.log("========== 编译成功 ==========", "ok")
                self.log_panel.log(f"感知包名: {app_name}", "ok")
            else:
                self.log_panel.log("========== 编译失败或被取消 ==========", "err")

        self._thread = _run_in_thread(_task, self.log_panel, self._stop_event, _on_done)

    def _on_cancel(self):
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            self.log_panel.log("用户请求取消… (注意: Jenkins 构建可能继续在服务器端运行)", "warn")
        else:
            self.log_panel.log("没有正在运行的任务", "info")

    def _on_open_dir(self):
        p = self.out_dir.get()
        if p:
            p = os.path.normpath(p)
        if p and os.path.isdir(p):
            try:
                os.startfile(p)  # type: ignore[attr-defined]
            except Exception as e:
                self.log_panel.log(f"打开失败: {e}", "err")
        else:
            messagebox.showwarning("提示", "请先选择输出目录")


# ---------------------------------------------------------------------------
# Tab 3: 自动回灌 (生成 SDK/列表回灌脚本,SSH 流式执行)
# ---------------------------------------------------------------------------

class TabFeedback(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=12)

        self._thread = None
        self._stop_event = threading.Event()
        self._unc_testbed = ""
        self._linux_testbed_base = ""
        self._replay_folder = ""
        self._idle_boards = []

        # === 多任务并发: 共享板池 + 任务注册表 ===
        import threading as _th
        self._tasks = {}               # {task_id: {"thread", "stop_event", "status", ...}}
        self._busy_boards = set()      # 所有任务占用的板名集合 (跨任务)
        self._available_pool = []      # 共享空闲板池 (跨任务)
        self._pool_lock = _th.Lock()   # 保护板池读写
        self._rescan_lock = _th.Lock()  # 保证增量检测同一时刻只跑一个
        self._shared_last_rescan = time.time()
        self._task_counter = 0
        self._board_first_run = {}   # {板名: 是否首次启动 (跨任务共享, True=需reboot)

        form = ttk.LabelFrame(self, text="回灌配置", style="Card.TLabelframe")
        form.pack(fill="x")

        row_env = ttk.Frame(form)
        row_env.pack(fill="x", pady=4)
        ttk.Label(row_env, text="回灌环境:", width=12, anchor="w").pack(side="left")
        self.env_var = tk.StringVar()
        self.env_combo = ttk.Combobox(row_env, textvariable=self.env_var,
                                       state="readonly", width=56)
        self.env_combo.pack(side="left")
        self.env_combo.bind("<<ComboboxSelected>>", self._on_select_env)
        ttk.Button(row_env, text="\U0001f504 刷新", style="Ghost.TButton",
                   command=self._on_scan_envs).pack(side="left", padx=(8, 0))

        row_pkg = ttk.Frame(form)
        row_pkg.pack(fill="x", pady=4)
        ttk.Label(row_pkg, text="感知包:", width=12, anchor="w").pack(side="left")
        self.pkg_var = tk.StringVar()
        self.pkg_combo = ttk.Combobox(row_pkg, textvariable=self.pkg_var,
                                       state="readonly", width=56)
        self.pkg_combo.pack(side="left")

        row_cal = ttk.Frame(form)
        row_cal.pack(fill="x", pady=4)
        ttk.Label(row_cal, text="fcf标定文件:", width=12, anchor="w").pack(side="left")
        self.cal_var = tk.StringVar()
        self.cal_combo = ttk.Combobox(row_cal, textvariable=self.cal_var,
                                       state="readonly", width=56)
        self.cal_combo.pack(side="left")

        row_car = ttk.Frame(form)
        row_car.pack(fill="x", pady=4)
        ttk.Label(row_car, text="车型/标定:", width=12, anchor="w").pack(side="left")
        self.car_var = tk.StringVar()
        self.car_combo = ttk.Combobox(row_car, textvariable=self.car_var,
                                       state="readonly", width=56)
        self.car_combo.pack(side="left")

        row_board = ttk.Frame(form)
        row_board.pack(fill="x", pady=4)
        ttk.Label(row_board, text="开发板数量:", width=12, anchor="w").pack(side="left")
        self.board_count = tk.IntVar(value=1)
        self.board_spin = ttk.Spinbox(row_board, from_=1, to=6,
                                       textvariable=self.board_count, width=5)
        self.board_spin.pack(side="left")
        ttk.Button(row_board, text="\U0001f50d 检测空闲板", style="Ghost.TButton",
                   command=self._on_detect_boards).pack(side="left", padx=(8, 0))
        self.use_online_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row_board, text="使用线上开发板",
                        variable=self.use_online_var).pack(side="left", padx=(16, 0))

        nb = ttk.Notebook(form)
        nb.pack(fill="x", pady=(12, 4))

        today = _dt.datetime.now().strftime("%Y%m%d")

        sdk_tab = ttk.Frame(nb)
        nb.add(sdk_tab, text=" SDK回灌 ")

        row_sdk_user = ttk.Frame(sdk_tab)
        row_sdk_user.pack(fill="x", pady=4)
        ttk.Label(row_sdk_user, text="用户名:", width=14, anchor="w").pack(side="left")
        self.sdk_user_var = tk.StringVar()
        ttk.Entry(row_sdk_user, textvariable=self.sdk_user_var, width=30).pack(
            side="left", fill="x", expand=True)

        row_sdk_date = ttk.Frame(sdk_tab)
        row_sdk_date.pack(fill="x", pady=4)
        ttk.Label(row_sdk_date, text="日期:", width=14, anchor="w").pack(side="left")
        self.sdk_date_var = tk.StringVar(value=today)
        ttk.Entry(row_sdk_date, textvariable=self.sdk_date_var, width=30).pack(
            side="left", fill="x", expand=True)
        ttk.Label(row_sdk_date, text="(自动填今天)",
                  style="Hint.TLabel").pack(side="left", padx=(8, 0))

        row_sdk_path = ttk.Frame(sdk_tab)
        row_sdk_path.pack(fill="x", pady=4)
        ttk.Label(row_sdk_path, text="素材相对路径:", width=14, anchor="w").pack(side="left")
        self.sdk_path_var = tk.StringVar()
        ttk.Entry(row_sdk_path, textvariable=self.sdk_path_var, width=30).pack(
            side="left", fill="x", expand=True)
        ttk.Label(row_sdk_path, text="例: 20260810/0452 或 20260810\\0452",
                  style="Hint.TLabel").pack(side="left", padx=(8, 0))

        # 视频路径 (智能检测: 已预处理 → 列表回灌, 未预处理 → 复制+预处理+SDK回灌)
        row_sdk_video = ttk.Frame(sdk_tab)
        row_sdk_video.pack(fill="x", pady=4)
        ttk.Label(row_sdk_video, text="视频路径:", width=14, anchor="w").pack(side="left")
        self.sdk_video_var = tk.StringVar()
        ttk.Entry(row_sdk_video, textvariable=self.sdk_video_var, width=30).pack(
            side="left", fill="x", expand=True)
        def _browse_sdk_video():
            init_dir = self._unc_testbed or os.getcwd()
            p = filedialog.askdirectory(
                title="选择视频路径 (含 .h265/.h264 的目录)",
                initialdir=init_dir)
            if p:
                self.sdk_video_var.set(p)
        ttk.Button(row_sdk_video, text="浏览…", width=8,
                   command=_browse_sdk_video).pack(side="left", padx=(4, 0))
        ttk.Label(row_sdk_video, text="(可选, 填后自动检测预处理状态)",
                  style="Hint.TLabel").pack(side="left", padx=(8, 0))

        list_tab = ttk.Frame(nb)
        nb.add(list_tab, text=" 列表回灌 ")

        row_list_user = ttk.Frame(list_tab)
        row_list_user.pack(fill="x", pady=4)
        ttk.Label(row_list_user, text="用户名:", width=14, anchor="w").pack(side="left")
        self.list_user_var = tk.StringVar()
        ttk.Entry(row_list_user, textvariable=self.list_user_var, width=30).pack(
            side="left", fill="x", expand=True)

        row_list_date = ttk.Frame(list_tab)
        row_list_date.pack(fill="x", pady=4)
        ttk.Label(row_list_date, text="日期:", width=14, anchor="w").pack(side="left")
        self.list_date_var = tk.StringVar(value=today)
        ttk.Entry(row_list_date, textvariable=self.list_date_var, width=30).pack(
            side="left", fill="x", expand=True)
        ttk.Label(row_list_date, text="(自动填今天)",
                  style="Hint.TLabel").pack(side="left", padx=(8, 0))

        row_list_input_mode = ttk.Frame(list_tab)
        row_list_input_mode.pack(fill="x", pady=(4, 0))
        ttk.Label(row_list_input_mode, text="素材输入:", width=14, anchor="w").pack(side="left")
        self._list_input_mode = tk.StringVar(value="txt")
        ttk.Radiobutton(row_list_input_mode, text="txt 文件",
                        variable=self._list_input_mode, value="txt",
                        command=self._on_list_input_change).pack(side="left", padx=(0, 16))
        ttk.Radiobutton(row_list_input_mode, text="视频路径",
                        variable=self._list_input_mode, value="video",
                        command=self._on_list_input_change).pack(side="left")
        self.list_txt_row = _PathRow(list_tab, "  选择:", pick="file",
                                     filetypes=[("文本文件", "*.txt"),
                                                ("所有文件", "*.*")])
        self.list_txt_row.pack(fill="x", pady=(2, 4))

        self._mode_var = tk.StringVar(value="sdk")
        nb.bind("<<NotebookTabChanged>>",
                lambda e: self._mode_var.set("sdk" if nb.index("current") == 0 else "list"))

        self.script_path = tk.StringVar()

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=12)
        ttk.Button(btns, text="\u25b6 生成脚本 & 启动回灌", style="Primary.TButton",
                   command=self._on_start).pack(side="left")
        ttk.Button(btns, text="\u00d7 停止", style="Danger.TButton",
                   command=self._on_stop).pack(side="left", padx=8)
        ttk.Label(btns, text="停止任务:").pack(side="left", padx=(4, 0))
        self._stop_task_var = tk.StringVar()
        self._stop_task_combo = ttk.Combobox(btns, textvariable=self._stop_task_var,
                                             state="readonly", width=12)
        self._stop_task_combo.pack(side="left", padx=2)
        ttk.Button(btns, text="\U0001f4c2 打开回灌目录", style="Ghost.TButton",
                   command=self._on_open_dir).pack(side="left")
        self.v_delete_scripts = tk.BooleanVar(value=False)
        ttk.Checkbutton(btns, text="回灌结束自动删除脚本",
                        variable=self.v_delete_scripts).pack(side="left", padx=(16, 0))

        self.log_panel = _LogPanel(self, title="回灌日志", show_progress=False)
        self.log_panel.pack(fill="both", expand=True)

        self.after(100, self._on_scan_envs)

    def _on_scan_envs(self):
        def _task():
            try:
                from devboard_toolkit.config import load_replay_env
                from devboard_toolkit.batch_replay import _derive_paths, _list_folders

                env = load_replay_env()
                self._unc_testbed, self._linux_testbed_base = _derive_paths(env)
                self.log_panel.log(f"扫描回灌环境: {self._unc_testbed}", "info")
                folders = _list_folders(self._unc_testbed)
                self.after(0, lambda: self._update_env_combo(folders))
            except FileNotFoundError as e:
                self.after(0, lambda: self.log_panel.log(str(e), "err"))
            except Exception as e:
                self.after(0, lambda: self.log_panel.log(f"扫描回灌环境失败: {e}", "err"))
        threading.Thread(target=_task, daemon=True).start()

    def _update_env_combo(self, folders):
        if not folders:
            self.log_panel.log("testbed 下没有子文件夹", "warn")
            self.env_combo["values"] = []
            return
        self.env_combo["values"] = folders
        self.env_var.set(folders[0])
        self._on_select_env()

    def _on_select_env(self, event=None):
        env_name = self.env_var.get()
        if not env_name or not self._unc_testbed:
            return
        self._replay_folder = env_name
        replay_dir = os.path.normpath(os.path.join(self._unc_testbed, env_name))
        self.log_panel.log(f"选中回灌环境: {env_name}", "info")

        # 列表回灌浏览默认打开回灌环境路径
        self.list_txt_row.set_initialdir(replay_dir)

        try:
            from devboard_toolkit.batch_replay import _find_perception_pkgs
            pkgs = _find_perception_pkgs(replay_dir)
            self.pkg_combo["values"] = pkgs
            if pkgs:
                self.pkg_var.set(pkgs[0])
                self.log_panel.log(f"发现感知包: {', '.join(pkgs)}", "ok")
            else:
                self.log_panel.log("未发现感知包 (NH_ADAS_PERCEPTION_*)", "warn")
                self.pkg_var.set("")
        except Exception as e:
            self.log_panel.log(f"扫描感知包失败: {e}", "err")

        try:
            cal_files = []
            try:
                from devboard_toolkit.batch_replay import _project_tool_dir
                tool_dir = _project_tool_dir()
                fcf_root = os.path.join(tool_dir, "fcf_calibration")
                if os.path.isdir(fcf_root):
                    versions = sorted([d for d in os.listdir(fcf_root)
                                       if os.path.isdir(os.path.join(fcf_root, d))])
                    if "default" in versions:
                        versions.remove("default")
                        versions.insert(0, "default")
                    cal_files = versions
            except Exception:
                pass

            self.cal_combo["values"] = cal_files
            if cal_files:
                self.cal_var.set("default" if "default" in cal_files else cal_files[0])
            self.log_panel.log(f"fcf标定版本: {', '.join(cal_files) if cal_files else '未检测到'}", "info")
        except Exception as e:
            self.log_panel.log(f"扫描fcf标定文件失败: {e}", "err")

        try:
            from devboard_toolkit.config import load_car_models
            car_models = load_car_models()
            if car_models:
                self.car_combo["values"] = list(car_models.keys())
                self.car_var.set(list(car_models.keys())[0])
        except Exception:
            pass

    def _on_list_input_change(self):
        mode = self._list_input_mode.get()
        if mode == "txt":
            self.list_txt_row.set_pick_type("file")
        else:
            self.list_txt_row.set_pick_type("dir")

    # ================================================================
    # 多任务并发: 共享板池操作 (所有方法加锁)
    # ================================================================

    def _pool_take(self, n):
        """从共享板池取最多 n 块板 (加锁). 取到的板标记为 busy."""
        with self._pool_lock:
            taken = self._available_pool[:n]
            self._available_pool = self._available_pool[len(taken):]
            self._busy_boards.update(taken)
            return taken

    def _pool_return_batch(self, boards):
        """将板还回共享板池 (加锁). 同时从 busy 集合移除."""
        with self._pool_lock:
            for bn in boards:
                self._busy_boards.discard(bn)
                if bn not in self._available_pool:
                    self._available_pool.append(bn)

    def _pool_add(self, boards):
        """将新发现的空闲板加入共享板池 (加锁), 不重复加入."""
        with self._pool_lock:
            for bn in boards:
                if bn not in self._available_pool and bn not in self._busy_boards:
                    self._available_pool.append(bn)

    def _pool_remove(self, board_name):
        """从空闲池移除指定板 (加锁). 用于检测到空闲池中的板已变忙."""
        with self._pool_lock:
            if board_name in self._available_pool:
                self._available_pool.remove(board_name)

    def _pool_size(self):
        """获取当前空闲板池大小 (加锁)."""
        with self._pool_lock:
            return len(self._available_pool)

    def _pool_snapshot(self):
        """获取当前空闲板池快照 (加锁)."""
        with self._pool_lock:
            return list(self._available_pool)

    def _busy_snapshot(self):
        """获取当前占用板快照 (加锁)."""
        with self._pool_lock:
            return set(self._busy_boards)

    def _sync_detect_boards(self, stop_event, exclude_busy=False):
        """同步检测空闲板 (供 _do_start_task 内部在每轮回灌前调用)

        Args:
            stop_event: 取消事件
            exclude_busy: True 时跳过已被其他任务占用的板 (多任务并发模式)
        Returns:
            空闲板名列表 (已排序). 失败返回空 list
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from devboard_toolkit.config import load_boards
        from devboard_toolkit.usage_check import check_usage_one

        boards = load_boards()
        if not boards:
            print("[!] 没有已配置开发板")
            return []

        # 按板名过滤: 线上板 (Online*) 需 use_online_var 勾选才参与检测
        use_online = bool(self.use_online_var.get())
        names = [n for n in boards.keys()
                 if use_online or not n.lower().startswith("online")]
        # 多任务模式: 排除已被其他任务占用的板
        if exclude_busy:
            busy = self._busy_snapshot()
            names = [n for n in names if n not in busy]
        if not names:
            print("[!] 当前过滤条件下没有可检测的开发板 (线上板需勾选)")
            return []

        results_map = {}
        tag_extra = f", 线上板=开({len([n for n in names if n.lower().startswith('online')])}块)" if use_online else ", 线上板=关"
        print(f"\n开发板检测 ({len(names)} 块{tag_extra}, 并行检测中...):")
        print(f"  {'板名':<10s}{'地址':<18s}{'状态'}")
        print("  " + "-" * 50)
        with ThreadPoolExecutor(max_workers=len(names)) as ex:
            futs = {ex.submit(check_usage_one, n, boards[n]): n for n in names}
            for fut in as_completed(futs):
                if stop_event is not None and stop_event.is_set():
                    return []
                r = fut.result()
                results_map[r.name] = r
                tag = "空闲" if not r.busy else "使用中"
                print(f"  {r.name:<10s}{r.host:<18s}{tag}")

        order = {n: i for i, n in enumerate(names)}
        results = sorted(results_map.values(), key=lambda r: order.get(r.name, 999))

        idle = [r.name for r in results if not r.busy]
        if idle:
            print(f"\n[+] 检测到 {len(idle)} 块空闲板: {', '.join(idle)}")
        else:
            print("\n[!] 没有空闲开发板")
        return idle

    def _wait_all_boards_done(self, board_names, log_dir, stop_event,
                               poll_interval=5, timeout_sec=86400):
        """轮询检测所有板都写完 {板名}.done 标记文件

        Returns:
            True 全部完成, False 被取消或超时
        """
        safe_names = [n.replace("/", "_").replace("\\", "_") for n in board_names]
        elapsed = 0
        done_paths = [os.path.join(log_dir, f"{s}.done") for s in safe_names]
        while True:
            if stop_event is not None and stop_event.is_set():
                print("\n[!] 用户取消,终止等待回灌完成")
                return False
            if elapsed > timeout_sec:
                print(f"\n[!] 等待回灌完成超时 ({timeout_sec}s),已放弃")
                return False
            # 全部 done 文件存在 → OK
            all_done = True
            pending = []
            for b, dp in zip(board_names, done_paths):
                if not os.path.isfile(dp):
                    all_done = False
                    pending.append(b)
            if all_done:
                print("\n[+] 所有板回灌完成")
                return True
            # 打印一次进度
            print(f"    等待回灌完成... {len(board_names)-len(pending)}/{len(board_names)} 板完成 "
                  f"(剩余: {', '.join(pending)}), 已等 {elapsed}s")
            time.sleep(poll_interval)
            elapsed += poll_interval

    def _on_detect_boards(self):
        if self._thread and self._thread.is_alive():
            self.log_panel.log("任务正在运行中", "warn")
            return

        self.log_panel.text.configure(state="normal")
        self.log_panel.text.delete("1.0", "end")
        self.log_panel.text.configure(state="disabled")
        self.log_panel.log("开始检测开发板使用状态...", "info")

        def _task(stop_event):
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from devboard_toolkit.config import load_boards
            from devboard_toolkit.usage_check import check_usage_one

            boards = load_boards()
            if not boards:
                print("[!] 没有已配置开发板")
                return 1

            # 按板名过滤: 线上板 (Online*) 需 use_online_var 勾选才参与检测
            use_online = bool(self.use_online_var.get())
            names = [n for n in boards.keys()
                     if use_online or not n.lower().startswith("online")]
            if not names:
                print("[!] 当前过滤条件下没有可检测的开发板 (线上板需勾选)")
                return 1

            results_map = {}
            tag_extra = f", 线上板=开({len([n for n in names if n.lower().startswith('online')])}块)" if use_online else ", 线上板=关"
            print(f"\n开发板检测 ({len(names)} 块{tag_extra}, 并行检测中...):")
            print(f"  {'板名':<10s}{'地址':<18s}{'状态'}")
            print("  " + "-" * 50)
            with ThreadPoolExecutor(max_workers=len(names)) as ex:
                futs = {ex.submit(check_usage_one, n, boards[n]): n for n in names}
                for fut in as_completed(futs):
                    results_map[futs[fut]] = fut.result()
                    r = fut.result()
                    tag = "空闲" if not r.busy else "使用中"
                    print(f"  {r.name:<10s}{r.host:<18s}{tag}")

            order = {n: i for i, n in enumerate(names)}
            results = sorted(results_map.values(), key=lambda r: order.get(r.name, 999))

            idle = [r.name for r in results if not r.busy]
            self._idle_boards = idle
            if idle:
                n_idle = len(idle)
                self.after(0, lambda: self._update_board_spin(n_idle))
                print(f"\n[+] 检测到 {n_idle} 块空闲板: {', '.join(idle)}")
            else:
                print("\n[!] 没有空闲开发板")
            return 0

        def _on_done(rc, app_name):
            if rc == 0:
                self.log_panel.log("========== 检测完成 ==========", "ok")
            else:
                self.log_panel.log("========== 检测失败 ==========", "err")

        self._thread = _run_in_thread(_task, self.log_panel, self._stop_event, _on_done)

    def _update_board_spin(self, n_idle: int):
        """检测到空闲板后更新 Spinbox 上限和当前值"""
        self.board_spin.configure(to=max(n_idle, 1))
        self.board_count.set(max(n_idle, 1))

    def _on_start(self):
        # === 多任务并发: 不再阻塞, 允许同时启动多个回灌任务 ===
        env_name = self.env_var.get()
        if not env_name:
            messagebox.showwarning("提示", "请先选择回灌环境")
            return
        if not self._unc_testbed:
            messagebox.showwarning("提示", "回灌环境尚未扫描完成,请稍后再试")
            return

        # 主线程只做最小校验: 板数>=1 (真正的空闲板检测放在任务线程内,避免GUI卡住)
        n = self.board_count.get()
        if n < 1:
            messagebox.showwarning("提示", "开发板数量至少为 1")
            return

        mode = self._mode_var.get()
        input_subpath = ""
        list_input_path = ""

        if mode == "list":
            list_input_path = self.list_txt_row.get()
            list_mode = self._list_input_mode.get()
            if not list_input_path:
                messagebox.showwarning("提示", "请选择素材输入")
                return
            if list_mode == "txt":
                if not os.path.isfile(list_input_path):
                    messagebox.showwarning("提示", "请选择有效的素材 txt 文件")
                    return
            else:  # video
                if not os.path.isdir(list_input_path):
                    messagebox.showwarning("提示", "请选择有效的视频路径文件夹")
                    return
            user = self.list_user_var.get().strip()
            date = self.list_date_var.get().strip()
            if not user:
                messagebox.showwarning("提示", "请输入用户名")
                return
            if not date:
                messagebox.showwarning("提示", "请输入日期")
                return
        else:
            # SDK 模式
            user = self.sdk_user_var.get().strip()
            date = self.sdk_date_var.get().strip()
            input_subpath = self.sdk_path_var.get().strip().replace("\\", "/").strip("/")
            sdk_video_path = self.sdk_video_var.get().strip()
            if not user:
                messagebox.showwarning("提示", "请输入用户名")
                return
            if not date:
                messagebox.showwarning("提示", "请输入日期")
                return
            # 视频路径有值时, 素材相对路径可选 (仅未预处理分支需要)
            if not sdk_video_path and not input_subpath:
                messagebox.showwarning("提示", "请输入素材相对路径或视频路径")
                return
            if sdk_video_path and not os.path.isdir(sdk_video_path):
                messagebox.showwarning("提示", f"视频路径不存在: {sdk_video_path}")
                return

        pkg_name = self.pkg_var.get().strip()
        if not pkg_name:
            messagebox.showwarning("提示", "请先选择感知包 (回灌环境中未检测到)")
            return

        car_model = self.car_var.get().strip()
        if not car_model:
            messagebox.showwarning("提示", "请选择车型")
            return

        # === 创建任务上下文 (快照当前 GUI 配置, 不再存 self 属性) ===
        self._task_counter += 1
        task_id = f"T{self._task_counter}"
        stop_event = threading.Event()
        prefix = f"[{task_id}] "

        list_mode_val = self._list_input_mode.get() if hasattr(self, "_list_input_mode") else "txt"

        task_ctx = {
            "task_id": task_id,
            "mode": mode,
            "n": n,
            "user": user,
            "date": date,
            "input_subpath": input_subpath if mode == "sdk" else "",
            "sdk_video_path": sdk_video_path if mode == "sdk" else "",
            "pkg_name": pkg_name,
            "car_model": car_model,
            "fcf_version": self.cal_var.get().strip(),
            "replay_folder": env_name,
            "unc_testbed": self._unc_testbed,
            "linux_testbed_base": self._linux_testbed_base,
            "list_input_mode": list_mode_val,
            "list_input_path": list_input_path,
            "delete_script": self.v_delete_scripts.get(),
        }

        # 仅第一个任务时清空日志面板 (后续任务追加输出)
        if not self._tasks:
            self.log_panel.text.configure(state="normal")
            self.log_panel.text.delete("1.0", "end")
            self.log_panel.text.configure(state="disabled")

        self.log_panel.log(f"{prefix}任务已启动 (模式={mode}, 环境={env_name}, 车型={car_model})", "info")

        def _task(stop_event):
            try:
                return self._do_start_task(task_ctx, stop_event)
            except Exception as e:
                print(f"[ERROR] {e}")
                import traceback
                traceback.print_exc()
                return 1

        def _on_done(rc, _a):
            if task_id in self._tasks:
                self._tasks[task_id]["status"] = "done" if rc == 0 else "failed"
            if rc == 0:
                self.log_panel.log(f"{prefix}========== 回灌完成 ==========", "ok")
            else:
                self.log_panel.log(f"{prefix}========== 回灌失败或被取消 ==========", "err")
            # 清理已完成任务
            if task_id in self._tasks:
                del self._tasks[task_id]
            self._refresh_stop_combo()

        thread = _run_in_thread(_task, self.log_panel, stop_event, _on_done, prefix=prefix)
        self._tasks[task_id] = {
            "thread": thread,
            "stop_event": stop_event,
            "status": "running",
            "ctx": task_ctx,
        }
        self._refresh_stop_combo()

    def _do_start_task(self, ctx, stop_event):
        from pathlib import Path
        from devboard_toolkit.config import (
            load_boards, load_car_models,
            load_replay_list_template, load_replay_sdk_template,
            load_replay_env,
        )
        from devboard_toolkit.batch_replay import _split_txt, _gen_one_script
        from devboard_toolkit.script_gen import _render_template, _extract_suffix
        from devboard_toolkit.ssh_client import build_client, safe_close

        boards = load_boards()
        if not boards:
            print("[!] 没有已配置开发板")
            return 1

        env = load_replay_env()
        mount_source = env.get("mount_source", "")
        mount_point = env.get("mount_point", "/mnt")
        mount_options = env.get("mount_options", "")

        replay_folder = ctx["replay_folder"]
        unc_replay_folder = os.path.normpath(
            os.path.join(ctx["unc_testbed"], replay_folder))
        linux_replay_folder = f"{ctx['linux_testbed_base']}/{replay_folder}"

        car_models = load_car_models()
        calibration = car_models.get(ctx["car_model"], ctx["car_model"])

        suffix = _extract_suffix(ctx["pkg_name"])
        print(f"[*] 感知包: {ctx['pkg_name']}")
        print(f"[*] 后缀: {suffix}")
        print(f"[*] 车型: {ctx['car_model']} / 标定: {calibration}")
        print(f"[*] 日期: {ctx['date']}")
        print(f"[*] 回灌目录(UNC): {unc_replay_folder}")
        print(f"[*] 回灌目录(板端): {linux_replay_folder}")

        # === 启动前空闲板检测 (放在任务线程,不阻塞 GUI) + 动态修正 ctx["n"] ===
        _idle_n = 0
        try:
            idle_init = self._sync_detect_boards(stop_event, exclude_busy=True)
            if idle_init:
                self._pool_add(idle_init)
                for _bn in idle_init:
                    self._board_first_run[_bn] = True
                _idle_n = len(idle_init)
                # 更新 GUI Spinbox 上限
                try:
                    self._update_board_spin(_idle_n)
                except Exception:
                    pass
                print(f"  [*] 启动前检测到 {_idle_n} 块空闲板: {', '.join(idle_init)}")
            else:
                print("  [!] 启动前未检测到空闲板 (后续增量检测会补充)")
        except Exception as _e:
            print(f"  [!] 启动前板检测异常: {_e}")
            idle_init = []

        # 修正 ctx["n"]: 用户选择不能超过实际空闲板数 (避免下游 L1868 min(ctx["n"]) 卡死)
        if _idle_n > 0:
            _orig_n = ctx["n"]
            ctx["n"] = min(_orig_n, _idle_n)
            if ctx["n"] != _orig_n:
                print(f"  [i] 板数自动调整: {_orig_n} → {ctx['n']} (不超过空闲板数)")
        if ctx["n"] < 1:
            # 没检测到板但用户填了,给一个至少为 1 的安全下限,下游会再次根据空闲池微调
            ctx["n"] = max(ctx["n"], 1)

        # fcf 标定覆盖: 用户选了非 default 版本时,复制覆盖回灌目录中的标定文件
        fcf_ver = ctx["fcf_version"]
        if fcf_ver and fcf_ver != "default":
            try:
                from devboard_toolkit.batch_replay import _project_tool_dir
                import shutil
                tool_dir = _project_tool_dir()
                fcf_src = os.path.join(tool_dir, "fcf_calibration", fcf_ver)
                if os.path.isdir(fcf_src):
                    for fname in ("vehConfig.json", "vruConfig.json"):
                        src_f = os.path.join(fcf_src, fname)
                        dst_f = os.path.join(unc_replay_folder, fname)
                        if os.path.isfile(src_f):
                            shutil.copy2(src_f, dst_f)
                            print(f"[*] fcf标定覆盖: {fname} ← {fcf_ver}")
                    print(f"[+] fcf标定已覆盖为 {fcf_ver}")
                else:
                    print(f"[!] fcf标定版本目录不存在: {fcf_src}, 跳过覆盖")
            except Exception as e:
                print(f"[!] fcf标定覆盖失败: {e}")
        else:
            print(f"[*] fcf标定: default (不覆盖)")

        vars_map = {
            "APP_PATH": ctx["pkg_name"],
            "APP_SUFFIX": suffix,
            "USER": ctx["user"],
            "DATE": ctx["date"],
            "CAR_MODEL": ctx["car_model"],
            "CALIBRATION": calibration,
        }

        delete_script = ctx["delete_script"]
        log_dir = os.path.join(unc_replay_folder, "logs")

        if ctx["mode"] == "list":
            template = load_replay_list_template()
            if not template:
                print("[!] config.yaml 中未找到 replay_list_template")
                return 1

            list_mode = ctx["list_input_mode"]
            list_input_path = ctx["list_input_path"]

            # ==== Step A: 视频路径模式 → 先区分车型, 生成多 txt ====
            if list_mode == "video":
                from devboard_toolkit.classify_by_car import classify_by_car
                print(f"\n{'=' * 60}")
                print("  [A] 区分车型 (视频路径 → 多 txt)")
                print(f"{'=' * 60}")
                sorted_txts = classify_by_car(
                    src_root=list_input_path,
                    out_dir=unc_replay_folder,
                    user=ctx["user"],
                    unc_prefix=ctx["unc_testbed"],
                    linux_prefix=ctx["linux_testbed_base"],
                    max_workers=32,
                )
                if not sorted_txts:
                    print("[!] 没有可回灌的分类 txt, 结束")
                    return 1
            else:
                sorted_txts = [list_input_path]

            # ==== Step B: 流水线式 txt 回灌 (自适应切分 + 动态调度 + 增量检测) ====
            BOARD_RESCAN_INTERVAL = 60  # 增量检测间隔: 1 分钟

            print(f"\n{'=' * 60}")
            print(f"  [B] 回灌队列表 (共 {len(sorted_txts)} 个 txt, 流水线模式)")
            print(f"  [B] 增量检测空闲板: 每 {BOARD_RESCAN_INTERVAL}s 一次")
            print(f"{'=' * 60}")
            for i, tp in enumerate(sorted_txts, 1):
                try:
                    n_line = sum(1 for _ in open(tp, encoding="utf-8") if _.strip())
                except Exception:
                    n_line = 0
                print(f"  {i}. {os.path.basename(tp)}  ({n_line} 条)")

            # 初始检测空闲板 (排除已被其他任务占用的板)
            print(f"\n[*] 检测空闲板 (排除已被其他任务占用的板)...")
            idle = self._sync_detect_boards(stop_event, exclude_busy=True)
            if stop_event.is_set():
                return 2
            if not idle:
                print("[!] 没有空闲板, 结束回灌")
                return 1
            self._pool_add(idle)
            print(f"[+] 检测到 {len(idle)} 块空闲板, 已加入共享板池 (当前池大小: {self._pool_size()})")

            os.makedirs(log_dir, exist_ok=True)

            queue = list(sorted_txts)      # 待回灌队列
            # running_txts 结构:
            # {txt_path: {
            #     "boards": [...],                  # 当前在跑的板
            #     "round": N,
            #     "per_target": N,
            #     "pending": [(script_path, sub_file, line_cnt), ...],  # 剩余待接力的分片队列
            #     "vars_map": {...},                # 复用的参数映射
            #     "car_model": str,                 # 复用的车型
            #     "suffix": str,                    # 复用的后缀
            #     "delete_script": bool,
            #     "log_dir": str,
            #     "replay_folder": str,
            # }}
            running_txts = {}
            round_counter = 0

            def _start_one_txt(txt_path, boards_to_use):
                """启动单个 txt 的回灌 (自适应切分+生成脚本+启动终端)
                返回: (ok, per_target, pending_list, extra_ctx_dict)
                  pending_list: 剩余未启动的分片 [(script_path, sub_file, line_cnt), ...]
                  extra_ctx_dict: 用于后续接力跑的上下文 (vars_map, car_model, suffix, ...)
                """
                nonlocal round_counter
                round_counter += 1
                round_idx = round_counter

                try:
                    total_n = sum(1 for _ in open(txt_path, encoding="utf-8") if _.strip())
                except Exception:
                    total_n = 0

                # === 自适应切分: 按"配置文件板总数"计算分片数 (给后续新上线板预留分片) ===
                try:
                    from devboard_toolkit.config import load_boards
                    all_boards_cfg = load_boards()
                    use_online_local = bool(self.use_online_var.get())
                    max_board_count = sum(
                        1 for n in all_boards_cfg.keys()
                        if use_online_local or not n.lower().startswith("online")
                    )
                except Exception:
                    max_board_count = 6  # 兜底
                max_board_count = max(1, max_board_count)

                if total_n == 0:
                    per_target = 8
                    n_parts = 1
                else:
                    # 向上取整 ceil(total / (max_board_count * 3)), 预留足够分片给后续上线板
                    divisor = max_board_count * 3
                    per_target = (total_n + divisor - 1) // divisor
                    per_target = max(2, min(60, per_target))  # clamp [2, 60]
                    n_parts = (total_n + per_target - 1) // per_target
                    n_parts = max(1, n_parts)

                _cur_boards = len(boards_to_use) if boards_to_use else 1

                print(f"\n{'=' * 60}")
                print(f"  [B-{round_idx}] 启动回灌: {os.path.basename(txt_path)}  "
                      f"({total_n} 条, 当前 {len(boards_to_use)} 块板, 全配置 {max_board_count} 块板, "
                      f"切 {n_parts} 份, 每份约 {per_target} 条)")
                print(f"{'=' * 60}")

                # 车型标定匹配
                if list_mode == "video":
                    from devboard_toolkit.classify_by_car import match_calibration
                    matched_car, matched_calib = match_calibration(
                        os.path.basename(txt_path), car_models)
                    if not matched_car:
                        print(f"[!] 无法匹配车型标定,跳过: {os.path.basename(txt_path)}")
                        return False, per_target, [], {}, []
                    vars_map_this = dict(vars_map)
                    vars_map_this["CAR_MODEL"] = matched_car
                    vars_map_this["CALIBRATION"] = matched_calib
                    print(f"  [i] 车型标定匹配: {matched_car} → {matched_calib}")
                    car_model_this = matched_car
                else:
                    vars_map_this = vars_map
                    car_model_this = ctx["car_model"]

                # 清理这些板的旧 .done
                for bn in boards_to_use:
                    safe_bn = bn.replace("/", "_").replace("\\", "_")
                    dp = os.path.join(log_dir, f"{safe_bn}.done")
                    if os.path.isfile(dp):
                        try:
                            os.remove(dp)
                        except Exception:
                            pass

                # 自适应切分 txt + 生成脚本 (按文件大小 LPT 轮询, 均衡回灌时长)
                print(f"\n[*] 自适应切分 txt ({os.path.basename(txt_path)}) 为 {n_parts} 份 "
                      f"(每份约 {per_target} 条, 按文件大小 LPT 均衡)...")
                sub_files = _split_txt(txt_path, n_parts, out_dir=unc_replay_folder,
                                       sort_by_size=True)
                sub_line_counts = []
                print(f"[+] 已生成 {len(sub_files)} 个子 txt:")
                for i, sf in enumerate(sub_files, 1):
                    full = os.path.join(unc_replay_folder, sf)
                    line_cnt = sum(1 for _ in open(full, encoding="utf-8") if _.strip())
                    sub_line_counts.append(line_cnt)
                    print(f"    {i}. {sf}  ({line_cnt} 条)")

                # 清理 replay_tmp 中本次所有分片的旧残留 (running_/failed_/tmp_*), 防跨任务污染
                replay_tmp_dir = os.path.join(unc_replay_folder, "replay_tmp")
                if os.path.isdir(replay_tmp_dir):
                    import glob as _glob_local
                    removed_cnt = 0
                    for sf in sub_files:
                        # sf = yaq_463_1.txt (无路径, stem 用作匹配后缀)
                        stem = os.path.splitext(sf)[0]
                        for pat in [f"running_{stem}.*", f"failed_{stem}.*",
                                    f"tmp_run_*", f"tmp_next_*"]:
                            for fp in _glob_local.glob(os.path.join(replay_tmp_dir, pat)):
                                try:
                                    os.remove(fp)
                                    removed_cnt += 1
                                except Exception:
                                    pass
                    if removed_cnt:
                        print(f"  [i] 清理 replay_tmp 残留 {removed_cnt} 个文件")

                scripts = []
                print(f"\n[*] 生成 {n_parts} 个启动脚本...")
                for i, sf in enumerate(sub_files, 1):
                    path = _gen_one_script(
                        template, vars_map_this, unc_replay_folder,
                        car_model_this, sf, i
                    )
                    scripts.append(path)
                    print(f"    {i}. {Path(path).name}  ←  {sf}")

                if scripts:
                    self.after(0, lambda p=scripts[0]: self.script_path.set(p))

                # 启动终端数 = min(板数, 分片数)
                n_launch = min(len(boards_to_use), n_parts)
                actual_boards = boards_to_use[:n_launch]
                returned_boards = boards_to_use[n_launch:]
                if returned_boards:
                    print(f"  [i] 切分数 < 分配板数, 退回 {len(returned_boards)} 块到空闲池: "
                          f"{', '.join(returned_boards)}")
                    self._pool_return_batch(returned_boards)

                # 构建分片信息列表: [(script_path, sub_file, line_cnt), ...]
                all_parts = list(zip(scripts, sub_files, sub_line_counts))
                launch_parts = all_parts[:n_launch]      # 首批启动的
                pending_parts = all_parts[n_launch:]     # 剩余等待接力的

                # 启动新 txt: 所有板都 reboot (防止上次挂载到其他路径导致回灌失败)
                no_reboot_set = set()
                for bn in actual_boards:
                    self._board_first_run[bn] = False  # 标记已使用,同txt内接力跳过reboot

                # 逐板分配明细: 板名 → 启动脚本 → 对应分片 + 条数
                assignments = []
                print(f"\n[*] 板 → 分片 分配明细 (首批 {len(actual_boards)} 块板"
                      f", 待接力 {len(pending_parts)} 份):")
                for i, board_name in enumerate(actual_boards):
                    script_path, sf, lc = launch_parts[i]
                    script_name = Path(script_path).name
                    reboot_tag = "[skip-reboot]" if board_name in no_reboot_set else "[reboot]"
                    print(f"    {i+1:2d}. {board_name:<16s} → {script_name:<32s} "
                          f"← {sf} ({lc:3d} 条)  {reboot_tag}")
                    assignments.append((board_name, ctx["replay_folder"], script_name))

                if pending_parts:
                    print(f"  [i] 以下 {len(pending_parts)} 份等待接力续跑:")
                    for j, (sp, sf, lc) in enumerate(pending_parts, 1):
                        print(f"    待-{j}. {Path(sp).name:<32s} ← {sf} ({lc:3d} 条)")

                from devboard_toolkit.batch_replay import _launch_terminals
                _launch_terminals(
                    _PROJECT_ROOT, assignments,
                    log_dir=log_dir, app_suffix=suffix,
                    delete_script=delete_script,
                    no_reboot_boards=no_reboot_set,
                )
                print(f"\n[+] 已启动回灌 (txt={os.path.basename(txt_path)}, "
                      f"首批板: {', '.join(actual_boards)}, 待接力: {len(pending_parts)} 份)")

                extra_ctx = {
                    "vars_map": vars_map_this,
                    "car_model": car_model_this,
                    "suffix": suffix,
                    "delete_script": delete_script,
                    "log_dir": log_dir,
                    "replay_folder": ctx["replay_folder"],
                }
                return True, per_target, pending_parts, extra_ctx, actual_boards

            # ---- 主轮询循环: 回收完成板 → 增量检测 → 启动新 txt ----
            while queue or running_txts:
                if stop_event.is_set():
                    print("\n[!] 用户取消, 终止回灌")
                    return 2

                # 0. 每 2 分钟增量检测新空闲板 (加锁, 避免多任务并发同时扫)
                now = time.time()
                if now - self._shared_last_rescan >= BOARD_RESCAN_INTERVAL:
                    if self._rescan_lock.acquire(blocking=False):
                        try:
                            self._shared_last_rescan = now
                            busy = self._busy_snapshot()
                            pool = self._pool_snapshot()
                            pool_set = set(pool)
                            try:
                                from concurrent.futures import ThreadPoolExecutor, as_completed
                                from devboard_toolkit.config import load_boards
                                from devboard_toolkit.usage_check import check_usage_one

                                all_boards_cfg = load_boards()
                                use_online = bool(self.use_online_var.get())
                                all_board_names = [n for n in all_boards_cfg.keys()
                                                   if use_online or not n.lower().startswith("online")]
                                # 检测: 不在 busy 中的板都检测 (含空闲池中的, 确认是否仍空闲)
                                to_check = [n for n in all_board_names if n not in busy]
                                if to_check:
                                    print(f"\n  [扫] 增量检测 {len(to_check)} 块板中... "
                                          f"(每 {BOARD_RESCAN_INTERVAL}s 一次)")
                                    newly_idle = []
                                    gone_busy = []
                                    with ThreadPoolExecutor(max_workers=len(to_check)) as ex:
                                        futs = {ex.submit(check_usage_one, n, all_boards_cfg[n]): n
                                                for n in to_check}
                                        for fut in as_completed(futs):
                                            if stop_event.is_set():
                                                break
                                            r = fut.result()
                                            if not r.busy:
                                                if r.name not in pool_set:
                                                    newly_idle.append(r.name)
                                                    print(f"    [+] 新板上线: {r.name} ({r.host})")
                                            else:
                                                if r.name in pool_set:
                                                    gone_busy.append(r.name)
                                                    print(f"    [-] {r.name} 已被占用, 从空闲池移除 ({r.host})")
                                    if newly_idle:
                                        self._pool_add(newly_idle)
                                        for bn in newly_idle:
                                            self._board_first_run[bn] = True
                                        print(f"  [扫] 新增 {len(newly_idle)} 块空闲板, "
                                              f"当前空闲池共 {self._pool_size()} 块")
                                    if gone_busy:
                                        for bn in gone_busy:
                                            self._pool_remove(bn)
                                        print(f"  [扫] 移除 {len(gone_busy)} 块已占用板, "
                                              f"当前空闲池共 {self._pool_size()} 块")
                                    if not newly_idle and not gone_busy:
                                        print(f"  [扫] 无变化, 保持现状 (空闲池 {self._pool_size()} 块)")
                                else:
                                    print(f"\n  [扫] 所有板均在使用中, 跳过增量检测")
                            except Exception as e:
                                print(f"  [扫] 增量检测异常: {e}")
                        finally:
                            self._rescan_lock.release()

                # 1. 检查所有正在跑的 txt 的板完成情况
                #    完成板优先分配给同一 txt 的剩余分片(接力续跑), 无剩余才退回池
                #    注意: 读取 .done 里的 exit_code, 失败板(exit!=0)不允许接力,直接退回池
                completed_boards_pool = []  # 最终退回共享池的板
                boards_to_relay = []        # 待接力的板: [(board_name, txt_path)]
                failed_boards_txts = set()  # 发生板级失败的 txt, 需丢弃其剩余 pending
                for txt_path, info in list(running_txts.items()):
                    still_running = []
                    for bn in info["boards"]:
                        safe_bn = bn.replace("/", "_").replace("\\", "_")
                        dp = os.path.join(log_dir, f"{safe_bn}.done")
                        if os.path.isfile(dp):
                            # 解析 .done 的 exit_code
                            done_exit = 0
                            try:
                                with open(dp, "r", encoding="utf-8") as df:
                                    for dline in df:
                                        if dline.startswith("exit_code="):
                                            try:
                                                done_exit = int(dline.strip().split("=", 1)[1])
                                            except Exception:
                                                done_exit = 0
                                            break
                            except Exception:
                                done_exit = 0
                            if done_exit != 0:
                                # 失败板: 不参与接力, 直接退回池, 并标记该 txt 不再接力
                                reason = {3: "失败素材", 4: "感知包CRASH"}.get(
                                    done_exit, f"exit={done_exit}")
                                print(f"    [✗] {bn} 失败 ({os.path.basename(txt_path)}) [{reason}], "
                                      f"退回空闲池 (不参与接力)")
                                completed_boards_pool.append(bn)
                                failed_boards_txts.add(txt_path)
                            elif info.get("pending"):
                                boards_to_relay.append((bn, txt_path))
                                print(f"    [✓] {bn} 完成 ({os.path.basename(txt_path)}) "
                                      f"→ 接力续跑 (还剩 {len(info['pending'])} 份)")
                            else:
                                completed_boards_pool.append(bn)
                                print(f"    [✓] {bn} 完成 ({os.path.basename(txt_path)}) "
                                      f"→ 该 txt 分片全部跑完, 退回空闲池")
                        else:
                            still_running.append(bn)
                    info["boards"] = still_running

                # 对失败板所在的 txt: 丢弃其剩余 pending (避免反复拿有问题的板接力)
                for ftxt in failed_boards_txts:
                    finfo = running_txts.get(ftxt)
                    if finfo and finfo.get("pending"):
                        dropped = len(finfo["pending"])
                        print(f"  [!] {os.path.basename(ftxt)}: 因板级失败, 放弃 {dropped} 份待接力分片")
                        finfo["pending"] = []

                # 所有已无在跑板且无待接力的 txt → 标记完成
                for txt_path in list(running_txts.keys()):
                    info2 = running_txts[txt_path]
                    if not info2.get("boards") and not info2.get("pending"):
                        del running_txts[txt_path]
                        print(f"\n[+] {os.path.basename(txt_path)} 回灌完成 "
                              f"(剩余 {len(queue)} 个待回灌, {len(running_txts)} 个进行中)")

                # 2. 处理接力续跑: 板直接续跑当前 txt 的下一个分片 (--no-reboot)
                for board_name, txt_path in boards_to_relay:
                    info = running_txts.get(txt_path)
                    if not info or not info.get("pending"):
                        completed_boards_pool.append(board_name)
                        continue
                    # 取出下一个待跑分片
                    script_path, sub_file, line_cnt = info["pending"].pop(0)
                    # 清理 .done 标记 (该板要立刻再跑)
                    safe_bn = board_name.replace("/", "_").replace("\\", "_")
                    dp = os.path.join(info["log_dir"], f"{safe_bn}.done")
                    try:
                        if os.path.isfile(dp):
                            os.remove(dp)
                    except Exception:
                        pass
                    # 启动: 同一 txt 接力 → 也 reboot (防止挂载残留)
                    script_name = Path(script_path).name
                    print(f"\n[接力] {board_name} 续跑 {os.path.basename(txt_path)} → "
                          f"{script_name} ← {sub_file} ({line_cnt} 条) [reboot]")
                    from devboard_toolkit.batch_replay import _launch_terminals
                    _launch_terminals(
                        _PROJECT_ROOT,
                        [(board_name, info["replay_folder"], script_name)],
                        log_dir=info["log_dir"],
                        app_suffix=info["suffix"],
                        delete_script=info["delete_script"],
                        no_reboot_boards=set(),  # 不跳过 reboot
                    )
                    # 板重新加入该 txt 的在跑列表
                    info["boards"].append(board_name)

                # 回收无接力任务的完成板到空闲池
                self._pool_return_batch(completed_boards_pool)

                # 3. 有空闲板且有待回灌 txt → 启动 (动态阈值判断)
                while self._pool_size() > 0 and queue:
                    pool_size = self._pool_size()
                    # === 动态阈值 ===
                    # 无在跑 txt: 阈值=1 (有空闲板立即启动)
                    # 有在跑 txt 且每份<=15条(小任务): 阈值=1 (避免空等)
                    # 有在跑 txt 且每份>15条(正常任务): 阈值=2 (提前启动下一个txt)
                    if not running_txts:
                        early_start_threshold = 1
                        reason = "无在跑任务"
                    else:
                        # 用下一个待启动 txt 的预估 per_target 判断 (粗略用第一个 txt 条数估计)
                        try:
                            next_total = sum(1 for _ in open(queue[0], encoding="utf-8") if _.strip())
                        except Exception:
                            next_total = 0
                        est_boards = pool_size if pool_size else 1
                        est_divisor = est_boards * 3
                        est_per_target = (next_total + est_divisor - 1) // est_divisor if next_total > 0 else 8
                        est_per_target = max(8, min(60, est_per_target))
                        if est_per_target <= 15:
                            early_start_threshold = 1
                            reason = f"小任务(每份约{est_per_target}条)"
                        else:
                            early_start_threshold = 2
                            reason = f"正常任务(每份约{est_per_target}条)"

                    if running_txts and pool_size < early_start_threshold:
                        break

                    next_txt = queue.pop(0)

                    # === 每个新 txt 启动前重新检测空闲板 (增量检测, 不阻塞) ===
                    try:
                        from concurrent.futures import ThreadPoolExecutor, as_completed
                        from devboard_toolkit.config import load_boards
                        from devboard_toolkit.usage_check import check_usage_one

                        all_boards_cfg = load_boards()
                        use_online = bool(self.use_online_var.get())
                        all_board_names = [n for n in all_boards_cfg.keys()
                                           if use_online or not n.lower().startswith("online")]
                        busy = self._busy_snapshot()
                        pool = self._pool_snapshot()
                        in_use = busy | set(pool)
                        to_check = [n for n in all_board_names if n not in in_use]
                        if to_check:
                            print(f"\n  [txt前检测] 增量扫描 {len(to_check)} 块未使用板...")
                            newly_idle = []
                            with ThreadPoolExecutor(max_workers=len(to_check)) as ex:
                                futs = {ex.submit(check_usage_one, n, all_boards_cfg[n]): n
                                        for n in to_check}
                                for fut in as_completed(futs):
                                    if stop_event.is_set():
                                        break
                                    r = fut.result()
                                    if not r.busy:
                                        newly_idle.append(r.name)
                            if newly_idle:
                                self._pool_add(newly_idle)
                                for bn in newly_idle:
                                    self._board_first_run[bn] = True
                                print(f"  [txt前检测] 新增 {len(newly_idle)} 块空闲板: "
                                      f"{', '.join(newly_idle)} (当前池: {self._pool_size()})")
                                pool_size = self._pool_size()
                    except Exception as e:
                        print(f"  [txt前检测] 增量检测异常: {e}")

                    try:
                        total_n = sum(1 for _ in open(next_txt, encoding="utf-8") if _.strip())
                    except Exception:
                        total_n = 0
                    # 取板数不超过用户选的板数(ctx["n"]), 避免多任务时一个任务占光所有板
                    n = min(pool_size, total_n, ctx["n"]) if total_n > 0 else min(pool_size, ctx["n"])
                    if n < 1:
                        # 修复: pool_size 不足时把 txt 放回队列头部, 等下一轮板空闲再跑
                        # (不能 continue 丢掉 txt)
                        queue.insert(0, next_txt)
                        print(f"[!] 空闲板不足, 暂缓: {os.path.basename(next_txt)} (放回队列头部)")
                        break
                    boards_to_use = self._pool_take(n)
                    pool_size = self._pool_size()
                    ok, per_target_used, pending_list, extra_ctx, actual_boards = (
                        _start_one_txt(next_txt, boards_to_use)
                    )
                    if not ok:
                        # 修复: ok=False 时退回板到池 + 把 txt 放回队列尾部 (而不是丢失)
                        self._pool_return_batch(boards_to_use)
                        queue.append(next_txt)
                        print(f"[!] 启动失败, 已退回 {len(boards_to_use)} 块板, "
                              f"txt 放回队列尾部: {os.path.basename(next_txt)}")
                        continue
                    running_txts[next_txt] = {
                            "boards": list(actual_boards),
                            "round": round_counter,
                            "per_target": per_target_used,
                            "pending": list(pending_list),
                            "vars_map": extra_ctx.get("vars_map", {}),
                            "car_model": extra_ctx.get("car_model", ""),
                            "suffix": extra_ctx.get("suffix", ""),
                            "delete_script": extra_ctx.get("delete_script", False),
                            "log_dir": extra_ctx.get("log_dir", log_dir),
                            "replay_folder": extra_ctx.get("replay_folder", ctx["replay_folder"]),
                        }

                # 3. 没法启动新 txt → 等待板完成
                pool_size = self._pool_size()
                running_wait = len(running_txts)
                pending_parts = sum(len(info.get("pending", [])) for info in running_txts.values())
                queue_len = len(queue)
                pending_wait = queue_len + pending_parts

                # 待回灌分项描述 (消除噪音: 只有一种就不显示另一种)
                if queue_len > 0 and pending_parts > 0:
                    pending_desc = f"待回灌: {queue_len} 个新txt + {pending_parts} 份接力"
                elif pending_parts > 0:
                    pending_desc = f"待回灌: {pending_parts} 份接力"
                elif queue_len > 0:
                    pending_desc = f"待回灌: {queue_len} 个新txt"
                else:
                    pending_desc = "待回灌: 无"

                if running_wait > 0:
                    # 显示当前阈值
                    if not running_txts:
                        cur_thr = 1
                    else:
                        any_big = any(info.get("per_target", 60) > 15
                                      for info in running_txts.values())
                        cur_thr = 1 if not any_big else 2
                    print(f"    等待中... 空闲 {pool_size} 块, 进行中 {running_wait} 个 txt, "
                          f"{pending_desc} (阈值={cur_thr})")
                    time.sleep(60)
                elif pending_wait > 0 and pool_size == 0:
                    print(f"    等待板空闲... {pending_desc}")
                    time.sleep(60)
                elif pending_wait > 0 and pool_size > 0:
                    # 空闲板不够阈值且无在跑 → 下一轮循环会直接启动 (running_txts 为空阈值=1)
                    pass

            print(f"\n{'=' * 60}")
            print(f"  全部 {len(sorted_txts)} 个 txt 回灌完毕!")
            print(f"{'=' * 60}")

            # === 合并所有分片的失败素材 ===
            try:
                import glob as _glob
                failed_files = _glob.glob(os.path.join(unc_replay_folder, "replay_tmp", "failed_*.txt"))
                if failed_files:
                    all_failed = set()
                    for ff in failed_files:
                        try:
                            with open(ff, "r", encoding="utf-8") as f:
                                for line in f:
                                    line = line.strip()
                                    if line:
                                        all_failed.add(line)
                        except Exception:
                            pass
                    if all_failed:
                        ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
                        summary_path = os.path.join(log_dir, f"failed_all_{ts}.txt")
                        with open(summary_path, "w", encoding="utf-8") as f:
                            for item in sorted(all_failed):
                                f.write(item + "\n")
                        print(f"\n[!] 总失败素材: {len(all_failed)} 条 → 汇总: {summary_path}")
                    else:
                        print(f"\n[✓] 所有分片无失败素材")
                else:
                    print(f"\n[✓] 无失败素材文件")
            except Exception as e:
                print(f"[!] 合并失败素材出错: {e}")

            return 0

        else:
            # ==== SDK 回灌 ====
            sdk_video_path = ctx.get("sdk_video_path", "")

            # ----------------------------------------------------------
            # 视频路径有值 → 智能检测: 区分已预处理/未预处理
            # ----------------------------------------------------------
            if sdk_video_path:
                print(f"\n{'=' * 60}")
                print(f"  [智能检测] 视频路径: {sdk_video_path}")
                print(f"{'=' * 60}")

                # 遍历视频路径下所有 .h265/.h264
                import glob as _glob
                from devboard_toolkit.classify_by_car import _walk_video_files
                all_videos = _walk_video_files(sdk_video_path)
                print(f"  找到 {len(all_videos)} 个 .h265/.h264 文件")

                if not all_videos:
                    print("[!] 视频路径下没有 .h265/.h264 文件")
                    return 1

                # 检测每个文件是否已预处理
                preprocessed = []    # 已预处理: 文件路径列表
                unprocessed = []     # 未预处理: 文件路径列表
                for vfp in all_videos:
                    if self._check_preprocessed(vfp):
                        preprocessed.append(vfp)
                    else:
                        unprocessed.append(vfp)

                print(f"  已预处理: {len(preprocessed)} 个")
                print(f"  未预处理: {len(unprocessed)} 个")

                # === Step 1: 未预处理分支 (复制 + ADAS预处理 + 常规SDK回灌) ===
                if unprocessed:
                    print(f"\n{'=' * 60}")
                    print(f"  [Step1] 未预处理素材: {len(unprocessed)} 个 → 复制+预处理+SDK回灌")
                    print(f"{'=' * 60}")

                    input_subpath = ctx["input_subpath"]
                    if not input_subpath:
                        print("[!] 未预处理素材需要素材相对路径, 请填写")
                        return 1

                    # 目标目录: UNC回灌路径/input/{USER}/{素材相对路径}/
                    target_base = os.path.join(unc_replay_folder, "input", ctx["user"], input_subpath)
                    os.makedirs(target_base, exist_ok=True)
                    print(f"  目标目录: {target_base}")

                    # 复制 .h265/.h264 并创建同名父文件夹
                    import shutil
                    for vfp in unprocessed:
                        fname = os.path.basename(vfp)
                        # 创建同名父文件夹 (去掉扩展名)
                        name_no_ext = os.path.splitext(fname)[0]
                        target_dir = os.path.join(target_base, name_no_ext)
                        os.makedirs(target_dir, exist_ok=True)
                        dst_file = os.path.join(target_dir, fname)
                        if os.path.isfile(dst_file):
                            print(f"    [跳过] {fname} (已存在)")
                            continue
                        shutil.copy2(vfp, dst_file)
                        print(f"    [复制] {fname} → {name_no_ext}/")

                    # 调用 ADAS 预处理 (后台调用, 日志输出到当前面板)
                    print(f"\n  [*] 启动 ADAS 预处理...")
                    from devboard_toolkit.data_preproc.pipeline import data_preproc_main
                    rc_pre = data_preproc_main(
                        txt_path=target_base,
                        output_dir=target_base,
                        mode="2",  # 视频路径模式
                        create_jira_folder=False,
                        classify_category=False,
                        run_preprocessing_flag=True,
                        max_workers=8,
                        car_type=3,
                        generate_mcap=False,
                        stop_event=stop_event,
                        create_file_folder=True,
                        keep_largest_suffix=False,
                    )
                    if stop_event.is_set():
                        return 2
                    if rc_pre != 0:
                        print(f"[!] ADAS 预处理失败 (rc={rc_pre}), 继续 SDK 回灌")
                    else:
                        print(f"  [+] ADAS 预处理完成")

                    # 常规 SDK 回灌
                    print(f"\n  [*] 启动常规 SDK 回灌 ({ctx['n']} 块板)...")
                    template = load_replay_sdk_template()
                    if not template:
                        print("[!] config.yaml 中未找到 replay_sdk_template")
                        return 1
                    n_sdk = ctx["n"]
                    scripts = []
                    v = dict(vars_map)
                    v["INPUT_SUBPATH"] = input_subpath
                    content = _render_template(template, v)
                    content = content.replace("\r\n", "\n").replace("\r", "\n")
                    for i in range(1, n_sdk + 1):
                        filename = f"start_sdk_{ctx['car_model']}_{i}.sh"
                        safe = filename.replace("/", "_").replace(":", "_").replace(" ", "_")
                        out_path = Path(unc_replay_folder) / safe
                        out_path.write_text(content, encoding="utf-8", newline="\n")
                        scripts.append(str(out_path))
                        print(f"    {i}. {filename}")

                    if scripts:
                        self.after(0, lambda: self.script_path.set(scripts[0]))

                    print(f"\n  [*] 检测空闲板 (排除已被其他任务占用的板)...")
                    idle = self._sync_detect_boards(stop_event, exclude_busy=True)
                    if stop_event.is_set():
                        return 2
                    if not idle:
                        print("[!] 没有空闲板, 结束 SDK 回灌")
                        return 1
                    self._pool_add(idle)
                    board_names = self._pool_take(n_sdk)
                    if len(board_names) < n_sdk:
                        print(f"[!] 空闲板不足: 需要 {n_sdk} 块, 可用 {len(board_names)} 块")
                        if not board_names:
                            return 1
                    print(f"  [+] 分配 {len(board_names)} 块板: {', '.join(board_names)}")

                    assignments = []
                    for i, board_name in enumerate(board_names):
                        script_name = Path(scripts[i]).name if i < len(scripts) else Path(scripts[-1]).name
                        assignments.append((board_name, ctx["replay_folder"], script_name))

                    os.makedirs(log_dir, exist_ok=True)
                    for bn in board_names:
                        safe_bn = bn.replace("/", "_").replace("\\", "_")
                        dp = os.path.join(log_dir, f"{safe_bn}.done")
                        if os.path.isfile(dp):
                            try:
                                os.remove(dp)
                            except Exception:
                                pass

                    from devboard_toolkit.batch_replay import _launch_terminals
                    _launch_terminals(
                        _PROJECT_ROOT, assignments,
                        log_dir=log_dir, app_suffix=suffix,
                        delete_script=delete_script,
                    )
                    print(f"\n  [+] SDK 回灌已启动 ({len(board_names)} 块板)")

                # === Step 2: 已预处理分支 (classify_by_car 生成 txt → 列表回灌) ===
                if preprocessed:
                    print(f"\n{'=' * 60}")
                    print(f"  [Step2] 已预处理素材: {len(preprocessed)} 个 → 列表回灌")
                    print(f"{'=' * 60}")

                    # 把已预处理素材的**所在目录**去重 (classify_by_car 会递归遍历)
                    # 先写一个临时 txt 列出所有已预处理视频的所在目录, 再让 classify_by_car 扫描
                    # 但 classify_by_car 需要一个根目录, 这里直接把 sdk_video_path 传进去
                    # 它内部会遍历所有 .h265/.h264, 但已预处理和未预处理的都会被扫到
                    # 所以需要: 只让已预处理的文件进入列表回灌
                    # 方案: 直接在 unc_replay_folder 下生成一个 txt, 手动写入已预处理的 Linux 路径
                    from devboard_toolkit.classify_by_car import normalize_path, _classify_one, CAR_KEYWORDS
                    from collections import defaultdict
                    from concurrent.futures import ThreadPoolExecutor, as_completed

                    print(f"  [*] 对已预处理素材按车型分类 ({len(preprocessed)} 个)...")
                    car_map_pp = defaultdict(list)
                    unknown_pp = []
                    with ThreadPoolExecutor(max_workers=32) as ex:
                        futs = {ex.submit(_classify_one, fp): fp for fp in preprocessed}
                        total_pp = len(futs)
                        done_pp = 0
                        for fut in as_completed(futs):
                            if stop_event.is_set():
                                break
                            linux_path, car = fut.result()
                            if car == "unknown":
                                unknown_pp.append(linux_path)
                            else:
                                car_map_pp[car].append(linux_path)
                            done_pp += 1
                            if done_pp % 500 == 0 or done_pp == total_pp:
                                print(f"    进度: {done_pp}/{total_pp}")

                    # 写 txt
                    output_txts_pp = []
                    for car, paths in sorted(car_map_pp.items()):
                        fname_pp = f"{ctx['user']}_{car}.txt"
                        fpath_pp = os.path.join(unc_replay_folder, fname_pp)
                        with open(fpath_pp, "w", encoding="utf-8") as f:
                            for p in paths:
                                f.write(p + "\n")
                        output_txts_pp.append((fpath_pp, len(paths)))
                        print(f"    {fname_pp:30s}: {len(paths):5d} 条")

                    if unknown_pp:
                        fpath_unk = os.path.join(unc_replay_folder, "error_unknown.txt")
                        with open(fpath_unk, "w", encoding="utf-8") as f:
                            for p in unknown_pp:
                                f.write(p + "\n")
                        print(f"    {'error_unknown.txt':30s}: {len(unknown_pp):5d} 条 (暂不回灌)")

                    # 按条数升序
                    output_txts_pp.sort(key=lambda x: x[1])
                    sorted_txts_pp = [fp for fp, _ in output_txts_pp]

                    if not sorted_txts_pp:
                        print("[!] 没有可回灌的已预处理 txt")
                    else:
                        # 构造列表回灌的 ctx 并递归调用 _do_start_task
                        list_ctx2 = dict(ctx)
                        list_ctx2["mode"] = "list"
                        list_ctx2["list_input_mode"] = "txt"
                        # 多个 txt 合并为一个大 txt (列表回灌内部会自适应切分)
                        if len(sorted_txts_pp) == 1:
                            list_ctx2["list_input_path"] = sorted_txts_pp[0]
                        else:
                            merged_path = os.path.join(unc_replay_folder, f"{ctx['user']}_merged.txt")
                            with open(merged_path, "w", encoding="utf-8") as mf:
                                for tp in sorted_txts_pp:
                                    with open(tp, encoding="utf-8") as tf:
                                        for line in tf:
                                            if line.strip():
                                                mf.write(line)
                            list_ctx2["list_input_path"] = merged_path
                            print(f"  [i] 多个 txt 合并为: {os.path.basename(merged_path)}")

                        print(f"\n  [*] 启动列表回灌 ({len(sorted_txts_pp)} 个 txt)...")
                        rc_list = self._do_start_task(list_ctx2, stop_event)
                        if rc_list != 0:
                            print(f"[!] 列表回灌失败 (rc={rc_list})")

                if not preprocessed and not unprocessed:
                    print("[!] 视频路径下没有 .h265/.h264 文件")
                    return 1

                print(f"\n[+] SDK 智能检测回灌完成")
                return 0

            # ----------------------------------------------------------
            # 无视频路径 → 常规 SDK 回灌 (原逻辑)
            # ----------------------------------------------------------
            template = load_replay_sdk_template()
            if not template:
                print("[!] config.yaml 中未找到 replay_sdk_template")
                return 1
            n = ctx["n"]

            print(f"\n[*] 生成 SDK 回灌脚本 ({n} 块板)...")
            scripts = []
            v = dict(vars_map)
            v["INPUT_SUBPATH"] = ctx["input_subpath"]
            content = _render_template(template, v)
            content = content.replace("\r\n", "\n").replace("\r", "\n")
            for i in range(1, n + 1):
                filename = f"start_sdk_{ctx['car_model']}_{i}.sh"
                safe = filename.replace("/", "_").replace(":", "_").replace(" ", "_")
                out_path = Path(unc_replay_folder) / safe
                out_path.write_text(content, encoding="utf-8", newline="\n")
                scripts.append(str(out_path))
                print(f"    {i}. {filename}")

            if scripts:
                self.after(0, lambda: self.script_path.set(scripts[0]))

            # 检测空闲板并从共享板池分配
            print(f"\n[*] 检测空闲板 (排除已被其他任务占用的板)...")
            idle = self._sync_detect_boards(stop_event, exclude_busy=True)
            if stop_event.is_set():
                return 2
            if not idle:
                print("[!] 没有空闲板, 结束回灌")
                return 1
            self._pool_add(idle)
            board_names = self._pool_take(n)
            if len(board_names) < n:
                print(f"[!] 空闲板不足: 需要 {n} 块, 可用 {len(board_names)} 块")
                if not board_names:
                    return 1
            print(f"[+] 分配 {len(board_names)} 块板: {', '.join(board_names)}")

            assignments = []
            for i, board_name in enumerate(board_names):
                script_name = Path(scripts[i]).name if i < len(scripts) else Path(scripts[-1]).name
                assignments.append((board_name, ctx["replay_folder"], script_name))

            os.makedirs(log_dir, exist_ok=True)
            # 清理旧 done
            for bn in board_names:
                safe_bn = bn.replace("/", "_").replace("\\", "_")
                dp = os.path.join(log_dir, f"{safe_bn}.done")
                if os.path.isfile(dp):
                    try:
                        os.remove(dp)
                    except Exception:
                        pass

            from devboard_toolkit.batch_replay import _launch_terminals
            _launch_terminals(
                _PROJECT_ROOT, assignments,
                log_dir=log_dir, app_suffix=suffix,
                delete_script=delete_script,
            )

            print("\n[+] 已启动多终端回灌,请在各终端窗口观察输出")
            return 0

    def _check_preprocessed(self, video_file: str) -> bool:
        """检测素材是否已预处理

        判定条件 (全部满足才算已预处理):
        1. .h265/.h264 本身非空 (size > 0)
        2. 同目录下存在同名 .bin 且非空
        3. 同目录下存在同名 .mp4 且非空
        4. 同目录下 log/ 文件夹存在且非空 (log 内为 txt 文件)
        """
        import os as _os
        try:
            # 条件1: 视频文件本身非空
            if _os.path.getsize(video_file) == 0:
                return False

            base_dir = _os.path.dirname(video_file)
            name_no_ext = _os.path.splitext(_os.path.basename(video_file))[0]

            # 条件2: 同名 .bin 存在且非空
            bin_path = _os.path.join(base_dir, name_no_ext + ".bin")
            if not _os.path.isfile(bin_path) or _os.path.getsize(bin_path) == 0:
                return False

            # 条件3: 同名 .mp4 存在且非空
            mp4_path = _os.path.join(base_dir, name_no_ext + ".mp4")
            if not _os.path.isfile(mp4_path) or _os.path.getsize(mp4_path) == 0:
                return False

            # 条件4: 同目录下 log/ 文件夹存在且非空 (内容为 txt 文件)
            log_dir = _os.path.join(base_dir, "log")
            if not _os.path.isdir(log_dir):
                return False
            log_files = [_os.path.join(log_dir, f) for f in _os.listdir(log_dir)]
            if not log_files:
                return False

            return True
        except (OSError, PermissionError):
            return False

    def _refresh_stop_combo(self):
        """刷新停止任务下拉框: 实时加载正在运行的任务名"""
        running = {tid: t for tid, t in self._tasks.items()
                   if t.get("status") == "running" and t["thread"].is_alive()}
        values = list(running.keys())
        if len(values) > 1:
            values.append("全部停止")
        self._stop_task_combo["values"] = values
        if values:
            cur = self._stop_task_var.get()
            if not cur or cur not in values:
                self._stop_task_var.set(values[0])
        else:
            self._stop_task_var.set("")

    def _on_stop(self):
        """多任务并发模式: 通过下拉框选择要停止的任务"""
        running = {tid: t for tid, t in self._tasks.items()
                   if t.get("status") == "running" and t["thread"].is_alive()}
        if not running:
            self.log_panel.log("没有正在运行的任务", "info")
            return

        selected = self._stop_task_var.get().strip()
        if selected == "全部停止":
            for tid, t in running.items():
                t["stop_event"].set()
                self.log_panel.log(f"[{tid}] 用户中止回灌", "err")
            return

        if selected and selected in running:
            running[selected]["stop_event"].set()
            self.log_panel.log(f"[{selected}] 用户中止回灌", "err")
        elif len(running) == 1:
            tid = list(running.keys())[0]
            running[tid]["stop_event"].set()
            self.log_panel.log(f"[{tid}] 用户中止回灌", "err")
        else:
            self.log_panel.log("请从下拉框选择要停止的任务", "warn")

    def _on_open_dir(self):
        if not self._replay_folder or not self._unc_testbed:
            messagebox.showwarning("提示", "请先选择回灌环境")
            return
        p = os.path.normpath(os.path.join(self._unc_testbed, self._replay_folder))
        if os.path.isdir(p):
            try:
                os.startfile(p)  # type: ignore[attr-defined]
            except Exception as e:
                self.log_panel.log(f"打开失败: {e}", "err")
        else:
            messagebox.showwarning("提示", f"回灌目录不存在: {p}")


# ---------------------------------------------------------------------------
# Tab 4: 组合流水线 (节点复选 + 一键串联)
# ---------------------------------------------------------------------------

class TabPipeline(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=12)

        # 顶部操作行
        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(head, text="可自由组合节点(支持任意组合), 默认仅启用「③ 自动回灌」",
                  style="Hint.TLabel").pack(side="left")
        ttk.Button(head, text="🔄 刷新配置摘要",
                   command=self._refresh_summary).pack(side="right")

        # 节点选择 (全部可自由勾选)
        flow = ttk.LabelFrame(self, text="流水线节点 (任选组合)",
                              style="Card.TLabelframe")
        flow.pack(fill="x", pady=(12, 0))

        # 默认: 仅 ③自动回灌 勾选
        self.nodes = [
            ("n1", "① 数据处理",       False),
            ("n2", "② 感知包编译",     False),
            ("n3", "③ 自动回灌",       True),
        ]
        self.node_vars = {}
        self._node_cbs = {}
        for i, (key, label, default) in enumerate(self.nodes):
            var = tk.BooleanVar(value=default)
            self.node_vars[key] = var
            cb = ttk.Checkbutton(flow, text=label, variable=var, state="normal")
            cb.grid(row=0, column=i, padx=12, pady=4, sticky="w")
            self._node_cbs[key] = cb
            flow.columnconfigure(i, weight=1)

        # 各节点配置摘要 (点击刷新按钮更新)
        summary = ttk.LabelFrame(self, text="当前各节点配置摘要 (点击右上 刷新配置摘要 更新)", style="Card.TLabelframe")
        summary.pack(fill="x", pady=(12, 0))

        self._summary_labels = {}
        for key, label, _ in self.nodes:
            row = ttk.Frame(summary)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=label, width=16, anchor="w",
                      style="SubTitle.TLabel").pack(side="left")
            hint_var = tk.StringVar(value="(未刷新)")
            self._summary_labels[key] = hint_var
            ttk.Label(row, textvariable=hint_var, style="Hint.TLabel").pack(side="left", padx=(6, 0))

        # 按钮栏
        btns = ttk.Frame(self)
        btns.pack(fill="x")
        ttk.Button(btns, text="▶ 一键全流程执行", style="Primary.TButton",
                   command=self._on_start).pack(side="left")
        ttk.Button(btns, text="× 中止", style="Danger.TButton",
                   command=self._on_abort).pack(side="left", padx=8)

        # 总进度
        prog = ttk.LabelFrame(self, text="总进度", style="Card.TLabelframe")
        prog.pack(fill="x", pady=(12, 0))
        self.total_prog = ttk.Progressbar(prog, mode="determinate",
                                           style="Horizontal.TProgressbar")
        self.total_prog.pack(fill="x", pady=4)
        self.total_status = tk.StringVar(value="等待启动")
        ttk.Label(prog, textvariable=self.total_status,
                  style="Hint.TLabel").pack(anchor="e")

        # 日志
        self.log_panel = _LogPanel(self, title="流水线日志", show_progress=False)
        self.log_panel.pack(fill="both", expand=True, pady=(12, 0))
        self._thread = None
        self._stop_event = threading.Event()
        # 初始刷新一次
        self.after(300, self._refresh_summary)

    def _refresh_summary(self):
        """读取其他 Tab 控件值,刷新摘要显示"""
        try:
            app = self.master.master
            tab_data = app.tab_data
            tab_build = app.tab_build
            tab_feed = app.tab_feed
        except Exception:
            return

        # 节点① 数据处理
        m_map = {"jira": "Jira链接", "video": "视频路径", "batch": "批量复制"}
        d_mode = m_map.get(tab_data.mode_var.get(), "?")
        d_class = "开" if tab_data.v_classify.get() else "关"
        d_ff = "开" if tab_data.v_file_folder.get() else "关"
        d_jf = "开" if tab_data.v_create_dir.get() else "关"
        d_kl = "开" if tab_data.v_keep_largest.get() else "关"
        d_adas = "开" if tab_data.v_adas.get() else "关"
        d_w = f"{tab_data.workers_var.get()}"
        self._summary_labels["n1"].set(
            f"模式={d_mode} | 车型分类={d_class} | 同名文件夹={d_ff} | "
            f"Jira子目录={d_jf} | 只保留最大后缀={d_kl} | ADAS预处理={d_adas} | 并发={d_w}"
        )

        # 节点② 感知包编译
        build_on = bool(self.node_vars["n2"].get())
        if build_on:
            from devboard_toolkit.config import load_jenkins
            jenkins_cfg = load_jenkins()
            b_job = jenkins_cfg.get("default_job", "?")
            b_sdk = os.path.basename(tab_build.sdk_zip.get()) or "(未选)"
            b_out = tab_build.out_dir.get() or "(未选)"
            self._summary_labels["n2"].set(
                f"Job={b_job} | SDK={b_sdk} | 输出={b_out}"
            )
        else:
            self._summary_labels["n2"].set(
                f"(节点未勾选 → 直接使用回灌环境中现有 runtime + 感知包)"
            )

        # 节点③ 自动回灌
        f_mode_map = {"sdk": "SDK回灌", "list": "列表回灌"}
        f_mode = f_mode_map.get(tab_feed._mode_var.get(), "?")
        try:
            env_name = os.path.basename(tab_feed.env_var.get()) or "(未选)"
        except Exception:
            env_name = "(未选)"
        car = tab_feed.car_var.get() or "(未选)"
        user = tab_feed.sdk_user_var.get() or tab_feed.list_user_var.get() or "(未填)"
        if tab_feed._mode_var.get() == "list":
            lim_map = {"txt": "txt", "video": "视频路径"}
            lim = lim_map.get(getattr(tab_feed, "_list_input_mode", "txt").get(), "?")
            pkg = tab_feed.pkg_var.get() or "(未选)"
            self._summary_labels["n3"].set(
                f"方式={f_mode}({lim}) | 回灌环境={env_name} | 车型={car} | 用户={user} | 感知包={pkg}"
            )
        else:
            sp = tab_feed.sdk_path_var.get() or "(未填)"
            pkg = tab_feed.pkg_var.get() or "(未选)"
            self._summary_labels["n3"].set(
                f"方式={f_mode} | 回灌环境={env_name} | 车型={car} | 用户={user} | 感知包={pkg} | 素材={sp}"
            )
        # 节点未勾选时统一提示
        for key in ("n1", "n2", "n3"):
            if not self.node_vars[key].get():
                self._summary_labels[key].set("(节点未勾选 → 跳过)")

    def _on_start(self):
        run_data = bool(self.node_vars["n1"].get())
        run_build = bool(self.node_vars["n2"].get())
        run_feed = bool(self.node_vars["n3"].get())
        # 至少勾选 1 个节点
        if not (run_data or run_build or run_feed):
            messagebox.showwarning("提示", "请至少勾选一个流水线节点")
            return
        if self._thread and self._thread.is_alive():
            messagebox.showwarning("提示", "流水线正在运行中")
            return

        self._stop_event.clear()
        self.log_panel.text.configure(state="normal")
        self.log_panel.text.delete("1.0", "end")
        self.log_panel.text.configure(state="disabled")

        # 实际参与节点 (用于进度条计算)
        step_nodes = [k for k, v in (("n1", run_data), ("n2", run_build), ("n3", run_feed)) if v]

        def _task(stop_event):
            app = self.master.master
            tab_data = app.tab_data
            tab_build = app.tab_build
            tab_feed = app.tab_feed
            stop = stop_event

            # ============================================================
            # Step 0: 校验所有勾选节点的必需项
            # ============================================================
            print("=" * 60)
            print("  Step 0: 参数校验 (勾选节点:",
                  ", ".join(step_nodes) or "无", ")")
            print("=" * 60)

            mode_val = None
            txt_path = None
            if run_data:
                # Tab1 数据处理校验
                txt_path = tab_data.txt_path.get()
                if not txt_path:
                    print("[!] Tab1(数据处理) 未选择 素材输入")
                    return 1
                mode_val = tab_data.mode_var.get()
                if mode_val in ("jira", "batch"):
                    if not os.path.isfile(txt_path):
                        print(f"[!] Tab1 输入不是有效文件: {txt_path}")
                        return 1
                elif mode_val == "video":
                    if not os.path.isdir(txt_path):
                        print(f"[!] Tab1 输入不是有效文件夹: {txt_path}")
                        return 1
                if not tab_data.out_dir.get():
                    print("[!] Tab1(数据处理) 未填写 输出目录")
                    return 1
                print(f"  [✓] Tab1 数据处理: 模式={mode_val} 输入={txt_path}")
            else:
                print("  [跳过] Tab1 数据处理 (节点未勾选)")

            if run_build:
                # Tab2 编译校验 (启用才校验)
                sdk_zip = tab_build.sdk_zip.get()
                if not sdk_zip or not os.path.isfile(sdk_zip):
                    print("[!] Tab2(感知包编译) 未选择 SDK zip 或文件不存在")
                    return 1
                if not tab_build.out_dir.get():
                    print("[!] Tab2(感知包编译) 未填写 输出回灌目录")
                    return 1
                print(f"  [✓] Tab2 感知包编译: SDK={os.path.basename(sdk_zip)} 输出={tab_build.out_dir.get()}")
            else:
                print("  [跳过] Tab2 感知包编译 (节点未勾选)")

            if run_feed:
                # Tab3 回灌校验
                if not tab_feed.env_var.get():
                    print("[!] Tab3(自动回灌) 未选择 回灌环境")
                    return 1
                if not getattr(tab_feed, "_unc_testbed", ""):
                    print("[!] Tab3 回灌环境尚未扫描完成,请稍后再试")
                    return 1
                if not tab_feed.car_var.get():
                    print("[!] Tab3(自动回灌) 未选车型")
                    return 1
                if not tab_feed.pkg_var.get():
                    print("[!] Tab3(自动回灌) 未选感知包")
                    return 1
                car_values = list(tab_feed.car_combo["values"])
                car_model_idx = tab_feed.car_combo.current()
                if car_model_idx < 0 or car_model_idx >= len(car_values):
                    print("[!] Tab3(自动回灌) 车型-校准映射 索引非法")
                    return 1
                if tab_feed._mode_var.get() == "sdk":
                    if not tab_feed.sdk_user_var.get():
                        print("[!] SDK 回灌未填用户名")
                        return 1
                    if not tab_feed.sdk_path_var.get():
                        print("[!] SDK 回灌未填素材相对路径")
                        return 1
                else:
                    list_mode = tab_feed._list_input_mode.get()
                    list_in = tab_feed.list_txt_row.get()
                    if not list_in:
                        print("[!] 列表回灌未选择素材输入")
                        return 1
                    if list_mode == "txt" and not os.path.isfile(list_in):
                        print(f"[!] 列表回灌素材 txt 不存在: {list_in}")
                        return 1
                    if list_mode == "video" and not os.path.isdir(list_in):
                        print(f"[!] 列表回灌视频路径不存在: {list_in}")
                        return 1
                    if not tab_feed.list_user_var.get():
                        print("[!] 列表回灌未填用户名")
                        return 1
                    if not tab_feed.list_date_var.get():
                        print("[!] 列表回灌未填日期")
                        return 1
                # fcf: 校验选中的版本
                fcf_values = list(tab_feed.cal_combo["values"])
                fcf_idx = tab_feed.cal_combo.current()
                if fcf_idx < 0 or fcf_idx >= len(fcf_values):
                    print("[!] Tab3(自动回灌) fcf 校准版本未选")
                    return 1
                print(f"  [✓] Tab3 自动回灌: 方式={tab_feed._mode_var.get()} "
                      f"环境={tab_feed.env_var.get()} fcf版本={fcf_values[fcf_idx]}")
            else:
                print("  [跳过] Tab3 自动回灌 (节点未勾选)")

            # ============================================================
            # Step 1: 并行: [数据处理(可选)] + [感知包编译(可选)]
            # ============================================================
            step1_enabled = run_data or run_build
            if step1_enabled:
                print("\n" + "=" * 60)
                tags = []
                if run_data:
                    tags.append("① 数据处理")
                if run_build:
                    tags.append("② 感知包编译(并行)")
                print(f"  Step 1: {' + '.join(tags)}")
                print("=" * 60)

                def _run_data_preproc():
                    """节点① 数据处理"""
                    from devboard_toolkit.data_preproc.pipeline import data_preproc_main
                    mode_map = {"jira": "1", "video": "2", "batch": "3"}
                    mode = mode_map.get(mode_val, "1")
                    rc = data_preproc_main(
                        txt_path=txt_path,
                        output_dir=tab_data.out_dir.get(),
                        mode=mode,
                        create_jira_folder=tab_data.v_create_dir.get(),
                        classify_category=tab_data.v_classify.get(),
                        run_preprocessing_flag=tab_data.v_adas.get(),
                        max_workers=tab_data.workers_var.get(),
                        car_type=int(tab_data.car_type_var.get().split(" - ")[0]) if tab_data.car_type_var.get().strip() else 3,
                        generate_mcap=(tab_data.mcap_var.get() == "是"),
                        stop_event=stop,
                        create_file_folder=tab_data.v_file_folder.get(),
                        keep_largest_suffix=tab_data.v_keep_largest.get(),
                    )
                    return rc

                def _run_build():
                    """节点② 感知包编译"""
                    project_root = _PROJECT_ROOT
                    if project_root not in sys.path:
                        sys.path.insert(0, project_root)
                    from jenkins_build import auto_build_main
                    app_name, rc = auto_build_main(
                        sdk_zip_path=tab_build.sdk_zip.get(),
                        replay_dir=tab_build.out_dir.get() or None,
                    )
                    return app_name, rc

                step1_failed = False
                cancelled = False

                from concurrent.futures import ThreadPoolExecutor, as_completed
                with ThreadPoolExecutor(max_workers=2) as ex:
                    future_map = {}
                    if run_data:
                        future_map[ex.submit(_run_data_preproc)] = ("① 数据处理", None)
                    if run_build:
                        future_map[ex.submit(_run_build)] = ("② 感知包编译", None)

                    for fut in as_completed(future_map):
                        if stop.is_set():
                            cancelled = True
                            break
                        tag, _ = future_map[fut]
                        try:
                            res = fut.result()
                        except Exception as e:
                            print(f"\n[!] {tag} 抛出异常: {e}")
                            import traceback; traceback.print_exc()
                            step1_failed = True
                            continue
                        if tag == "① 数据处理":
                            rc_d = res
                            if rc_d == 2:
                                print(f"[!] {tag} 已取消")
                                cancelled = True
                            elif rc_d != 0:
                                print(f"[!] {tag} 失败")
                                step1_failed = True
                            else:
                                print(f"[+] {tag} 完成")
                        else:  # ② 感知包编译
                            app_name_b, rc_b = res
                            if rc_b != 0:
                                print(f"[!] {tag} 失败")
                                step1_failed = True
                            else:
                                print(f"[+] {tag} 完成,感知包名={app_name_b}")
                                # 编译完成后重新扫描一次 Tab3 感知包下拉
                                try:
                                    self.after(0, lambda: tab_feed._on_select_env())
                                except Exception:
                                    pass

                if cancelled:
                    print("\n[!] 用户取消 Step1,流水线终止")
                    return 2
                if step1_failed:
                    print("[!] Step1 有失败节点 → 流水线终止")
                    return 1
            else:
                print("\n[跳过] Step1 (数据处理+感知包编译均未勾选)")

            # ============================================================
            # Step 2: 回灌环境完整性检测 + 编译后刷新 (仅 n3 勾选时执行)
            # ============================================================
            if run_feed:
                print("\n" + "=" * 60)
                print(f"  Step 2: 回灌环境完整性检测 (need_build={run_build})")
                print("=" * 60)

                env_name = tab_feed.env_var.get()
                unc_testbed = tab_feed._unc_testbed
                unc_replay_folder = os.path.normpath(os.path.join(unc_testbed, env_name))

                fcf_ver = tab_feed.cal_var.get()
                fcf_src_dir = None
                if fcf_ver and fcf_ver != "default":
                    try:
                        from devboard_toolkit.batch_replay import _project_tool_dir
                        fcf_src_dir = os.path.join(
                            _project_tool_dir(), "fcf_calibration", fcf_ver)
                    except Exception:
                        fcf_src_dir = None

                from devboard_toolkit.batch_replay import validate_replay_env
                ok_env, app_name_env = validate_replay_env(
                    replay_dir=unc_replay_folder,
                    need_build=run_build,
                    fcf_src_dir=fcf_src_dir,
                )
                if not ok_env:
                    print("[!] 回灌环境完整性检测失败,流水线终止")
                    return 1
                print(f"  [✓] 环境完整性检测通过, 感知包={'(编译模式,编译产物为准)' if run_build else app_name_env}")

                # 已勾选编译节点: 编译后刷新一次感知包下拉框
                if run_build:
                    print("  [i] 编译模式 → 强制刷新一次 Tab3 感知包下拉框...")
                    try:
                        import time as _t
                        retry = 0
                        while retry < 3:
                            if stop.is_set():
                                return 2
                            sync_done = threading.Event()

                            def _flush():
                                try:
                                    tab_feed._on_select_env()
                                finally:
                                    sync_done.set()

                            self.after(0, _flush)
                            sync_done.wait(timeout=5)
                            if tab_feed.pkg_var.get():
                                break
                            retry += 1
                            _t.sleep(2)
                    except Exception as e:
                        print(f"  [!] 刷新感知包下拉失败: {e}")
            else:
                print("\n[跳过] Step2 (未勾选自动回灌)")

            # ============================================================
            # Step 3: 自动回灌 (仅 n3 勾选时执行)
            # ============================================================
            if run_feed:
                print("\n" + "=" * 60)
                print("  Step 3: 自动回灌")
                print("=" * 60)

                f_mode = tab_feed._mode_var.get()
                f_n = tab_feed.board_count.get()
                f_user = (tab_feed.sdk_user_var.get()
                          if f_mode == "sdk"
                          else tab_feed.list_user_var.get())
                f_date = (tab_feed.sdk_date_var.get()
                          if f_mode == "sdk"
                          else tab_feed.list_date_var.get())
                f_input_subpath = (tab_feed.sdk_path_var.get().strip()
                                   .replace("\\", "/").strip("/")
                                   if f_mode == "sdk" else "")
                f_list_input_path = tab_feed.list_txt_row.get() if f_mode == "list" else ""
                f_list_mode = tab_feed._list_input_mode.get() if hasattr(tab_feed, "_list_input_mode") else "txt"

                task_ctx = {
                    "task_id": "PL",
                    "mode": f_mode,
                    "n": f_n,
                    "user": f_user,
                    "date": f_date,
                    "input_subpath": f_input_subpath,
                    "pkg_name": tab_feed.pkg_var.get(),
                    "car_model": tab_feed.car_var.get(),
                    "fcf_version": tab_feed.cal_var.get(),
                    "replay_folder": tab_feed.env_var.get(),
                    "unc_testbed": tab_feed._unc_testbed,
                    "linux_testbed_base": tab_feed._linux_testbed_base,
                    "list_input_mode": f_list_mode,
                    "list_input_path": f_list_input_path,
                    "delete_script": tab_feed.v_delete_scripts.get(),
                }

                print(f"  [✓] 准备就绪: 方式={f_mode} 板数={f_n} 用户={f_user}")

                rc_feed = tab_feed._do_start_task(task_ctx, stop)
                if rc_feed == 2:
                    print("[!] 回灌被取消,流水线终止")
                    return 2
                if rc_feed != 0:
                    print("[!] 自动回灌失败 → 流水线终止")
                    return 1
                else:
                    print("[+] 自动回灌完成")
            else:
                print("\n[跳过] Step3 自动回灌 (节点未勾选)")

            print("\n========== 流水线执行完毕 ==========")
            return 0

        # _step_progress: 将 step_idx(0=Step1/1=Step2/2=Step3) 映射为进度
        # 分母: 实际执行了多少个执行步(数据/编译并行算1步,环境检测1步,回灌1步)
        def _build_step_scale():
            exec_steps = 0
            if run_data or run_build:
                exec_steps += 1  # Step1 并行合并为 1 步
            if run_feed:
                exec_steps += 2  # Step2 环境检测 + Step3 回灌
            return max(1, exec_steps)

        steps_total = _build_step_scale()

        def _set_step_progress(step_idx, desc):
            # step_idx: 0-based, 当前执行到第几个执行步
            # 每个执行步区间 [step/steps_total, (step+1)/steps_total]
            # 给个 90% 的最大值(最后收尾再补到 100%)
            ratio = min(step_idx, steps_total) / steps_total * 0.9
            pct = int(ratio * 100)
            self.total_prog.configure(maximum=100, value=pct)
            self.total_status.set(f"{desc} ({pct}%)")

        def _on_done(rc, _a):
            if rc == 0:
                self.log_panel.log("流水线执行完毕", "ok")
                self.total_prog.configure(maximum=100, value=100)
                self.total_status.set("完成 100%")
            elif rc == 2:
                self.log_panel.log("流水线已被用户中止", "err")
                self.total_status.set("已中止")
            else:
                self.log_panel.log("流水线执行失败", "err")
                self.total_status.set("失败")

        # 初始进度: Step0 参数校验 (起点 5%)
        self.total_prog.configure(maximum=100, value=5)
        self.total_status.set("运行中… (Step0 参数校验, 勾选节点=" +
                              ",".join(step_nodes) + ")")
        self._thread = _run_in_thread(_task, self.log_panel, self._stop_event, _on_done)

    def _on_abort(self):
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            self.log_panel.log("用户中止流水线", "err")
            self.total_status.set("已中止")
        else:
            self.log_panel.log("没有正在运行的流水线", "info")


# ---------------------------------------------------------------------------
# 全局设置弹窗
# ---------------------------------------------------------------------------

class _DynKVEditor(ttk.Frame):
    """通用 key-value 动态编辑器 (用于车型-标定映射)

    每行: [key输入框] [value输入框] [×删除] ; 底部 [+ 添加] 按钮。
    """

    def __init__(self, master, key_label: str = "Key", value_label: str = "Value",
                 initial: Optional[list] = None):
        super().__init__(master)

        # 表头
        head = ttk.Frame(self)
        head.pack(fill="x", pady=(0, 4))
        ttk.Label(head, text=key_label, width=14, anchor="w").pack(side="left")
        ttk.Label(head, text=value_label, anchor="w").pack(side="left", padx=(4, 80))

        self._rows: list = []  # [(key_var, value_var, frame), ...]

        # 可滚动区域
        scroll_wrap = ttk.Frame(self)
        scroll_wrap.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(scroll_wrap, highlightthickness=0, height=220)
        sb = ttk.Scrollbar(scroll_wrap, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)
        self._inner = ttk.Frame(self._canvas)
        self._canvas_window = self._canvas.create_window(0, 0, window=self._inner, anchor="nw")

        def _on_inner_config(event):
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))
            self._canvas.itemconfig(self._canvas_window, width=self._canvas.winfo_width())
        self._inner.bind("<Configure>", _on_inner_config)

        def _on_canvas_config(event):
            self._canvas.itemconfig(self._canvas_window, width=event.width)
        self._canvas.bind("<Configure>", _on_canvas_config)

        def _on_wheel(event):
            self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self._canvas.bind("<MouseWheel>", _on_wheel)
        self._inner.bind("<MouseWheel>", _on_wheel)

        # 底部添加按钮
        foot = ttk.Frame(self)
        foot.pack(fill="x", pady=(6, 0))
        ttk.Button(foot, text="+ 添加", style="Ghost.TButton",
                   command=self.add_row).pack(side="left")

        # 初始数据
        for k, v in (initial or []):
            self.add_row(k, v)

    def add_row(self, key: str = "", value: str = ""):
        row = ttk.Frame(self._inner)
        row.pack(fill="x", pady=2)
        kv = tk.StringVar(value=key)
        vv = tk.StringVar(value=value)
        ttk.Entry(row, textvariable=kv, width=14).pack(side="left")
        ttk.Entry(row, textvariable=vv).pack(
            side="left", fill="x", expand=True, padx=4)
        def _del(_r=row):
            row.destroy()
            self._rows = [t for t in self._rows if t[2] is not row]
        ttk.Button(row, text="×", width=3, style="Ghost.TButton",
                   command=_del).pack(side="left")
        self._rows.append((kv, vv, row))

    def get_items(self) -> list:
        """返回 [(key, value), ...] 过滤掉 key 为空的行"""
        out = []
        for kv, vv, _ in self._rows:
            k = kv.get().strip()
            v = vv.get().strip()
            if k:
                out.append((k, v))
        return out


class _BoardEditor(ttk.Frame):
    """开发板动态编辑器

    每行: [板名] [IP] [port] [user] [timeout] [×删除]
    密码硬编码,不再在 GUI 中输入
    """

    def __init__(self, master, initial: Optional[list] = None):
        super().__init__(master)

        # 表头
        head = ttk.Frame(self)
        head.pack(fill="x", pady=(0, 4))
        for i, (txt, w) in enumerate([("板名", 12), ("IP", 18), ("端口", 6),
                                      ("用户", 10), ("超时", 6)]):
            ttk.Label(head, text=txt, width=w, anchor="w").pack(side="left", padx=(0, 4))

        self._rows: list = []

        # 可滚动区域
        scroll_wrap = ttk.Frame(self)
        scroll_wrap.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(scroll_wrap, highlightthickness=0, height=220)
        sb = ttk.Scrollbar(scroll_wrap, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)
        self._inner = ttk.Frame(self._canvas)
        self._canvas_window = self._canvas.create_window(0, 0, window=self._inner, anchor="nw")

        def _on_inner_config(event):
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))
            self._canvas.itemconfig(self._canvas_window, width=self._canvas.winfo_width())
        self._inner.bind("<Configure>", _on_inner_config)

        def _on_canvas_config(event):
            self._canvas.itemconfig(self._canvas_window, width=event.width)
        self._canvas.bind("<Configure>", _on_canvas_config)

        def _on_wheel(event):
            self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self._canvas.bind("<MouseWheel>", _on_wheel)
        self._inner.bind("<MouseWheel>", _on_wheel)

        foot = ttk.Frame(self)
        foot.pack(fill="x", pady=(6, 0))
        ttk.Button(foot, text="+ 添加开发板", style="Ghost.TButton",
                   command=self.add_row).pack(side="left")

        for item in (initial or []):
            self.add_row(item.get("name", ""),
                         item.get("ip", ""),
                         str(item.get("port", 22)),
                         item.get("user", "root"),
                         str(item.get("timeout", 8)))

    def add_row(self, name: str = "", ip: str = "", port: str = "22",
                user: str = "root", timeout: str = "8"):
        row = ttk.Frame(self._inner)
        row.pack(fill="x", pady=2)
        nv, iv, pv, uv, tv = (tk.StringVar(value=name), tk.StringVar(value=ip),
                              tk.StringVar(value=port), tk.StringVar(value=user),
                              tk.StringVar(value=timeout))
        ttk.Entry(row, textvariable=nv, width=12).pack(side="left", padx=(0, 4))
        ttk.Entry(row, textvariable=iv, width=18).pack(side="left", padx=(0, 4))
        ttk.Entry(row, textvariable=pv, width=6).pack(side="left", padx=(0, 4))
        ttk.Entry(row, textvariable=uv, width=10).pack(side="left", padx=(0, 4))
        ttk.Entry(row, textvariable=tv, width=6).pack(side="left", padx=(0, 4))
        def _del(_r=row):
            row.destroy()
            self._rows = [t for t in self._rows if t[5] is not row]
        ttk.Button(row, text="×", width=3, style="Ghost.TButton",
                   command=_del).pack(side="left")
        self._rows.append((nv, iv, pv, uv, tv, row))

    def get_items(self) -> list:
        """返回 [{name,ip,port,user,timeout}, ...]"""
        out = []
        for nv, iv, pv, uv, tv, _ in self._rows:
            n = nv.get().strip()
            if not n:
                continue
            out.append({
                "name": n,
                "ip": iv.get().strip(),
                "port": int(pv.get() or 22),
                "user": uv.get().strip() or "root",
                "timeout": int(tv.get() or 8),
            })
        return out

    def get_shared_password(self) -> str:
        return "arcsoft123"


# ---------------------------------------------------------------------------
# config.yaml 读写与转换
# ---------------------------------------------------------------------------

# 项目根目录的 config.yaml 路径
_CONFIG_YAML_PATH = os.path.join(_PROJECT_ROOT, "config.yaml")

# 开发板共享密码(硬编码)
_BOARD_PASSWORD = "arcsoft123"


def _load_yaml(path: str) -> dict:
    """读取 yaml 文件,返回 dict;文件不存在返回空 dict"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _save_yaml(path: str, data: dict):
    """将 dict 写入 yaml 文件"""
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _yaml_to_gui_init(yaml_data: dict) -> dict:
    """从 config.yaml 的 dict 提取 GUI 初始化参数

    Returns:
        {
            "jira": {"username", "password", "base_url", "test_url"},
            "jenkins": {"url", "username", "token"},
            "adas": {"exe_path", "version"},
            "boards": [{"name", "ip", "port", "user", "timeout"}],
            "replay_env": {"source", "point", "username", "password", "domain", "testbed_subpath", "windows_host"},
            "car_models": [(k, v), ...],
            "paths": {"download_root", "output_root"},
        }
    """
    result = {
        "jira": {"username": "", "password": "", "base_url": "", "test_url": ""},
        "jenkins": {"url": "", "username": "", "token": ""},
        "adas": {"exe_path": "", "version": ""},
        "boards": [],
        "replay_env": {"source": "", "point": "", "username": "", "password": "", "domain": "", "testbed_subpath": "", "windows_host": ""},
        "car_models": [],
        "paths": {"download_root": "", "output_root": ""},
    }

    # Jira
    jira = yaml_data.get("jira_data", {})
    result["jira"]["username"] = jira.get("username", "")
    result["jira"]["password"] = jira.get("password", "")
    result["jira"]["base_url"] = jira.get("base_url", "")
    result["jira"]["test_url"] = jira.get("test_url", "")

    # Jenkins
    jen = yaml_data.get("jenkins", {})
    result["jenkins"]["url"] = jen.get("server", "")
    result["jenkins"]["username"] = jen.get("username", "")
    result["jenkins"]["token"] = jen.get("password", "")

    # ADAS
    adas = yaml_data.get("adas", {})
    result["adas"]["exe_path"] = adas.get("exe_path", "")
    # version 不在 adas 段,暂用空字符串

    # Boards
    boards = yaml_data.get("boards", {})
    for name, info in boards.items():
        result["boards"].append({
            "name": name,
            "ip": info.get("host", ""),
            "port": info.get("port", 22),
            "user": info.get("user", "root"),
            "timeout": info.get("timeout", 8),
        })

    # Replay env (合并 mount + replay_env)
    mount = yaml_data.get("mount", {})
    replay = yaml_data.get("replay_env", {})
    result["replay_env"]["source"] = mount.get("source", replay.get("mount_source", ""))
    result["replay_env"]["point"] = mount.get("point", replay.get("mount_point", ""))
    result["replay_env"]["username"] = mount.get("username", "")
    result["replay_env"]["password"] = mount.get("password", "")
    result["replay_env"]["domain"] = mount.get("domain", "")
    result["replay_env"]["testbed_subpath"] = mount.get("testbed_subpath", replay.get("testbed_subpath", ""))
    result["replay_env"]["windows_host"] = replay.get("windows_host", "")

    # Car models
    car_models = yaml_data.get("car_models", {})
    for k, v in car_models.items():
        result["car_models"].append((k, v))

    # Paths
    paths = yaml_data.get("paths", {})
    result["paths"]["download_root"] = paths.get("download_root", "")
    result["paths"]["output_root"] = paths.get("output_root", "")

    return result


def _gui_data_to_yaml(gui_data: dict, existing_yaml: dict) -> dict:
    """将 GUI 数据合并到现有 yaml dict 中(保留不在 GUI 管理的段)

    Returns:
        更新后的完整 yaml dict
    """
    yaml_out = dict(existing_yaml)  # 浅拷贝,保留 replay_list_template 等段

    # Boards
    boards_out = {}
    for item in gui_data.get("boards", {}).get("items", []):
        name = item["name"]
        boards_out[name] = {
            "host": item["ip"],
            "port": item["port"],
            "user": item["user"],
            "password": _BOARD_PASSWORD,
            "timeout": item["timeout"],
        }
    yaml_out["boards"] = boards_out

    # Mount (从 replay_env 拆出)
    env = gui_data.get("replay_env", {})
    yaml_out["mount"] = {
        "source": env.get("source", ""),
        "point": env.get("point", ""),
        "username": env.get("username", ""),
        "password": env.get("password", ""),
        "domain": env.get("domain", ""),
        "testbed_subpath": env.get("testbed_subpath", ""),
    }

    # Replay env (同时更新,保持一致)
    yaml_out["replay_env"] = {
        "mount_source": env.get("source", ""),
        "mount_point": env.get("point", ""),
        "mount_options": f"username={env.get('username', '')},password={env.get('password', '')},domain={env.get('domain', '')}",
        "testbed_subpath": env.get("testbed_subpath", ""),
        "windows_host": env.get("windows_host", ""),
    }

    # Car models
    car_out = {}
    for k, v in gui_data.get("car_models", []):
        car_out[k] = v
    yaml_out["car_models"] = car_out

    # Jira
    jira = gui_data.get("jira", {})
    yaml_out["jira_data"] = {
        "base_url": jira.get("base_url", ""),
        "test_url": jira.get("test_url", ""),
        "username": jira.get("username", ""),
        "password": jira.get("password", ""),
        "max_workers": existing_yaml.get("jira_data", {}).get("max_workers", 5),
    }

    # Jenkins
    jen = gui_data.get("jenkins", {})
    existing_jen = existing_yaml.get("jenkins", {})
    yaml_out["jenkins"] = {
        "server": jen.get("url", ""),
        "username": jen.get("username", ""),
        "password": jen.get("token", ""),
        "download_dir": existing_jen.get("download_dir", ""),
        "default_job": existing_jen.get("default_job", ""),
    }

    # ADAS
    adas = gui_data.get("adas", {})
    existing_adas = existing_yaml.get("adas", {})
    yaml_out["adas"] = {
        "exe_path": adas.get("exe_path", ""),
        "verbose": existing_adas.get("verbose", 3),
        "timeout": existing_adas.get("timeout", 300),
        "max_workers": existing_adas.get("max_workers", 4),
    }

    # Paths (新增段)
    paths = gui_data.get("paths", {})
    yaml_out["paths"] = {
        "download_root": paths.get("download_root", ""),
        "output_root": paths.get("output_root", ""),
    }

    return yaml_out


class SettingsDialog(tk.Toplevel):
    def __init__(self, master: tk.Tk, on_save: Optional[Callable[[dict], None]] = None,
                 config_path: str = None):
        super().__init__(master)
        self.title("⚙ 全局设置")
        self.geometry("680x780")
        self.minsize(640, 700)
        self.transient(master)
        self.grab_set()
        self._on_save_cb = on_save
        self._config_path = config_path or _CONFIG_YAML_PATH

        # 读取现有 config.yaml 作为初始化数据
        yaml_data = _load_yaml(self._config_path)
        self._yaml_data = yaml_data
        init_data = _yaml_to_gui_init(yaml_data)

        # 底部按钮 (必须先于 notebook pack, 否则 expand=True 的 notebook 会把它挤出窗口)
        footer = ttk.Frame(self)
        footer.pack(side="bottom", fill="x", padx=10, pady=(0, 12))
        ttk.Button(footer, text="💾 保存到 config.yaml", style="Primary.TButton",
                   command=self._on_save).pack(side="right")
        ttk.Button(footer, text="📥 导入配置", style="Ghost.TButton",
                   command=self._on_import).pack(side="right", padx=8)
        ttk.Button(footer, text="📤 导出配置", style="Ghost.TButton",
                   command=self._on_export).pack(side="right", padx=8)
        ttk.Button(footer, text="取消", style="Ghost.TButton",
                   command=self.destroy).pack(side="left")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        # --- Jira ---
        jira = ttk.Frame(nb, padding=10)
        nb.add(jira, text=" Jira ")
        _j = init_data["jira"]
        self.jira_user = self._row(jira, "用户名:", _j["username"])
        self.jira_pwd = self._row(jira, "密码:", _j["password"], show="*")
        self.jira_base = self._row(jira, "服务前缀:", _j["base_url"])
        self.jira_test = self._row(jira, "验证 URL:", _j["test_url"])

        # --- Jenkins ---
        jen = ttk.Frame(nb, padding=10)
        nb.add(jen, text=" Jenkins ")
        _je = init_data["jenkins"]
        self.jen_url = self._row(jen, "Jenkins URL:", _je["url"])
        self.jen_user = self._row(jen, "用户名:", _je["username"])
        self.jen_token = self._row(jen, "Token/密码:", _je["token"], show="*")

        # --- ADAS ---
        adas = ttk.Frame(nb, padding=10)
        nb.add(adas, text=" ADAS ")
        self.adas_exe = _PathRow(adas, "预处理 exe:", pick="file",
                                 filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")],
                                 default=init_data["adas"]["exe_path"])
        self.adas_exe.pack(fill="x", pady=4)
        self.adas_ver = self._row(adas, "版本号:", init_data["adas"]["version"])

        # --- 开发板 (动态,密码硬编码) ---
        boards_f = ttk.Frame(nb, padding=10)
        nb.add(boards_f, text=" 开发板 ")
        self.board_editor = _BoardEditor(
            boards_f,
            initial=init_data["boards"],
        )
        self.board_editor.pack(fill="both", expand=True)

        # --- 回灌环境 (合并 mount + replay_env) ---
        env_f = ttk.Frame(nb, padding=10)
        nb.add(env_f, text=" 回灌环境 ")
        _env = init_data["replay_env"]
        self.replay_source = self._row(env_f, "挂载源 (source):", _env["source"])
        self.replay_point = self._row(env_f, "挂载点 (point):", _env["point"])
        self.replay_user = self._row(env_f, "用户名:", _env["username"])
        self.replay_pwd = self._row(env_f, "密码:", _env["password"])
        self.replay_domain = self._row(env_f, "域 (domain):", _env["domain"])
        self.replay_subpath = self._row(env_f, "testbed 子路径:", _env["testbed_subpath"])
        self.replay_win_host = self._row(env_f, "Windows 主机名:", _env["windows_host"])
        ttk.Label(env_f, text="说明: 合并原 mount + replay_env 配置。Windows UNC 路径 = "
                  "\\\\windows_host\\source去掉//IP/\\testbed_subpath\\回灌文件夹",
                  style="Hint.TLabel", wraplength=560, justify="left").pack(
            fill="x", pady=(8, 0))

        # --- 车型-标定映射 (动态) ---
        car_f = ttk.Frame(nb, padding=10)
        nb.add(car_f, text=" 车型-标定映射 ")
        self.car_editor = _DynKVEditor(
            car_f, key_label="车型", value_label="标定名称",
            initial=init_data["car_models"],
        )
        self.car_editor.pack(fill="both", expand=True)

        # --- 默认路径 ---
        path_f = ttk.Frame(nb, padding=10)
        nb.add(path_f, text=" 默认路径 ")
        _paths = init_data["paths"]
        self.dl_root = _PathRow(path_f, "下载根目录:", pick="dir",
                                default=_paths["download_root"])
        self.dl_root.pack(fill="x", pady=4)
        self.out_root = _PathRow(path_f, "输出根目录:", pick="dir",
                                 default=_paths["output_root"])
        self.out_root.pack(fill="x", pady=4)

    def _row(self, parent, label: str, default: str = "", show: str = "") -> tk.StringVar:
        r = ttk.Frame(parent)
        r.pack(fill="x", pady=4)
        ttk.Label(r, text=label, width=18, anchor="w").pack(side="left")
        var = tk.StringVar(value=default)
        entry = ttk.Entry(r, textvariable=var, show=show or "")
        entry.pack(side="left", fill="x", expand=True)
        return var

    def _collect(self) -> dict:
        return {
            "jira": {"username": self.jira_user.get(),
                     "password": self.jira_pwd.get(),
                     "base_url": self.jira_base.get(),
                     "test_url": self.jira_test.get()},
            "jenkins": {"url": self.jen_url.get(),
                        "username": self.jen_user.get(),
                        "token": self.jen_token.get()},
            "adas": {"exe_path": self.adas_exe.get(),
                     "version": self.adas_ver.get()},
            "boards": {
                "shared_password": self.board_editor.get_shared_password(),
                "items": self.board_editor.get_items(),
            },
            "replay_env": {
                "source": self.replay_source.get(),
                "point": self.replay_point.get(),
                "username": self.replay_user.get(),
                "password": self.replay_pwd.get(),
                "domain": self.replay_domain.get(),
                "testbed_subpath": self.replay_subpath.get(),
                "windows_host": self.replay_win_host.get(),
            },
            "car_models": self.car_editor.get_items(),
            "paths": {"download_root": self.dl_root.get(),
                      "output_root": self.out_root.get()},
        }

    def _on_save(self):
        gui_data = self._collect()
        try:
            yaml_out = _gui_data_to_yaml(gui_data, self._yaml_data)
            _save_yaml(self._config_path, yaml_out)
            self._yaml_data = yaml_out  # 更新缓存
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return
        if self._on_save_cb:
            try:
                self._on_save_cb(gui_data)
            except Exception:
                pass
        messagebox.showinfo("设置", f"已保存到 {self._config_path}")
        self.destroy()

    def _on_import(self):
        p = filedialog.askopenfilename(filetypes=[("YAML 文件", "*.yaml *.yml")])
        if not p:
            return
        yaml_data = _load_yaml(p)
        if not yaml_data:
            messagebox.showwarning("导入", f"文件为空或读取失败: {p}")
            return
        init_data = _yaml_to_gui_init(yaml_data)
        self._apply_init_data(init_data)
        self._yaml_data = yaml_data
        messagebox.showinfo("导入", f"已从 {p} 导入配置")

    def _on_export(self):
        p = filedialog.asksaveasfilename(filetypes=[("YAML 文件", "*.yaml")],
                                         defaultextension=".yaml")
        if not p:
            return
        gui_data = self._collect()
        try:
            yaml_out = _gui_data_to_yaml(gui_data, self._yaml_data)
            _save_yaml(p, yaml_out)
            messagebox.showinfo("导出", f"已导出到 {p}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def _apply_init_data(self, data: dict):
        """将初始化数据填充到 GUI 控件"""
        jira = data.get("jira", {})
        self.jira_user.set(jira.get("username", ""))
        self.jira_pwd.set(jira.get("password", ""))
        self.jira_base.set(jira.get("base_url", ""))
        self.jira_test.set(jira.get("test_url", ""))

        jen = data.get("jenkins", {})
        self.jen_url.set(jen.get("url", ""))
        self.jen_user.set(jen.get("username", ""))
        self.jen_token.set(jen.get("token", ""))

        adas = data.get("adas", {})
        self.adas_exe.set(adas.get("exe_path", ""))
        self.adas_ver.set(adas.get("version", ""))

        # Boards: 清空现有行,重新填充
        for _, _, _, _, _, row in self.board_editor._rows:
            row.destroy()
        self.board_editor._rows.clear()
        for item in data.get("boards", []):
            self.board_editor.add_row(
                item["name"], item["ip"], str(item["port"]),
                item["user"], str(item["timeout"]))

        # Replay env
        env = data.get("replay_env", {})
        self.replay_source.set(env.get("source", ""))
        self.replay_point.set(env.get("point", ""))
        self.replay_user.set(env.get("username", ""))
        self.replay_pwd.set(env.get("password", ""))
        self.replay_domain.set(env.get("domain", ""))
        self.replay_subpath.set(env.get("testbed_subpath", ""))
        self.replay_win_host.set(env.get("windows_host", ""))

        # Car models: 清空现有行,重新填充
        for _, _, row in self.car_editor._rows:
            row.destroy()
        self.car_editor._rows.clear()
        for k, v in data.get("car_models", []):
            self.car_editor.add_row(k, v)

        # Paths
        paths = data.get("paths", {})
        self.dl_root.set(paths.get("download_root", ""))
        self.out_root.set(paths.get("output_root", ""))


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class App(tk.Tk):
    """主窗口"""

    # GitHub 仓库信息 (用于自动更新)
    _GH_OWNER = "SYC-OldCat"
    _GH_REPO = "devboard-toolkit"
    _GH_BRANCH = "master"
    _EXE_NAME = "devboard_toolkit.exe"

    def __init__(self):
        super().__init__()
        self.title("DevBoard Toolkit")
        self.geometry("1100x820")
        self.minsize(1000, 740)
        _setup_style(self)

        # 顶部标题栏
        header = ttk.Frame(self)
        header.pack(fill="x", padx=16, pady=(12, 0))
        ttk.Label(header, text="🛠️  DevBoard Toolkit",
                  style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="  v0.2.0  ·  数据处理 + 感知包编译 + 自动回灌 + 自动更新",
                  style="Hint.TLabel").pack(side="left", pady=(6, 0))
        ttk.Button(header, text="⚙ 设置", style="Ghost.TButton",
                   command=self._open_settings).pack(side="right")

        ttk.Separator(self).pack(fill="x", padx=12, pady=10)

        # Notebook
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.tab_data = TabDataProcessing(nb)
        self.tab_build = TabJenkinsBuild(nb)
        self.tab_feed = TabFeedback(nb)
        self.tab_pipe = TabPipeline(nb)

        nb.add(self.tab_data, text="  📦 数据处理  ")
        nb.add(self.tab_build, text="  🚀 感知包编译  ")
        nb.add(self.tab_feed, text="  🔁 自动回灌  ")
        nb.add(self.tab_pipe, text="  ⛓️ 组合流水线  ")

        # 状态栏
        status = ttk.Frame(self)
        status.pack(fill="x", padx=14, pady=(0, 10))
        self._status_var = tk.StringVar(value="就绪")
        ttk.Label(status, textvariable=self._status_var,
                  style="Hint.TLabel").pack(side="left")
        ttk.Label(status, text="4 Tab 已连接实际功能",
                  style="Hint.TLabel").pack(side="right")

        # exe 模式: 启动后 2s 后台检测更新 (不阻塞 GUI)
        if getattr(sys, 'frozen', False):
            self.after(2000, self._check_update)

    # ------------------------------------------------------------------
    # 自动更新
    # ------------------------------------------------------------------

    def _read_local_version(self) -> str:
        """读取本地 _version.txt (打包时注入的 git commit hash)"""
        exe_dir = os.path.dirname(sys.executable)
        ver_path = os.path.join(exe_dir, "_version.txt")
        try:
            with open(ver_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""

    def _check_update(self):
        """后台检测 GitHub Releases Atom Feed (无API限流)"""
        print("[更新] 启动版本检测...", flush=True)
        local_hash = self._read_local_version()
        print(f"[更新] 本地版本: {local_hash or '(无)'}", flush=True)
        
        if not local_hash:
            print("[更新] 无 _version.txt, 跳过检测", flush=True)
            return

        def _worker():
            import re
            import urllib.request
            import urllib.error

            # 使用 Releases Atom Feed (无频率限制, 不占 API 配额)
            atom_url = (f"https://github.com/{self._GH_OWNER}/"
                       f"{self._GH_REPO}/releases.atom")
            print(f"[更新] 请求: {atom_url}", flush=True)

            try:
                req = urllib.request.Request(atom_url, headers={
                    "User-Agent": "devboard-toolkit-updater",
                })
                with urllib.request.urlopen(req, timeout=10) as resp:
                    atom_xml = resp.read().decode("utf-8")
                print("[更新] Atom Feed 请求成功", flush=True)
            except Exception as e:
                print(f"[更新] 请求失败: {e}", flush=True)
                return

            # 从 Atom Feed 的第一个 <link rel="alternate"> 提取 tag
            # 格式: href=".../releases/tag/v20260820_0957_98453732"
            tag = ""
            m = re.search(r'href="[^"]*/releases/tag/([^"]+)"', atom_xml)
            if m:
                tag = m.group(1).strip()

            # 有时 title 包含 HTML 标签, 清理
            tag = re.sub(r"<[^>]+>", "", tag).strip()

            remote_hash = ""
            if tag.startswith("v"):
                # tag 格式: v20260820_0957_98453732 → 取最后一段
                parts = tag.split("_")
                if len(parts) >= 3:
                    remote_hash = parts[-1]
                else:
                    remote_hash = tag[1:]
            else:
                remote_hash = tag

            print(f"[更新] 远程版本: {remote_hash or '(无)'} (tag: {tag})", flush=True)
            if not remote_hash:
                print("[更新] 无远程版本, 跳过", flush=True)
                return

            # 比较: local_hash 前 8 位 == remote_hash
            if local_hash[:8] == remote_hash[:8]:
                print("[更新] 已是最新版本", flush=True)
                return

            print(f"[更新] 发现新版本! 本地={local_hash[:8]} 远程={remote_hash[:8]}", flush=True)

            # 更新说明: 从 Atom content 提取
            commit_msg = f"版本 {remote_hash[:7]}"
            # 尝试提取第一个 release body 文本
            content_m = re.search(
                r"<content[^>]*>(.*?)</content>", atom_xml, re.DOTALL)
            if content_m:
                body = re.sub(r"<[^>]+>", "", content_m.group(1)).strip()
                if body:
                    commit_msg = body.split("\n")[0][:60] or commit_msg

            self.after(0, lambda: self._prompt_update(
                remote_hash, commit_msg, tag))

        threading.Thread(target=_worker, daemon=True).start()

    def _prompt_update(self, remote_hash: str, commit_msg: str,
                       release_tag: str = ""):
        """弹窗提示用户是否更新"""
        msg = (f"发现新版本\n\n"
               f"当前版本: {self._read_local_version()[:8]}\n"
               f"最新版本: {remote_hash[:8]}\n"
               f"更新内容: {commit_msg}\n\n"
               f"是否立即下载并更新？")
        rc = messagebox.askyesno("自动更新", msg, default="yes")
        if rc:
            self._do_update(remote_hash, release_tag)

    def _do_update(self, remote_hash: str, release_tag: str = ""):
        """下载新 exe + _version.txt + 写 .bat 替换脚本 + 重启"""
        import urllib.request
        import urllib.error
        import tempfile

        # 如果没有传 tag, 从 remote_hash 推导 (格式: v20260820_HHmm_hash)
        if not release_tag:
            release_tag = f"v_auto_{remote_hash[:8]}"

        # 用 tag 直接构造下载 URL (无需 API, 无频率限制)
        base_dl = f"https://github.com/{self._GH_OWNER}/{self._GH_REPO}/releases/download/{release_tag}"
        exe_url = f"{base_dl}/{self._EXE_NAME}"
        ver_url = f"{base_dl}/_version.txt"

        # 2. 弹出下载进度窗口
        dl_win = tk.Toplevel(self)
        dl_win.title("正在更新")
        dl_win.geometry("420x160")
        dl_win.resizable(False, False)
        dl_win.transient(self)
        dl_win.grab_set()

        ttk.Label(dl_win, text=f"正在下载 {release_tag}...",
                  style="SubTitle.TLabel").pack(pady=(16, 8))
        prog = ttk.Progressbar(dl_win, mode="determinate",
                               style="Horizontal.TProgressbar")
        prog.pack(fill="x", padx=24, pady=4)
        pct_var = tk.StringVar(value="准备中...")
        ttk.Label(dl_win, textvariable=pct_var,
                  style="Hint.TLabel").pack(pady=4)

        def _download_thread():
            try:
                tmp_dir = tempfile.gettempdir()
                tmp_exe = os.path.join(tmp_dir, f"{self._EXE_NAME}.new")
                tmp_ver = os.path.join(tmp_dir, "_version.new.txt")

                # 下载 exe (大文件, 带进度)
                req = urllib.request.Request(exe_url, headers={
                    "User-Agent": "devboard-toolkit-updater",
                })
                with urllib.request.urlopen(req, timeout=60) as resp:
                    total = int(resp.headers.get("Content-Length", 0))
                    if total > 0:
                        prog.configure(maximum=total)
                    downloaded = 0
                    with open(tmp_exe, "wb") as out:
                        while True:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            out.write(chunk)
                            downloaded += len(chunk)
                            if total > 0:
                                pct = int(downloaded / total * 100)
                                self.after(0, lambda p=pct: (
                                    prog.configure(value=p),
                                    pct_var.set(f"下载中 {p}%")
                                ))

                # 下载 _version.txt (小文件)
                new_hash = remote_hash
                if ver_url:
                    self.after(0, lambda: pct_var.set("下载版本信息..."))
                    req2 = urllib.request.Request(ver_url, headers={
                        "User-Agent": "devboard-toolkit-updater",
                    })
                    with urllib.request.urlopen(req2, timeout=15) as resp2:
                        new_hash = resp2.read().decode("utf-8").strip()
                    with open(tmp_ver, "w", encoding="utf-8") as vf:
                        vf.write(new_hash)

                # 下载完成 → 写 .bat 替换脚本
                self.after(0, lambda: pct_var.set("准备替换..."))
                self.after(0, lambda: self._swap_and_restart(
                    tmp_exe, tmp_ver if ver_url else None,
                    new_hash, dl_win))
            except Exception as e:
                self.after(0, lambda: (
                    dl_win.destroy(),
                    messagebox.showerror("更新失败", f"下载失败:\n{e}")
                ))

        threading.Thread(target=_download_thread, daemon=True).start()

    def _swap_and_restart(self, new_exe: str, new_ver: str,
                          new_hash: str, dl_win):
        """生成 .bat 脚本: 等待退出→替换→重启 (explorer 等同双击, 最可靠)"""
        import tempfile

        dl_win.destroy()

        current_exe = sys.executable
        exe_dir = os.path.dirname(current_exe)
        target_path = os.path.join(exe_dir, self._EXE_NAME)
        version_path = os.path.join(exe_dir, "_version.txt")
        current_pid = os.getpid()
        bat_path = os.path.join(tempfile.gettempdir(), "devboard_updater.bat")
        log_path = os.path.join(tempfile.gettempdir(), "devboard_updater.log")

        # 关键: 用 explorer 启动新 exe (等同双击, 绕过 start 的各种坑)
        # explorer 会用 Windows Shell 打开 .exe, 100% 可靠
        safe_log = log_path.replace('\\', '\\\\')
        bat_lines = [
            "@echo off",
            f'echo [{time.strftime("%H:%M:%S")}] 开始更新 > "{safe_log}"',
            f'echo 目标: {target_path} >> "{log_path}"',
            f'echo 新文件: {new_exe} >> "{log_path}"',
            "",
            ":wait",
            f'tasklist /FI "PID eq {current_pid}" 2>nul | find "{current_pid}" >nul',
            "if %ERRORLEVEL%==0 (",
            f'    echo 等待进程 {current_pid} 退出... >> "{log_path}"',
            "    timeout /t 1 >nul",
            "    goto wait",
            ")",
            "",
            "timeout /t 2 >nul",
            "",
            f'echo 正在替换... >> "{log_path}"',
            f'copy /Y "{new_exe}" "{target_path}" >> "{log_path}" 2>&1',
            "if %ERRORLEVEL% neq 0 (",
            f'    echo [FAIL] 替换失败 >> "{log_path}"',
            "    pause",
            "    exit /b 1",
            ")",
            f'del "{new_exe}" 2>nul',
        ]

        if new_ver:
            bat_lines.extend([
                f'copy /Y "{new_ver}" "{version_path}" >> "{log_path}" 2>&1',
                f'del "{new_ver}" 2>nul',
            ])
        else:
            bat_lines.append(f'echo {new_hash}> "{version_path}"')

        bat_lines.extend([
            f'echo 更新完成 >> "{log_path}"',
            # 用 explorer 启动 (等同双击文件, 最可靠)
            f'explorer "{target_path}"',
            f'timeout /t 1 >nul',
            f'del "{bat_path}"',
        ])

        bat_content = "\r\n".join(bat_lines)

        try:
            with open(bat_path, "w", encoding="gbk") as f:
                f.write(bat_content)
            if not os.path.isfile(bat_path):
                raise RuntimeError(".bat 文件创建失败")
        except Exception as e:
            messagebox.showerror("更新失败", f"无法创建更新脚本:\n{e}")
            return

        # 用 cmd /c 启动 bat (最简方式)
        import subprocess
        subprocess.Popen(
            ["cmd", "/c", bat_path],
            cwd=tempfile.gettempdir(),
            creationflags=subprocess.CREATE_NO_WINDOW,
            close_fds=True,
        )

        # 延迟退出, 确保 bat 已启动并进入 wait 循环
        self.after(800, self.quit)

    def _open_settings(self):
        SettingsDialog(self, on_save=self._save_settings, config_path=_CONFIG_YAML_PATH)

    def _save_settings(self, data: dict):
        """演示占位: 真正实现时这里写 config.yaml"""
        self._status_var.set(
            f"设置已保存: Jira={data['jira']['username']}, "
            f"Jenkins={data['jenkins']['url']}")


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
