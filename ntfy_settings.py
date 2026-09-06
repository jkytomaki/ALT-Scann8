"""Settings dialog for the scanner's independent ntfy sender."""
import tkinter as tk
from tkinter import ttk


class NtfySettingsWindow(tk.Toplevel):
    def __init__(self, parent, notifier):
        super().__init__(parent)
        self.notifier = notifier
        self.title('Scanner notifications')
        self.transient(parent)
        self.resizable(False, False)
        settings = notifier.get_settings()
        self.enabled = tk.BooleanVar(self, settings['enabled'])
        self.fields = {key: tk.StringVar(self, settings[key]) for key in ('server', 'topic', 'token', 'command_topic')}
        self.images = tk.BooleanVar(self, settings['images'])
        self.remote_commands = tk.BooleanVar(self, settings['remote_commands'])
        self.status = tk.StringVar(self)
        self.notice = tk.StringVar(self)
        content = ttk.Frame(self, padding=16)
        content.pack(fill='both', expand=True)
        ttk.Checkbutton(content, text='Notify on guard pauses, failed recovery and save errors',
                        variable=self.enabled).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 12))
        for row, (key, label) in enumerate((('server', 'Server'), ('topic', 'Topic'),
                                           ('token', 'Access token (optional)'),
                                           ('command_topic', 'Command topic (optional)')), 1):
            ttk.Label(content, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=4)
            ttk.Entry(content, textvariable=self.fields[key], width=44,
                      show='*' if key == 'token' else '').grid(row=row, column=1, sticky='ew', pady=4)
        ttk.Checkbutton(content, text='Attach held-frame preview (uploads image to ntfy)',
                        variable=self.images).grid(row=5, column=0, columnspan=2, sticky='w')
        ttk.Checkbutton(content, text='Allow phone buttons to retry, save as-is or stop a paused scan',
                        variable=self.remote_commands).grid(row=6, column=0, columnspan=2, sticky='w')
        ttk.Label(content, text='Subscribe to this server and topic in the ntfy phone app.\n'
                  'Keep your topic private: its subscribers can use the buttons.\n'
                  'Blank command topic uses the alert topic plus -commands.',
                  wraplength=560).grid(row=7, column=0, columnspan=2, sticky='w', pady=(10, 6))
        ttk.Label(content, textvariable=self.notice, wraplength=560).grid(
            row=8, column=0, columnspan=2, sticky='w')
        ttk.Label(content, textvariable=self.status, wraplength=560).grid(
            row=9, column=0, columnspan=2, sticky='w', pady=(4, 10))
        buttons = ttk.Frame(content)
        buttons.grid(row=10, column=0, columnspan=2, sticky='ew')
        ttk.Button(buttons, text='Send test notification', command=self.test).pack(side='left')
        ttk.Button(buttons, text='Save', command=self.save).pack(side='right', padx=(8, 0))
        ttk.Button(buttons, text='Cancel', command=self.close).pack(side='right', padx=(12, 0))
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _event: self.close())
        self.refresh()
        self.wait_visibility()
        self.grab_set()

    def settings(self):
        return dict(enabled=self.enabled.get(), images=self.images.get(),
                    remote_commands=self.remote_commands.get(), **{key: var.get() for key, var in self.fields.items()})

    def test(self):
        try:
            accepted = self.notifier.notify('test', None, 'Scanner notifications are connected.',
                                            test_settings=self.settings())
            self.notice.set('Test queued using the fields above. Save to apply settings.' if accepted
                            else 'Could not queue test notification')
        except ValueError as error:
            self.notice.set(str(error))

    def save(self):
        try:
            self.notifier.configure(self.settings())
        except (ValueError, OSError) as error:
            self.notice.set(str(error))
            return
        self.close()

    def refresh(self):
        self.status.set(self.notifier.status + '\n' + self.notifier.commands.status)
        self.refresh_id = self.after(500, self.refresh)

    def close(self):
        self.after_cancel(self.refresh_id)
        self.grab_release()
        if self.master.winfo_exists():
            self.master.grab_set()
        self.destroy()
