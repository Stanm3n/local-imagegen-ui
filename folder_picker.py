"""Native folder dialog, isolated from the web server's threads."""

import json
import sys
import tkinter as tk
from tkinter import filedialog

if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(
            parent=root,
            title="Local ImageGen UI — Ausgabeordner wählen",
            initialdir=sys.argv[1],
            mustexist=True,
        )
        print(json.dumps({"path": selected}, ensure_ascii=True))
    finally:
        root.destroy()
