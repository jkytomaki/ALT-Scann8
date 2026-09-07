"""Side-by-side evidence with matching crops and a blink/overlay view."""
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk


class StabilizationComparison(ttk.Frame):
    def __init__(self, master, first, second, result, evidence):
        super().__init__(master)
        self.images = (first, second)
        self.timer = None
        self.phase = 0
        self.crop = tk.StringVar(self, 'Whole frame')
        self.mode = tk.StringVar(self, 'Side by side')
        status = dict(movement='Movement detected between post-delay exposures',
                      stable='No significant movement detected',
                      detection_disagreement='Detection disagreement: picture detail stayed fixed',
                      inconclusive='Image comparison inconclusive')[result['status']]
        def number(value):
            return 'unknown' if value is None else f'{value:+.2f}'
        interval = result['interval_ms']
        separation = 'unknown' if interval is None else f'{interval:.1f} ms'
        text = (f"{status}\nPicture shift: x {number(result.get('dx_px'))}, "
                f"y {number(result.get('dy_px'))} px; sprocket change: {number(result['hole_delta_px'])} px\n"
                f"Movement threshold: {result['threshold_px']:.2f} px; "
                f"exposure separation: {separation}")
        text += f"; stabilization delay: {result['first']['settle_ms']} ms"
        if result.get('reason'):
            text += '\n' + result['reason']
        ttk.Label(self, text=text, justify='left').pack(padx=8, pady=4)
        controls = ttk.Frame(self)
        controls.pack()
        for variable, choices in ((self.crop, ('Whole frame', 'Sprocket', 'Picture detail')),
                                  (self.mode, ('Side by side', 'Blink', 'Overlay'))):
            menu = ttk.Combobox(controls, textvariable=variable, values=choices, state='readonly', width=16)
            menu.pack(side='left', padx=4)
            menu.bind('<<ComboboxSelected>>', self.redraw)
        pictures = ttk.Frame(self)
        pictures.pack(padx=8, pady=4)
        self.left = ttk.Label(pictures)
        self.right = ttk.Label(pictures)
        self.left.grid(row=1, column=0)
        self.right.grid(row=1, column=1)
        ttk.Label(pictures, text='First exposure').grid(row=0, column=0)
        self.right_title = ttk.Label(pictures, text='Second exposure')
        self.right_title.grid(row=0, column=1)
        ttk.Label(self, text=f'Evidence: {evidence}', wraplength=900).pack(padx=8, pady=4)
        self.bind('<Destroy>', self.destroyed)
        self.redraw()

    def destroyed(self, event):
        if event.widget is self and self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None

    def redraw(self, _event=None):
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
        w, h = self.images[0].size
        boxes = {'Whole frame': (0, 0, w, h), 'Sprocket': (0, h * .15, w * .12, h * .85),
                 'Picture detail': (w * .25, h * .3, w * .65, h * .7)}
        self.previews = [im.crop(tuple(round(v) for v in boxes[self.crop.get()])) for im in self.images]
        for im in self.previews:
            im.thumbnail((440, 300), Image.Resampling.LANCZOS)
        self.phase = 0
        self.paint()

    def paint(self):
        first, second = self.previews
        mode = self.mode.get()
        if mode == 'Overlay':
            right = Image.blend(first, second, .5)
            title = '50% overlay (unregistered)'
        elif mode == 'Blink':
            right = self.previews[self.phase]
            title = 'Blink: ' + ('first' if self.phase == 0 else 'second')
            self.phase = 1 - self.phase
        else:
            right, title = second, 'Second exposure'
        self.photos = (ImageTk.PhotoImage(first), ImageTk.PhotoImage(right))
        self.left.configure(image=self.photos[0])
        self.right.configure(image=self.photos[1])
        self.right_title.configure(text=title)
        if mode == 'Blink':
            self.timer = self.after(500, self.paint)
