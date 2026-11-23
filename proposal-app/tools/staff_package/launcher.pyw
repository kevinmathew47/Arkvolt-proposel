"""ARKVOLT Proposals launcher (desktop icon). Runs with pythonw, so no console window appears.
Shows a branded splash, starts the app in the background if it is not running yet, then opens the browser."""
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "8520"))
URL = f"http://localhost:{PORT}"
LOG = HERE / "data" / "server.log"
NAVY, GREEN, MUTED = "#0A1527", "#80C200", "#9FB0C8"


def running():
    try:
        with urllib.request.urlopen(URL + "/login", timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def start_server():
    (HERE / "data").mkdir(exist_ok=True)
    log = open(LOG, "a", encoding="utf-8")
    flags = 0x08000000 | 0x00000008  # CREATE_NO_WINDOW | DETACHED_PROCESS: keeps running after the splash closes
    py = HERE / ".venv" / "Scripts" / "pythonw.exe"
    subprocess.Popen([str(py if py.exists() else sys.executable), str(HERE / "run.py")], cwd=HERE, stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)


class Splash:
    def __init__(self):
        self.root = r = tk.Tk()
        r.overrideredirect(True)
        r.configure(bg=NAVY)
        w, h = 460, 250
        r.geometry(f"{w}x{h}+{(r.winfo_screenwidth() - w) // 2}+{(r.winfo_screenheight() - h) // 2}")
        r.attributes("-topmost", True)
        try:
            r.iconbitmap(str(HERE / "arkvolt.ico"))
        except tk.TclError:
            pass
        tk.Frame(r, bg=GREEN, height=4).pack(fill="x")
        try:
            self.logo = tk.PhotoImage(file=str(HERE / "logo-white.png"))
            tk.Label(r, image=self.logo, bg=NAVY).pack(pady=(34, 16))
        except tk.TclError:
            tk.Label(r, text="ARKVOLT", fg="white", bg=NAVY, font=("Segoe UI", 26, "bold")).pack(pady=(34, 16))
        self.msg = tk.Label(r, text="Starting ARKVOLT Proposals…", fg="white", bg=NAVY, font=("Segoe UI", 12, "bold"))
        self.msg.pack()
        self.sub = tk.Label(r, text="This takes a few seconds", fg=MUTED, bg=NAVY, font=("Segoe UI", 9))
        self.sub.pack(pady=(4, 14))
        self.bar = tk.Canvas(r, width=300, height=6, bg="#1d2c47", highlightthickness=0)
        self.bar.pack()
        self.pos = 0
        self.btn = None
        self.animate()

    def animate(self):
        if self.btn:
            return
        self.bar.delete("all")
        x = self.pos % 360 - 60
        self.bar.create_rectangle(x, 0, x + 60, 6, fill=GREEN, width=0)
        self.pos += 8
        self.root.after(30, self.animate)

    def fail(self, text):
        self.bar.pack_forget()
        self.msg.config(text="ARKVOLT Proposals could not start", fg="#FF9C9C")
        self.sub.config(text=text, wraplength=400, justify="center")
        self.btn = tk.Button(self.root, text="Close", command=self.root.destroy, bg=GREEN, fg=NAVY, relief="flat",
                             font=("Segoe UI", 10, "bold"), padx=18, pady=4)
        self.btn.pack(pady=6)


def open_app():
    if not os.environ.get("ARKVOLT_NO_BROWSER"):  # test switch
        webbrowser.open(URL)


def main():
    if running():  # already started earlier: just open it
        open_app()
        return
    s = Splash()

    def work():
        start_server()
        for _ in range(90):
            if running():
                open_app()
                s.root.after(0, s.root.destroy)
                return
            time.sleep(1)
        tail = ""
        try:
            tail = LOG.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1][:160]
        except OSError:
            pass
        s.root.after(0, lambda: s.fail(f"Please run Setup again. Details: {tail or 'no response from the app'}\n(log: {LOG})"))

    threading.Thread(target=work, daemon=True).start()
    s.root.mainloop()


if __name__ == "__main__":
    main()
