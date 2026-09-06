"""Live alignment statistics window; reads snapshots without touching the scanner."""
import tkinter as tk
from tkinter import font


class AlignmentStatisticsWindow(tk.Toplevel):
    GROUPS = (
        ('Alignment on arrival', (
            ('Aligned within tolerance', 'aligned_pct', 'percent', True),
            ('Median offset', 'median_offset_pct', 'signed', False),
            ('95th percentile absolute offset', 'p95_absolute_pct', 'percent', False),
            ('Phototransistor stops', 'pt_arrivals', 'count', False),
            ('Undershoots', 'undershoots', 'count', False),
            ('Overshoots', 'overshoots', 'count', False),
            ('Unknown detections', 'unknown', 'count', False),
            ('Not measured', 'unmeasured', 'count', False),
            ('Confirmed with two exposures', 'confirmed', 'count', False),
        )),
        ('Corrections & recovery', (
            ('Frames corrected', 'corrections', 'count', True),
            ('Nudges / motor steps', ('nudges', 'nudge_steps'), 'pair', False),
            ('Frames paused', 'pauses', 'count', False),
            ('Recovery attempts', 'recovery_attempts', 'count', False),
            ('Recovered frames', 'recovered', 'count', False),
            ('Recovery motor steps', 'recovery_steps', 'count', False),
            ('Failed recoveries', 'recovery_failures', 'count', False),
            ('Saved as-is', 'accepted_as_is', 'count', False),
        )),
        ('Throughput', (
            ('Effective frames / second', 'effective_fps', 'decimal', True),
            ('Frames captured', 'captured', 'count', False),
        )),
    )

    def __init__(self, parent):
        super().__init__(parent)
        self.title('Alignment statistics')
        self.configure(background='#f3f5f7')
        self.body_font = font.nametofont('TkDefaultFont').copy()
        self.body_font.configure(size=11)
        self.bold_font = self.body_font.copy()
        self.bold_font.configure(weight='bold')
        self.title_font = self.bold_font.copy()
        self.title_font.configure(size=16)
        self.small_font = self.body_font.copy()
        self.small_font.configure(size=10)
        self.values = {}
        self.context = tk.StringVar(self)
        self.tuning = tk.StringVar(self)
        self.settings = tk.StringVar(self)
        self.recent_heading = tk.StringVar(self, 'Last 100')
        self.session_heading = tk.StringVar(self, 'Session')

        self.label(self, text='Alignment statistics', font=self.title_font,
                   background='#f3f5f7').pack(anchor='w', padx=18, pady=(14, 2))
        self.label(self, textvariable=self.context, font=self.small_font,
                   background='#f3f5f7').pack(anchor='w', padx=18, pady=(0, 12))
        table = tk.Frame(self, background='white', highlightthickness=1,
                         highlightbackground='#d5dce3')
        table.pack(fill='both', expand=True, padx=18)
        table.columnconfigure(0, weight=1)
        for column in (1, 2):
            table.columnconfigure(column, minsize=115, uniform='values')
        for column, options in enumerate((dict(text='Measure'),
                dict(textvariable=self.recent_heading), dict(textvariable=self.session_heading))):
            self.label(table, **options, font=self.bold_font, background='#e8edf2',
                       anchor='w' if column == 0 else 'e', padx=12, pady=8).grid(
                           row=0, column=column, sticky='nsew')
        row = 1
        for title, measures in self.GROUPS:
            self.label(table, text=title, font=self.bold_font, background='#f0f3f6',
                       padx=12, pady=6).grid(row=row, column=0, columnspan=3, sticky='ew')
            row += 1
            for label, key, style, prominent in measures:
                row_font = self.bold_font if prominent else self.body_font
                self.label(table, text=label, font=row_font, padx=12, pady=3).grid(
                    row=row, column=0, sticky='ew')
                for column, scope in enumerate(('recent', 'session'), 1):
                    value = tk.StringVar(self, '—')
                    self.values[(scope, key)] = value
                    self.label(table, textvariable=value, font=row_font, anchor='e',
                               padx=12, pady=3).grid(row=row, column=column, sticky='ew')
                row += 1

        tuning = tk.Frame(self, background='#e8edf2')
        tuning.pack(fill='x', padx=18, pady=(12, 0))
        self.label(tuning, text='Auto Fine Tune', font=self.bold_font,
                   background='#e8edf2').pack(anchor='w', padx=12, pady=(8, 2))
        self.label(tuning, textvariable=self.tuning, background='#e8edf2',
                   wraplength=620).pack(anchor='w', padx=12)
        self.label(tuning, textvariable=self.settings, font=self.small_font,
                   background='#e8edf2', wraplength=620).pack(anchor='w', padx=12, pady=(4, 8))
        footer = tk.Frame(self, background='#f3f5f7')
        footer.pack(fill='x', padx=18, pady=12)
        self.label(footer, text=(
            'Offsets are % of image height, before correction; + means undershoot.\n'
            'Unknown and unmeasured stops count against the aligned percentage.\n'
            'Speed includes pauses and recovery; captured frames may still be saving.'),
            font=self.small_font, background='#f3f5f7', wraplength=570).pack(side='left')
        self.close_button = tk.Button(footer, text='Close', command=self.close, padx=12)
        self.close_button.pack(side='right', padx=(12, 0))
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', self.close)
        self.update_idletasks()
        self.minsize(self.winfo_reqwidth(), self.winfo_reqheight())

    def close(self, _event=None):
        self.destroy()

    def label(self, parent, **options):
        options.setdefault('font', self.body_font)
        options.setdefault('background', 'white')
        options.setdefault('foreground', '#202b36')
        options.setdefault('anchor', 'w')
        options.setdefault('justify', 'left')
        return tk.Label(parent, **options)

    def update_snapshot(self, snapshot, tuning, settings):
        for _title, measures in self.GROUPS:
            for _label, key, style, _prominent in measures:
                for scope in ('recent', 'session'):
                    data = snapshot[scope]
                    if style == 'pair':
                        text = ' / '.join(f'{data[item]:,}' for item in key)
                    else:
                        value = data[key]
                        text = ('—' if value is None else
                                f'{value:,}' if style == 'count' else
                                f'{value:+.2f}%' if style == 'signed' else
                                f'{value:.2f}%' if style == 'percent' else f'{value:.2f}')
                    self.values[(scope, key)].set(text)
        self.recent_heading.set(f"Last 100\n{snapshot['recent']['positions']:,} positions")
        self.session_heading.set(f"Session\n{snapshot['session']['positions']:,} positions")
        if snapshot['run_id'] is None:
            context = 'No scan started · Statistics begin when scanning starts'
        else:
            elapsed = int(snapshot['session']['elapsed_s'])
            duration = f'{elapsed // 3600:02d}:{elapsed // 60 % 60:02d}:{elapsed % 60:02d}'
            frames = ('Waiting for first frame' if snapshot['first_frame'] is None else
                      f"Frames {snapshot['first_frame']:,}–{snapshot['last_frame']:,}")
            context = f"{'Scan in progress' if snapshot['active'] else 'Scan stopped'} · {frames} · {duration}"
        self.context.set(context)
        self.tuning.set(tuning)
        self.settings.set(settings)
