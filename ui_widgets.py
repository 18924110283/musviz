"""圆角按钮（Canvas 绘制）"""

import tkinter as tk


class RoundedButton(tk.Canvas):
    def __init__(self, parent, text="", command=None, width=None, height=32,
                 bg="#26242c", fg="#e9e6ef", fill="#2f2c36", hover="#7d5bb8",
                 font=("Microsoft YaHei", 9), radius=9, padx=16):
        self._bg = bg
        super().__init__(parent, height=height, bg=bg, highlightthickness=0,
                         bd=0, cursor="hand2")
        self._r = radius
        self._fill = fill
        self._hover = hover
        self._fg = fg
        self._text = text
        self._cmd = command
        self._font = font
        self._padx = padx
        self._h = height
        self._state = "normal"

        self._t = self.create_text(0, height // 2, text=text, fill=fg, font=font, anchor="w")
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Enter>", lambda e: self._set_fill(self._hover))
        self.bind("<Leave>", lambda e: self._set_fill(self._fill))
        self.bind("<Button-1>", self._click)
        w = width or (self._measure() + padx * 2)
        self.configure(width=w)

    def _measure(self):
        return max(10, len(self._text) * 14 + 6)

    def _set_fill(self, color):
        if self._state == "disabled":
            return
        self._fill_cur = color
        self._redraw()

    def _redraw(self):
        w = self.winfo_width()
        h = self._h
        if w <= 1:
            return
        self.delete("bg")
        r = self._r
        fill = getattr(self, "_fill_cur", self._fill)
        if self._state == "disabled":
            fill = "#33313a"
        # 圆角矩形：四个圆 + 两个矩形
        self.create_rectangle(r, 0, w - r, h, fill=fill, outline="", tags="bg")
        self.create_rectangle(0, r, w, h - r, fill=fill, outline="", tags="bg")
        for x, y in ((0, 0), (w - 2 * r, 0), (0, h - 2 * r), (w - 2 * r, h - 2 * r)):
            self.create_oval(x, y, x + 2 * r, y + 2 * r, fill=fill, outline="", tags="bg")
        self.tag_lower("bg")
        self.coords(self._t, w / 2, h / 2)
        self.itemconfigure(self._t, anchor="center",
                           fill="#7a7484" if self._state == "disabled" else self._fg)

    def _click(self, _e):
        if self._state == "disabled":
            return
        if self._cmd:
            self._cmd()

    def set_text(self, text):
        self._text = text
        self.itemconfigure(self._t, text=text)
        self._redraw()

    def set_state(self, state):
        self._state = state
        if state == "disabled":
            self._set_fill(self._fill)
        self._redraw()
