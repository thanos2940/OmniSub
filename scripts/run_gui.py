"""
OmniSub Launcher - Graphical Dashboard for Backend & Frontend runners.
Features:
- Dual split-view terminal consoles with colored logging and auto-scroll
- Start/Stop/Restart control for both Backend and Frontend independently or together
- Direct "Open in Browser" button (http://localhost:5173 and http://localhost:8000/docs)
- Status indicators (Running / Stopped / Error)
- Full text selection and copying support (Ctrl+C, right-click context menu, and toolbar Copy button)
- Clean shutdown on window close ensuring all child processes & ports are killed cleanly.
"""

import sys
import os
import subprocess
import threading
import queue
import time
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox
import urllib.request

# Base paths
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")
FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend")

# Virtual environment python
VENV_PYTHON = os.path.join(ROOT_DIR, ".venv", "Scripts", "python.exe")
if not os.path.exists(VENV_PYTHON):
    VENV_PYTHON = sys.executable

# Colors & theme
BG_DARK = "#121820"
BG_PANEL = "#1a222d"
BG_CONSOLE = "#0e1319"
FG_TEXT = "#e2e8f0"
FG_MUTED = "#94a3b8"
ACCENT_BLUE = "#3b82f6"
ACCENT_BLUE_HOVER = "#2563eb"
ACCENT_GREEN = "#10b981"
ACCENT_RED = "#ef4444"
ACCENT_AMBER = "#f59e0b"
BORDER_COLOR = "#2d3748"

FONT_MAIN = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 12, "bold")
FONT_CONSOLE = ("Consolas", 9)


class ProcessRunner:
    """Manages spawning, streaming output, and terminating a subprocess."""
    def __init__(self, name, cmd, cwd, output_queue, tag):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.output_queue = output_queue
        self.tag = tag
        self.process = None
        self.is_running = False
        self._threads = []

    def start(self):
        if self.is_running:
            return

        try:
            # Set environment with unbuffered stdout for python
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            
            # Start process with creationflags for Windows process group
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

            self.process = subprocess.Popen(
                self.cmd,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=creation_flags
            )
            self.is_running = True
            self.output_queue.put((self.tag, f"[{self.name}] Process started (PID: {self.process.pid})...\n", "system"))

            t = threading.Thread(target=self._reader_thread, daemon=True)
            t.start()
            self._threads.append(t)
        except Exception as e:
            self.is_running = False
            self.output_queue.put((self.tag, f"[{self.name} ERROR] Failed to start: {e}\n", "error"))

    def _reader_thread(self):
        try:
            for line in iter(self.process.stdout.readline, ''):
                if not line:
                    break
                self.output_queue.put((self.tag, line, "normal"))
        except Exception:
            pass
        finally:
            if self.process:
                self.process.stdout.close()
                code = self.process.wait()
                self.is_running = False
                self.output_queue.put((self.tag, f"\n[{self.name}] Process exited with code {code}.\n", "system"))

    def stop(self):
        if not self.is_running or not self.process:
            self.is_running = False
            return

        pid = self.process.pid
        self.output_queue.put((self.tag, f"[{self.name}] Stopping process (PID: {pid})...\n", "system"))
        try:
            if sys.platform == "win32":
                # Use taskkill to terminate tree
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False
                )
            else:
                self.process.terminate()
        except Exception as e:
            self.output_queue.put((self.tag, f"[{self.name} ERROR] Error killing process: {e}\n", "error"))
        finally:
            self.is_running = False


class OmniSubLauncherGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("OmniSub Services Dashboard")
        self.root.geometry("1100x720")
        self.root.minsize(850, 500)
        self.root.configure(bg=BG_DARK)

        # Queue for cross-thread log streaming
        self.log_queue = queue.Queue()

        # Commands setup
        backend_cmd = [VENV_PYTHON, "-m", "uvicorn", "main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"]
        frontend_cmd = ["cmd.exe", "/c", "npm", "run", "dev"] if sys.platform == "win32" else ["npm", "run", "dev"]

        self.backend_runner = ProcessRunner("Backend", backend_cmd, BACKEND_DIR, self.log_queue, "backend")
        self.frontend_runner = ProcessRunner("Frontend", frontend_cmd, FRONTEND_DIR, self.log_queue, "frontend")

        self.auto_scroll_backend = tk.BooleanVar(value=True)
        self.auto_scroll_frontend = tk.BooleanVar(value=True)

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Start queue poller and auto-start services
        self.root.after(100, self._process_log_queue)
        self.root.after(300, self.start_all)
        self.root.after(1000, self._health_check_loop)

    def _build_ui(self):
        # Header / Control Bar
        header = tk.Frame(self.root, bg=BG_PANEL, height=60, bd=0, highlightbackground=BORDER_COLOR, highlightthickness=1)
        header.pack(fill=tk.X, side=tk.TOP, padx=10, pady=(10, 5))

        # Title & Subtitle
        title_box = tk.Frame(header, bg=BG_PANEL)
        title_box.pack(side=tk.LEFT, padx=15, pady=8)

        lbl_title = tk.Label(
            title_box, 
            text="OmniSub Dev Manager", 
            font=("Segoe UI", 13, "bold"), 
            fg=FG_TEXT, 
            bg=BG_PANEL
        )
        lbl_title.pack(anchor="w")

        lbl_sub = tk.Label(
            title_box, 
            text="FastAPI Backend & Vite React Frontend", 
            font=("Segoe UI", 8), 
            fg=FG_MUTED, 
            bg=BG_PANEL
        )
        lbl_sub.pack(anchor="w")

        # Global Control Buttons
        btn_frame = tk.Frame(header, bg=BG_PANEL)
        btn_frame.pack(side=tk.RIGHT, padx=15, pady=8)

        self.btn_open_app = tk.Button(
            btn_frame,
            text="🌐 Open App (5173)",
            bg=ACCENT_BLUE,
            fg="white",
            activebackground=ACCENT_BLUE_HOVER,
            activeforeground="white",
            relief=tk.FLAT,
            font=FONT_BOLD,
            cursor="hand2",
            padx=12,
            pady=4,
            command=lambda: webbrowser.open("http://localhost:5173")
        )
        self.btn_open_app.pack(side=tk.LEFT, padx=5)

        self.btn_open_docs = tk.Button(
            btn_frame,
            text="📖 API Docs (8000)",
            bg="#334155",
            fg=FG_TEXT,
            activebackground="#475569",
            activeforeground="white",
            relief=tk.FLAT,
            font=FONT_MAIN,
            cursor="hand2",
            padx=10,
            pady=4,
            command=lambda: webbrowser.open("http://localhost:8000/docs")
        )
        self.btn_open_docs.pack(side=tk.LEFT, padx=5)

        self.btn_restart_all = tk.Button(
            btn_frame,
            text="🔄 Restart All",
            bg="#475569",
            fg="white",
            relief=tk.FLAT,
            font=FONT_MAIN,
            cursor="hand2",
            padx=10,
            pady=4,
            command=self.restart_all
        )
        self.btn_restart_all.pack(side=tk.LEFT, padx=5)

        self.btn_stop_all = tk.Button(
            btn_frame,
            text="⏹ Stop All",
            bg="#dc2626",
            fg="white",
            relief=tk.FLAT,
            font=FONT_MAIN,
            cursor="hand2",
            padx=10,
            pady=4,
            command=self.stop_all
        )
        self.btn_stop_all.pack(side=tk.LEFT, padx=5)

        # Main Paned Window (Split into Backend Terminal and Frontend Terminal)
        paned = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, bg=BG_DARK, bd=0, sashwidth=6, sashrelief=tk.FLAT)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Backend Panel
        self.backend_panel, self.backend_text, self.backend_status_lbl = self._create_terminal_panel(
            parent=paned,
            title="Backend Service (Port 8000)",
            tag="backend",
            auto_scroll_var=self.auto_scroll_backend,
            start_fn=self.start_backend,
            stop_fn=self.stop_backend,
            restart_fn=self.restart_backend,
            clear_fn=lambda: self._clear_console(self.backend_text)
        )
        paned.add(self.backend_panel, minsize=380, stretch="always")

        # Frontend Panel
        self.frontend_panel, self.frontend_text, self.frontend_status_lbl = self._create_terminal_panel(
            parent=paned,
            title="Frontend Service (Port 5173)",
            tag="frontend",
            auto_scroll_var=self.auto_scroll_frontend,
            start_fn=self.start_frontend,
            stop_fn=self.stop_frontend,
            restart_fn=self.restart_frontend,
            clear_fn=lambda: self._clear_console(self.frontend_text)
        )
        paned.add(self.frontend_panel, minsize=380, stretch="always")

    def _create_terminal_panel(self, parent, title, tag, auto_scroll_var, start_fn, stop_fn, restart_fn, clear_fn):
        frame = tk.Frame(parent, bg=BG_PANEL, highlightbackground=BORDER_COLOR, highlightthickness=1)

        # Top bar of the sub-panel
        top_bar = tk.Frame(frame, bg=BG_PANEL)
        top_bar.pack(fill=tk.X, padx=10, pady=6)

        # Title & Status Dot
        left_box = tk.Frame(top_bar, bg=BG_PANEL)
        left_box.pack(side=tk.LEFT)

        status_dot = tk.Label(left_box, text="●", fg=ACCENT_AMBER, bg=BG_PANEL, font=("Segoe UI", 12))
        status_dot.pack(side=tk.LEFT, padx=(0, 4))

        lbl = tk.Label(left_box, text=title, font=FONT_BOLD, fg=FG_TEXT, bg=BG_PANEL)
        lbl.pack(side=tk.LEFT)

        # Actions on the right
        right_box = tk.Frame(top_bar, bg=BG_PANEL)
        right_box.pack(side=tk.RIGHT)

        btn_start = tk.Button(right_box, text="▶ Start", bg="#1e293b", fg=ACCENT_GREEN, relief=tk.FLAT, font=FONT_MAIN, cursor="hand2", padx=6, pady=2, command=start_fn)
        btn_start.pack(side=tk.LEFT, padx=2)

        btn_stop = tk.Button(right_box, text="⏹ Stop", bg="#1e293b", fg=ACCENT_RED, relief=tk.FLAT, font=FONT_MAIN, cursor="hand2", padx=6, pady=2, command=stop_fn)
        btn_stop.pack(side=tk.LEFT, padx=2)

        btn_restart = tk.Button(right_box, text="🔄", bg="#1e293b", fg=FG_TEXT, relief=tk.FLAT, font=FONT_MAIN, cursor="hand2", padx=6, pady=2, command=restart_fn)
        btn_restart.pack(side=tk.LEFT, padx=2)

        # Dedicated Copy Button
        btn_copy = tk.Button(
            right_box,
            text="📋 Copy",
            bg="#1e293b",
            fg="#60a5fa",
            relief=tk.FLAT,
            font=FONT_MAIN,
            cursor="hand2",
            padx=6,
            pady=2,
            command=lambda: self._copy_terminal_content(text)
        )
        btn_copy.pack(side=tk.LEFT, padx=2)

        btn_clear = tk.Button(right_box, text="Clear", bg="#1e293b", fg=FG_MUTED, relief=tk.FLAT, font=FONT_MAIN, cursor="hand2", padx=6, pady=2, command=clear_fn)
        btn_clear.pack(side=tk.LEFT, padx=2)

        chk_scroll = tk.Checkbutton(
            right_box, 
            text="Autoscroll", 
            variable=auto_scroll_var, 
            bg=BG_PANEL, 
            fg=FG_MUTED, 
            selectcolor=BG_DARK, 
            activebackground=BG_PANEL,
            activeforeground=FG_TEXT,
            font=("Segoe UI", 8)
        )
        chk_scroll.pack(side=tk.LEFT, padx=(6, 0))

        # Console Text Box
        console_container = tk.Frame(frame, bg=BG_CONSOLE)
        console_container.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        scroll = tk.Scrollbar(console_container)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        text = tk.Text(
            console_container,
            bg=BG_CONSOLE,
            fg=FG_TEXT,
            insertbackground="white",
            font=FONT_CONSOLE,
            relief=tk.FLAT,
            wrap=tk.WORD,
            yscrollcommand=scroll.set,
            bd=0,
            padx=8,
            pady=8,
            exportselection=False,  # Retains selection across focus changes
            selectbackground="#2563eb",  # Bright blue high-contrast selection
            selectforeground="#ffffff",
            inactiveselectbackground="#1d4ed8"
        )
        text.pack(fill=tk.BOTH, expand=True)
        scroll.config(command=text.yview)

        # Configure styles / tags
        text.tag_config("normal", foreground="#cbd5e1")
        text.tag_config("system", foreground="#38bdf8", font=("Consolas", 9, "bold"))
        text.tag_config("error", foreground="#f87171")
        text.tag_config("warn", foreground="#fbbf24")
        text.tag_config("success", foreground="#4ade80")

        # Setup full copy, selection, and context menu support
        self._setup_terminal_copy_features(text)

        return frame, text, status_dot

    def _setup_terminal_copy_features(self, text_widget):
        """Enable keyboard shortcuts (Ctrl+C, Ctrl+A) and right-click context menu."""

        def copy_selection(event=None):
            try:
                selected_text = text_widget.get(tk.SEL_FIRST, tk.SEL_LAST)
            except tk.TclError:
                selected_text = None

            if selected_text:
                self.root.clipboard_clear()
                self.root.clipboard_append(selected_text)
            return "break"

        def select_all(event=None):
            text_widget.tag_add(tk.SEL, "1.0", "end-1c")
            text_widget.mark_set(tk.INSERT, "1.0")
            text_widget.see(tk.INSERT)
            return "break"

        def copy_all(event=None):
            all_text = text_widget.get("1.0", tk.END).rstrip("\n")
            if all_text:
                self.root.clipboard_clear()
                self.root.clipboard_append(all_text)
            return "break"

        # Prevent accidental keystrokes from modifying text, but allow navigation and shortcuts
        def on_key_press(event):
            # Check if Control or Command modifier is pressed
            if event.state & 0x4 or (sys.platform == "darwin" and event.state & 0x8):
                if event.keysym.lower() in ('c', 'a', 'x'):
                    return None
            # Allow navigation keys
            if event.keysym in ('Up', 'Down', 'Left', 'Right', 'Prior', 'Next', 'Home', 'End'):
                return None
            return "break"

        text_widget.bind("<Control-c>", copy_selection)
        text_widget.bind("<Control-C>", copy_selection)
        text_widget.bind("<Control-a>", select_all)
        text_widget.bind("<Control-A>", select_all)
        text_widget.bind("<Key>", on_key_press)

        # Context Menu (Right Click)
        menu = tk.Menu(
            text_widget, 
            tearoff=0, 
            bg=BG_PANEL, 
            fg=FG_TEXT, 
            activebackground=ACCENT_BLUE, 
            activeforeground="white", 
            bd=1, 
            relief=tk.SOLID,
            font=FONT_MAIN
        )
        menu.add_command(label="📋 Copy (Ctrl+C)", command=lambda: copy_selection())
        menu.add_command(label="🔲 Select All (Ctrl+A)", command=lambda: select_all())
        menu.add_command(label="📄 Copy All Output", command=lambda: copy_all())
        menu.add_separator()
        menu.add_command(label="🧹 Clear Output", command=lambda: self._clear_console(text_widget))

        def show_context_menu(event):
            menu.tk_popup(event.x_root, event.y_root)

        text_widget.bind("<Button-3>", show_context_menu)

    def _copy_terminal_content(self, text_widget):
        """Copies highlighted text, or all text if nothing is selected."""
        try:
            selected_text = text_widget.get(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            selected_text = None

        if not selected_text:
            selected_text = text_widget.get("1.0", tk.END).rstrip("\n")

        if selected_text:
            self.root.clipboard_clear()
            self.root.clipboard_append(selected_text)

    def _clear_console(self, text_widget):
        text_widget.delete("1.0", tk.END)

    def _process_log_queue(self):
        """Drains log messages from worker threads to UI text boxes."""
        while not self.log_queue.empty():
            try:
                tag, text_chunk, log_type = self.log_queue.get_nowait()
                target_text = self.backend_text if tag == "backend" else self.frontend_text
                auto_scroll = self.auto_scroll_backend if tag == "backend" else self.auto_scroll_frontend

                target_text.insert(tk.END, text_chunk, log_type)

                # Keep console memory manageable (max ~3500 lines)
                line_count = int(target_text.index('end-1c').split('.')[0])
                if line_count > 3500:
                    target_text.delete("1.0", f"{line_count - 3000}.0")

                if auto_scroll.get():
                    target_text.see(tk.END)

            except queue.Empty:
                break

        self.root.after(50, self._process_log_queue)

    def _health_check_loop(self):
        """Periodically update the indicator dots based on process state."""
        # Backend
        if self.backend_runner.is_running:
            self.backend_status_lbl.config(fg=ACCENT_GREEN)
        else:
            self.backend_status_lbl.config(fg=ACCENT_RED)

        # Frontend
        if self.frontend_runner.is_running:
            self.frontend_status_lbl.config(fg=ACCENT_GREEN)
        else:
            self.frontend_status_lbl.config(fg=ACCENT_RED)

        self.root.after(1000, self._health_check_loop)

    def start_backend(self):
        self.backend_runner.start()

    def stop_backend(self):
        self.backend_runner.stop()

    def restart_backend(self):
        self.stop_backend()
        self.root.after(800, self.start_backend)

    def start_frontend(self):
        self.frontend_runner.start()

    def stop_frontend(self):
        self.frontend_runner.stop()

    def restart_frontend(self):
        self.stop_frontend()
        self.root.after(800, self.start_frontend)

    def start_all(self):
        self.start_backend()
        self.start_frontend()

    def stop_all(self):
        self.stop_backend()
        self.stop_frontend()

    def restart_all(self):
        self.stop_all()
        self.root.after(1000, self.start_all)

    def on_closing(self):
        """Cleanup all child processes cleanly upon window close."""
        self.backend_runner.stop()
        self.frontend_runner.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = OmniSubLauncherGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
