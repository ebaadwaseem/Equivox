import queue
import threading
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont


class LiveSubtitle:
    """Create and run on the main thread."""

    def __init__(self, max_chars=20000):
        self._queue = queue.Queue()
        self._closed = threading.Event()
        self._max_chars = max(1, int(max_chars))

        self.root = tk.Tk()
        self.root.title("Live Subtitles")
        self.root.geometry("800x150")
        self.root.minsize(320, 120)
        self.root.attributes("-topmost", True)

        toolbar = ttk.Frame(self.root, padding=6)
        toolbar.pack(fill="x")

        self._on_top = tk.BooleanVar(
            master=self.root,
            value=True,
        )

        ttk.Checkbutton(
            toolbar,
            text="Always on Top",
            variable=self._on_top,
            command=self._toggle_topmost,
        ).pack(side="left")

        ttk.Button(
            toolbar,
            text="Clear",
            command=self.clear,
        ).pack(side="right")

        self._font = tkfont.nametofont(
            "TkDefaultFont", root=self.root
        ).copy()
        self._font.configure(size=24)

        self.text = tk.Text(
            self.root,
            height=1,
            wrap="none",
            font=self._font,
            bg="#171717",
            fg="white",
            padx=12,
            pady=12,
            relief="flat",
            highlightthickness=0,
            state="disabled",
        )
        self.text.pack(
            fill="both",
            expand=True,
            padx=6,
            pady=(0, 6),
        )

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._timer = self.root.after(30, self._drain)

    def _toggle_topmost(self):
        self.root.attributes("-topmost", self._on_top.get())

    def append_text(self, text):
        """Append new text. Safe to call from a worker thread.

        Pass only newly recognized text, not the full transcript.
        Include spaces between English words as needed.
        """
        if text is None or self._closed.is_set():
            return

        # Keep the subtitles on a single line.
        text = str(text).replace("\r", " ").replace("\n", " ")

        if text:
            self._queue.put(text)

    def add_sentence(self, sentence):
        """Append a completed sentence followed by a space."""
        if sentence is None:
            return

        sentence = " ".join(str(sentence).split())
        if sentence:
            self.append_text(sentence + " ")

    def _drain(self):
        """Apply queued updates on the Tk main thread."""
        parts = []

        for _ in range(200):
            try:
                parts.append(self._queue.get_nowait())
            except queue.Empty:
                break

        if parts:
            self.text.configure(state="normal")
            self.text.insert("end", "".join(parts))

            # Limit retained text to avoid unlimited memory growth.
            length = int(
                self.text.count("1.0", "end-1c", "chars")[0]
            )
            excess = length - self._max_chars

            if excess > 0:
                self.text.delete("1.0", f"1.0+{excess}c")

            self.text.configure(state="disabled")

            # Keep the newest text visible at the right edge.
            self.text.xview_moveto(1.0)

        self._timer = self.root.after(30, self._drain)

    def clear(self):
        """Clear displayed and queued text. Call on the main thread."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self.text.xview_moveto(0.0)

    def close(self):
        if self._closed.is_set():
            return

        self._closed.set()
        self.root.after_cancel(self._timer)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    popup = LiveSubtitle()

    def demo():
        transcript = (
            "Hello, these are live subtitles. "
            "New words appear from left to right. "
            "When the line becomes wider than the window, "
            "the older text scrolls to the left automatically. "
            "The latest words always remain visible on the right."
        )

        # Simulate incremental recognition results.
        for word in transcript.split():
            if popup._closed.wait(0.25):
                return

            popup.append_text(word + " ")

    threading.Thread(target=demo, daemon=True).start()
    popup.run()


if __name__ == "__main__":
    main()