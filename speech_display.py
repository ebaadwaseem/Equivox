
"""Single-line live subtitles. Run transcribe.py to start recognition."""
import queue
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk


class LiveSubtitle:
    """Create on the main thread; recognition updates are thread-safe."""

    def __init__(self, max_chars=20000):
        self._queue = queue.Queue()
        self.closed = threading.Event()
        self._max_chars = max(1, int(max_chars))
        self._history = ''
        self._partial = ''
        self.root = tk.Tk()
        self.root.title('Live Subtitles')
        self.root.geometry('800x150')
        self.root.minsize(320, 120)
        self.root.attributes('-topmost', True)
        toolbar = ttk.Frame(self.root, padding=6)
        toolbar.pack(fill='x')
        self._on_top = tk.BooleanVar(master=self.root, value=True)
        ttk.Checkbutton(toolbar, text='Always on Top', variable=self._on_top,
                        command=lambda: self.root.attributes('-topmost', self._on_top.get())).pack(side='left')
        ttk.Button(toolbar, text='Clear', command=self.clear).pack(side='right')
        self._font = tkfont.nametofont('TkDefaultFont', root=self.root).copy()
        self._font.configure(size=24)
        self.text = tk.Text(self.root, height=1, wrap='none', font=self._font,
                            bg='#171717', fg='white', padx=12, pady=12,
                            relief='flat', highlightthickness=0, state='disabled')
        self.text.pack(fill='both', expand=True, padx=6, pady=(0, 6))
        self._status = tk.StringVar(master=self.root, value='Starting recognition...')
        ttk.Label(self.root, textvariable=self._status).pack(fill='x', padx=6)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self._timer = self.root.after(30, self._drain)
        self.text.bind('<Configure>', lambda event: self.text.xview_moveto(1.0))

    def _send(self, kind, text):
        if not self.closed.is_set():
            self._queue.put((kind, ' '.join(str(text).split())))

    def partial(self, text):
        """Replace the current hypothesis, including with an empty result."""
        self._send('partial', text)

    def final(self, text):
        """Commit one result and remove its temporary hypothesis."""
        self._send('final', text)

    def status(self, text):
        self._send('status', text)

    def _render(self):
        self._history = self._history[-self._max_chars:]
        value = (self._history + self._partial)[-self._max_chars:]
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', value)
        self.text.configure(state='disabled')
        self.text.xview_moveto(1.0)

    def _drain(self):
        changed = False
        for _ in range(200):
            try:
                kind, text = self._queue.get_nowait()
            except queue.Empty:
                break
            if kind == 'status':
                self._status.set(text)
            elif kind == 'partial':
                self._partial = text
                changed = True
            else:
                if text:
                    self._history += text + ' '
                self._partial = ''
                changed = True
        if changed:
            self._render()
        self._timer = self.root.after(30, self._drain)

    def clear(self):
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._history = self._partial = ''
        self._render()

    def close(self):
        if not self.closed.is_set():
            self.closed.set()
            self.root.after_cancel(self._timer)
            self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    LiveSubtitle().run()
