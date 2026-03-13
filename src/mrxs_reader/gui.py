"""
MRXS Reader GUI — tkinter-based graphical slide browser.

Launch with:
    python -m mrxs_reader gui
"""

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.colorchooser import askcolor
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from PIL import Image, ImageTk

from . import MrxsSlide

# ── Default false-color palette ───────────────────────────────────────────────

_CHANNEL_PALETTE: Dict[str, str] = {
    "DAPI":      "#4466ff",
    "SpGreen":   "#00cc44",
    "SpOrange":  "#ff8800",
    "SpAqua":    "#00cccc",
    "CY5":       "#cc44ff",
    "Cy5":       "#cc44ff",
    "FITC":      "#00ee00",
    "TRITC":     "#ff4400",
}
_FALLBACK_COLORS = ["#ff4444", "#44ff44", "#4444ff",
                    "#ffff44", "#ff44ff", "#44ffff"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hex_to_rgb(hex_color: str):
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def _arr_to_photo(arr: np.ndarray, max_dim: int = 1200) -> ImageTk.PhotoImage:
    """Scale a 2-D uint8 array down to fit *max_dim* and wrap in PhotoImage."""
    img = Image.fromarray(arr, mode="L").convert("RGB")
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                         Image.LANCZOS)
    return ImageTk.PhotoImage(img)


def _build_composite_arr(arrays: Dict[str, np.ndarray],
                          colors: Dict[str, str]) -> np.ndarray:
    """Blend mono float arrays with per-channel false colors → uint8 RGB."""
    if not arrays:
        return np.zeros((1, 1, 3), dtype=np.uint8)

    shapes = [a.shape for a in arrays.values()]
    h, w = shapes[0]
    out = np.zeros((h, w, 3), dtype=np.float32)

    for name, arr in arrays.items():
        norm = arr.astype(np.float32) / 255.0
        nz = norm[norm > 0]
        if nz.size > 1:
            p2, p98 = np.percentile(nz, (2, 98))
            if p98 > p2:
                norm = np.clip((norm - p2) / (p98 - p2), 0.0, 1.0)
        rgb = _hex_to_rgb(colors.get(name, "#ffffff"))
        for c, col in enumerate(rgb):
            out[:, :, c] += norm * (col / 255.0)

    return np.clip(out * 255, 0, 255).astype(np.uint8)


# ── Scrollable image canvas ───────────────────────────────────────────────────

class _ImageCanvas(ttk.Frame):
    """Canvas with scrollbars that displays a single PIL PhotoImage."""

    def __init__(self, parent, bg="#111122", **kw):
        super().__init__(parent, **kw)
        self.canvas = tk.Canvas(self, bg=bg, cursor="fleur", highlightthickness=0)
        vsb = ttk.Scrollbar(self, orient="vertical",   command=self.canvas.yview)
        hsb = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self._photo: Optional[ImageTk.PhotoImage] = None  # retain reference

        # Mouse-drag pan
        self.canvas.bind("<ButtonPress-1>",   self._drag_start)
        self.canvas.bind("<B1-Motion>",        self._drag_move)
        self.canvas.bind("<MouseWheel>",       self._on_mousewheel)

        self._drag_x = self._drag_y = 0

    def show(self, photo: ImageTk.PhotoImage) -> None:
        self._photo = photo
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=photo)
        self.canvas.configure(scrollregion=(0, 0, photo.width(), photo.height()))

    def _drag_start(self, event):
        self._drag_x, self._drag_y = event.x, event.y

    def _drag_move(self, event):
        dx = self._drag_x - event.x
        dy = self._drag_y - event.y
        self._drag_x, self._drag_y = event.x, event.y
        self.canvas.xview_scroll(int(dx), "units")
        self.canvas.yview_scroll(int(dy), "units")

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


# ── Main application window ───────────────────────────────────────────────────

class MrxsViewerApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("MRXS Viewer")
        self.geometry("1200x820")
        self.minsize(900, 640)

        self._slide: Optional[MrxsSlide] = None
        self._cache: Dict[str, np.ndarray] = {}   # "ChannelName@level" → array
        self._worker: Optional[threading.Thread] = None

        # Per-channel state (populated when slide loads)
        self._ch_vars:   Dict[str, tk.BooleanVar] = {}
        self._ch_colors: Dict[str, tk.StringVar]  = {}
        self._ch_swatches: Dict[str, List[tk.Label]] = {}  # name → [swatch, ...]

        self._active_ch: Optional[str] = None

        self._apply_theme()
        self._build_menu()
        self._build_ui()

    # ── Theme ─────────────────────────────────────────────────────────────────

    def _apply_theme(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        BG, FG, ACC = "#0e0e1c", "#d8dcf0", "#6688dd"
        PANEL, BORDER = "#16162a", "#2a2a4a"
        s.configure(".",            background=BG,    foreground=FG,
                    font=("Segoe UI", 9))
        s.configure("TFrame",      background=BG)
        s.configure("TLabel",      background=BG,    foreground=FG)
        s.configure("TLabelframe", background=BG,    foreground=ACC,
                    bordercolor=BORDER)
        s.configure("TLabelframe.Label", background=BG, foreground=ACC)
        s.configure("TNotebook",   background=BG)
        s.configure("TNotebook.Tab", background=PANEL, foreground="#8899cc",
                    padding=(12, 4))
        s.map("TNotebook.Tab",
              background=[("selected", "#202040")],
              foreground=[("selected", "#c8d8ff")])
        s.configure("TButton",     background="#1c2060", foreground="#b8ccff",
                    relief="flat", padding=(8, 4))
        s.map("TButton",
              background=[("active", "#2c3090"), ("pressed", "#0c1040")])
        s.configure("TCheckbutton", background=BG,   foreground="#a0b8e0")
        s.configure("TProgressbar", troughcolor=PANEL, background=ACC)
        s.configure("TScale",       background=BG,   troughcolor=PANEL)
        s.configure("TSpinbox",     fieldbackground=PANEL, foreground=FG)
        s.configure("TEntry",       fieldbackground=PANEL, foreground=FG)
        s.configure("Accent.TButton", background="#2a3890", foreground="#d0e4ff",
                    font=("Segoe UI", 9, "bold"), padding=(10, 5))
        s.configure("Info.TLabel",  foreground="#90a0c8",
                    font=("Consolas", 9), background=BG)
        s.configure("Head.TLabel",  foreground=ACC,
                    font=("Segoe UI", 10, "bold"), background=BG)
        s.configure("Status.TLabel", background="#09090f", foreground="#606080",
                    padding=(6, 2))
        self.configure(bg=BG)

    # ── Menu ──────────────────────────────────────────────────────────────────

    def _build_menu(self):
        MENU_KW = dict(bg="#16162a", fg="#b8ccff",
                       activebackground="#2a2a50", activeforeground="#ffffff",
                       relief="flat", tearoff=0)
        bar = tk.Menu(self, bg="#16162a", fg="#b8ccff", relief="flat")

        file_m = tk.Menu(bar, **MENU_KW)
        file_m.add_command(label="Open Slide…",    accelerator="Ctrl+O",
                           command=self._browse_and_load)
        file_m.add_separator()
        file_m.add_command(label="Exit", command=self.destroy)
        bar.add_cascade(label="File", menu=file_m)

        export_m = tk.Menu(bar, **MENU_KW)
        export_m.add_command(label="Export Selected Channels…",
                             command=self._export_channels)
        export_m.add_command(label="Export Composite…",
                             command=self._export_composite)
        bar.add_cascade(label="Export", menu=export_m)

        help_m = tk.Menu(bar, **MENU_KW)
        help_m.add_command(label="About", command=self._show_about)
        bar.add_cascade(label="Help", menu=help_m)

        self.configure(menu=bar)
        self.bind("<Control-o>", lambda _: self._browse_and_load())

    # ── UI layout ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Top bar ──────────────────────────────────────────────────────────────
        top = ttk.Frame(self, padding=(8, 6, 8, 4))
        top.pack(fill="x", side="top")

        ttk.Label(top, text="Slide:").pack(side="left")
        self._path_var = tk.StringVar()
        ttk.Entry(top, textvariable=self._path_var, width=60).pack(
            side="left", padx=(4, 4), fill="x", expand=True)
        ttk.Button(top, text="Browse…", command=self._browse_and_load).pack(
            side="left", padx=(0, 4))
        ttk.Button(top, text="Load", style="Accent.TButton",
                   command=self._load_from_entry).pack(side="left")

        # PanedWindow: left sidebar + right content ────────────────────────────
        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        left  = ttk.Frame(pane, width=240)
        right = ttk.Frame(pane)
        pane.add(left,  weight=0)
        pane.add(right, weight=1)

        self._build_left(left)
        self._build_right(right)

        # Status bar ───────────────────────────────────────────────────────────
        sbar = ttk.Frame(self, style="TFrame")
        sbar.pack(fill="x", side="bottom")
        self._status_var = tk.StringVar(value="Ready — open a slide to begin")
        ttk.Label(sbar, textvariable=self._status_var,
                  style="Status.TLabel").pack(side="left", fill="x", expand=True)
        self._pbar = ttk.Progressbar(sbar, mode="indeterminate", length=140)
        self._pbar.pack(side="right", padx=(4, 4), pady=1)

    def _build_left(self, parent):
        # Slide info ───────────────────────────────────────────────────────────
        info_f = ttk.LabelFrame(parent, text="Slide Info", padding=8)
        info_f.pack(fill="x", padx=4, pady=(4, 4))

        self._info: Dict[str, ttk.Label] = {}
        for key in ("ID", "Dimensions", "Levels", "Pixel size", "Objective"):
            row = ttk.Frame(info_f)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=f"{key}:", width=12, anchor="w",
                      style="Info.TLabel").pack(side="left")
            lbl = ttk.Label(row, text="—", anchor="w", style="Info.TLabel")
            lbl.pack(side="left", fill="x", expand=True)
            self._info[key] = lbl

        # Channel list ─────────────────────────────────────────────────────────
        ch_f = ttk.LabelFrame(parent, text="Channels", padding=8)
        ch_f.pack(fill="both", expand=True, padx=4, pady=4)

        self._ch_list_frame = ttk.Frame(ch_f)
        self._ch_list_frame.pack(fill="both", expand=True)
        ttk.Label(self._ch_list_frame, text="(no slide loaded)",
                  style="Info.TLabel").pack(pady=20)

        # Zoom ─────────────────────────────────────────────────────────────────
        zoom_f = ttk.LabelFrame(parent, text="Zoom Level", padding=8)
        zoom_f.pack(fill="x", padx=4, pady=4)

        zrow = ttk.Frame(zoom_f)
        zrow.pack(fill="x")
        self._zoom_var = tk.IntVar(value=7)
        self._zoom_spin = ttk.Spinbox(zrow, from_=0, to=9,
                                      textvariable=self._zoom_var,
                                      width=4, state="readonly")
        self._zoom_spin.pack(side="left")
        ttk.Label(zrow, text=" (0 = full res)", style="Info.TLabel").pack(
            side="left")

        self._zoom_scale = ttk.Scale(zoom_f, from_=0, to=9, orient="horizontal",
                                     variable=self._zoom_var,
                                     command=self._on_zoom_drag)
        self._zoom_scale.pack(fill="x", pady=(4, 0))

        ttk.Button(parent, text="▶  Reload View", style="Accent.TButton",
                   command=self._reload_view).pack(padx=4, pady=6, fill="x")

    def _build_right(self, parent):
        nb = ttk.Notebook(parent)
        nb.pack(fill="both", expand=True)
        self._nb = nb

        # Tab 1 — Channel View ─────────────────────────────────────────────────
        t1 = ttk.Frame(nb)
        nb.add(t1, text="  Channel View  ")
        self._ch_canvas = _ImageCanvas(t1)
        self._ch_canvas.pack(fill="both", expand=True)
        self._placeholder(self._ch_canvas.canvas)

        # Tab 2 — Composite ────────────────────────────────────────────────────
        t2 = ttk.Frame(nb)
        nb.add(t2, text="  Composite  ")

        ctrl = ttk.Frame(t2, padding=(8, 6))
        ctrl.pack(fill="x")
        ttk.Label(ctrl, text="Check channels in the sidebar, then:",
                  style="Info.TLabel").pack(side="left")
        ttk.Button(ctrl, text="Build Composite", style="Accent.TButton",
                   command=self._build_composite).pack(side="right")

        self._comp_canvas = _ImageCanvas(t2)
        self._comp_canvas.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._placeholder(self._comp_canvas.canvas)

        # Tab 3 — Export ───────────────────────────────────────────────────────
        t3 = ttk.Frame(nb)
        nb.add(t3, text="  Export  ")
        self._build_export_tab(t3)

    def _build_export_tab(self, parent):
        f = ttk.Frame(parent, padding=18)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="Export Options", style="Head.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))

        # Output dir
        ttk.Label(f, text="Output directory:").grid(row=1, column=0, sticky="w")
        self._exp_dir_var = tk.StringVar()
        ttk.Entry(f, textvariable=self._exp_dir_var, width=44).grid(
            row=1, column=1, sticky="ew", padx=(6, 4))
        ttk.Button(f, text="Browse…", command=self._browse_export_dir).grid(
            row=1, column=2)

        # Format
        ttk.Label(f, text="Format:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self._exp_fmt = tk.StringVar(value="tiff")
        fmts = ttk.Frame(f)
        fmts.grid(row=2, column=1, sticky="w", pady=(8, 0))
        ttk.Radiobutton(fmts, text="TIFF", variable=self._exp_fmt,
                        value="tiff").pack(side="left")
        ttk.Radiobutton(fmts, text="PNG",  variable=self._exp_fmt,
                        value="png").pack(side="left", padx=(10, 0))
        ttk.Radiobutton(fmts, text="OME-TIFF", variable=self._exp_fmt,
                        value="ome-tiff").pack(side="left", padx=(10, 0))

        # Zoom
        ttk.Label(f, text="Zoom level:").grid(row=3, column=0, sticky="w",
                                               pady=(8, 0))
        self._exp_zoom = tk.IntVar(value=7)
        ttk.Spinbox(f, from_=0, to=9, textvariable=self._exp_zoom,
                    width=6, state="readonly").grid(
            row=3, column=1, sticky="w", padx=(6, 0), pady=(8, 0))

        # Action buttons
        btn_row = ttk.Frame(f)
        btn_row.grid(row=4, column=0, columnspan=3, sticky="w", pady=(18, 0))
        ttk.Button(btn_row, text="Export Checked Channels",
                   style="Accent.TButton",
                   command=self._export_channels).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Export Composite",
                   command=self._export_composite).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Export OME-TIFF Stack",
                   command=self._export_ometiff_stack).pack(side="left")

        f.columnconfigure(1, weight=1)

        # Log output
        ttk.Label(f, text="Log:", style="Head.TLabel").grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(16, 4))
        log_f = ttk.Frame(f)
        log_f.grid(row=6, column=0, columnspan=3, sticky="nsew")
        f.rowconfigure(6, weight=1)

        self._log = tk.Text(log_f, height=12, bg="#090912", fg="#809090",
                            font=("Consolas", 9), relief="flat",
                            state="disabled", wrap="word")
        log_sb = ttk.Scrollbar(log_f, command=self._log.yview)
        self._log.configure(yscrollcommand=log_sb.set)
        self._log.pack(side="left", fill="both", expand=True)
        log_sb.pack(side="right", fill="y")

    # ── Channel sidebar ───────────────────────────────────────────────────────

    def _rebuild_channels(self, names: List[str]):
        for w in self._ch_list_frame.winfo_children():
            w.destroy()
        self._ch_vars.clear()
        self._ch_colors.clear()
        self._ch_swatches.clear()

        for i, name in enumerate(names):
            var = tk.BooleanVar(value=True)
            default = _CHANNEL_PALETTE.get(name, _FALLBACK_COLORS[i % len(_FALLBACK_COLORS)])
            color_var = tk.StringVar(value=default)
            self._ch_vars[name]   = var
            self._ch_colors[name] = color_var
            self._ch_swatches[name] = []

            row = ttk.Frame(self._ch_list_frame)
            row.pack(fill="x", pady=2)

            ttk.Checkbutton(row, variable=var).pack(side="left")

            swatch = tk.Label(row, bg=default, width=3, relief="solid",
                              cursor="hand2", bd=1)
            swatch.pack(side="left", padx=(2, 4))
            swatch.bind("<Button-1>", lambda e, n=name, cv=color_var:
                        self._pick_color(n, cv))
            self._ch_swatches[name].append(swatch)

            ttk.Button(row, text=name,
                       command=lambda n=name: self._view_channel(n)).pack(
                side="left", fill="x", expand=True)

    # ── Slide loading ─────────────────────────────────────────────────────────

    def _browse_and_load(self):
        # Try .mrxs file first; fall back to directory
        path = filedialog.askopenfilename(
            title="Open MRXS slide",
            filetypes=[("MRXS files", "*.mrxs"), ("All files", "*.*")])
        if not path:
            path = filedialog.askdirectory(title="Or select slide folder")
        if path:
            self._path_var.set(path)
            self._spawn(self._task_load, Path(path))

    def _load_from_entry(self):
        raw = self._path_var.get().strip()
        if not raw:
            messagebox.showwarning("No path", "Enter or browse to a slide path.")
            return
        p = Path(raw)
        if not p.exists():
            messagebox.showerror("Not found", f"Path does not exist:\n{p}")
            return
        self._spawn(self._task_load, p)

    def _task_load(self, path: Path):
        self._status("Loading slide…")
        try:
            if self._slide is not None:
                try:
                    self._slide.__exit__(None, None, None)
                except Exception:
                    pass
                self._slide = None
            self._cache.clear()

            slide = MrxsSlide(path)
            slide.__enter__()
            self._slide = slide
            self.after(0, self._on_slide_ready)
        except Exception as exc:
            self.after(0, self._status, f"Error: {exc}")
            self.after(0, messagebox.showerror, "Load error", str(exc))
            self.after(0, self._stop_pbar)

    def _on_slide_ready(self):
        s   = self._slide
        info = s.get_slide_info()

        # Info panel
        self._info["ID"].configure(text=info.get("slide_id", "—"))
        w, h = info.get("dimensions", (0, 0))
        self._info["Dimensions"].configure(text=f"{w} × {h} px")
        self._info["Levels"].configure(text=str(info.get("level_count", "—")))
        ps = info.get("pixel_size_um")
        self._info["Pixel size"].configure(
            text=f"{ps:.4f} µm/px" if ps else "—")
        obj = info.get("objective_magnification")
        self._info["Objective"].configure(text=f"{obj}×" if obj else "—")

        # Zoom range
        max_lvl = s.level_count - 1
        self._zoom_spin.configure(to=max_lvl)
        self._zoom_scale.configure(to=max_lvl)
        self._zoom_var.set(min(7, max_lvl))
        self._exp_zoom.set(min(7, max_lvl))

        self._rebuild_channels(s.channel_names)
        self._status(f"Loaded {s.slide_id} — "
                     f"{len(s.channel_names)} channels, {s.level_count} levels")
        self._stop_pbar()

        # Auto-preview first channel
        if s.channel_names:
            self._active_ch = s.channel_names[0]
            self._spawn(self._task_load_thumbnails)

    # ── Channel / composite view ──────────────────────────────────────────────

    def _view_channel(self, name: str):
        self._active_ch = name
        self._reload_view()

    def _reload_view(self):
        if self._slide is None:
            return
        self._spawn(self._task_show_channel, self._active_ch, self._zoom_var.get())

    def _task_show_channel(self, name: Optional[str], level: int):
        if not name or self._slide is None:
            return
        self._status(f"Loading {name} @ level {level}…")
        try:
            arr = self._get_array(name, level)
            if arr is None:
                self.after(0, self._status, f"No data for {name}")
                return
            photo = _arr_to_photo(arr)
            self.after(0, self._display_channel, name, level, photo)
        except Exception as exc:
            self.after(0, self._status, f"Error: {exc}")
            self.after(0, self._stop_pbar)

    def _display_channel(self, name: str, level: int, photo: ImageTk.PhotoImage):
        self._ch_canvas.show(photo)
        self._nb.select(0)
        self.title(f"MRXS  — {name} (level {level})")
        self._status(f"{name} @ level {level}: {photo.width()}×{photo.height()} px")
        self._stop_pbar()

    def _task_load_thumbnails(self):
        """Pre-load all channels at the highest zoom level for quick access."""
        if self._slide is None:
            return
        level = self._slide.level_count - 1
        for name in self._slide.channel_names:
            if self._slide is None:
                return
            self._get_array(name, level)  # caches result

        # Show first channel
        name = self._slide.channel_names[0] if self._slide else None
        if name:
            arr = self._cache.get(f"{name}@{level}")
            if arr is not None:
                photo = _arr_to_photo(arr)
                self.after(0, self._display_channel, name, level, photo)

    def _build_composite(self):
        if self._slide is None:
            return
        self._spawn(self._task_build_composite,
                    self._zoom_var.get(),
                    {n: self._ch_colors[n].get()
                     for n, v in self._ch_vars.items() if v.get()})

    def _task_build_composite(self, level: int, colors: Dict[str, str]):
        if not colors:
            self.after(0, messagebox.showwarning, "No channels",
                       "Check at least one channel in the sidebar.")
            return
        self._status("Building composite…")
        arrays = {}
        for name in colors:
            arr = self._get_array(name, level)
            if arr is not None:
                arrays[name] = arr
        if not arrays:
            self.after(0, self._status, "No channel data available")
            self.after(0, self._stop_pbar)
            return
        comp = _build_composite_arr(arrays, colors)
        photo = ImageTk.PhotoImage(
            Image.fromarray(self._scale_preview(comp), mode="RGB"))
        self.after(0, self._display_composite, photo,
                   list(colors.keys()), level)

    def _display_composite(self, photo: ImageTk.PhotoImage,
                            channels: List[str], level: int):
        self._comp_canvas.show(photo)
        self._nb.select(1)
        self._status(f"Composite [{', '.join(channels)}] @ level {level}")
        self._stop_pbar()

    # ── Color picker ──────────────────────────────────────────────────────────

    def _pick_color(self, name: str, color_var: tk.StringVar):
        result = askcolor(color=color_var.get(), title=f"Color for {name}")
        if result and result[1]:
            color_var.set(result[1])
            for sw in self._ch_swatches.get(name, []):
                sw.configure(bg=result[1])

    # ── Export ────────────────────────────────────────────────────────────────

    def _browse_export_dir(self):
        d = filedialog.askdirectory(title="Select export directory")
        if d:
            self._exp_dir_var.set(d)

    def _export_channels(self):
        if self._slide is None:
            messagebox.showwarning("No slide", "Load a slide first.")
            return
        selected = [n for n, v in self._ch_vars.items() if v.get()]
        if not selected:
            messagebox.showwarning("No channels",
                                   "Check at least one channel in the sidebar.")
            return
        out = self._exp_dir_var.get().strip()
        if not out:
            out = filedialog.askdirectory(title="Select export directory")
            if not out:
                return
            self._exp_dir_var.set(out)
        self._nb.select(2)
        self._spawn(self._task_export_channels,
                    Path(out), selected,
                    self._exp_zoom.get(), self._exp_fmt.get())

    def _task_export_channels(self, out_dir: Path, channels: List[str],
                               level: int, fmt: str):
        out_dir.mkdir(parents=True, exist_ok=True)
        slide_id = self._slide.slide_id
        for name in channels:
            self._status(f"Exporting {name}…")
            try:
                arr = self._slide.read_channel(name, level)
                if arr is None:
                    self._append_log(f"SKIP  {name}: no data returned")
                    continue
                stem = f"{slide_id}_{name}_L{level}"
                if fmt == "ome-tiff":
                    path = out_dir / f"{stem}.ome.tiff"
                    try:
                        import tifffile
                        ch = self._slide.get_channel(name)
                        pixel_size = self._slide.get_level_pixel_size(level)
                        ch_meta: dict = {'Name': name}
                        if ch and ch.excitation_wavelength:
                            ch_meta['ExcitationWavelength'] = ch.excitation_wavelength
                            ch_meta['ExcitationWavelengthUnit'] = 'nm'
                        if ch and ch.emission_wavelength:
                            ch_meta['EmissionWavelength'] = ch.emission_wavelength
                            ch_meta['EmissionWavelengthUnit'] = 'nm'
                        import numpy as _np
                        tifffile.imwrite(
                            str(path),
                            arr[_np.newaxis],  # (1, Y, X)
                            photometric='minisblack',
                            metadata={
                                'axes': 'CYX',
                                'PhysicalSizeX': pixel_size,
                                'PhysicalSizeXUnit': '\u00b5m',
                                'PhysicalSizeY': pixel_size,
                                'PhysicalSizeYUnit': '\u00b5m',
                                'Channel': [ch_meta],
                            },
                        )
                    except ImportError:
                        self._append_log(
                            f"ERR   tifffile not installed; "
                            f"install it with: pip install tifffile")
                        continue
                elif fmt == "tiff":
                    path = out_dir / f"{stem}.tiff"
                    try:
                        import tifffile
                        tifffile.imwrite(str(path), arr,
                                         metadata={"channel": name, "level": level})
                    except ImportError:
                        Image.fromarray(arr).save(str(path))
                else:
                    path = out_dir / f"{stem}.png"
                    Image.fromarray(arr).save(str(path))
                self._append_log(
                    f"OK    {path.name}  ({arr.shape[1]}\u00d7{arr.shape[0]})")
            except Exception as exc:
                self._append_log(f"ERR   {name}: {exc}")
        self.after(0, self._status, f"Export done \u2192 {out_dir}")
        self.after(0, self._stop_pbar)

    def _export_ometiff_stack(self):
        if self._slide is None:
            messagebox.showwarning("No slide", "Load a slide first.")
            return
        selected = [n for n, v in self._ch_vars.items() if v.get()]
        if not selected:
            messagebox.showwarning("No channels",
                                   "Check at least one channel in the sidebar.")
            return
        dest = filedialog.asksaveasfilename(
            title="Save OME-TIFF stack",
            defaultextension=".ome.tiff",
            filetypes=[("OME-TIFF", "*.ome.tiff"), ("All files", "*.*")])
        if not dest:
            return
        self._nb.select(2)
        self._spawn(self._task_export_ometiff_stack,
                    Path(dest), selected, self._exp_zoom.get())

    def _task_export_ometiff_stack(self, dest: Path, channels: List[str],
                                   level: int):
        self._status("Building OME-TIFF stack…")
        try:
            import tifffile
        except ImportError:
            self.after(0, messagebox.showerror, "Missing library",
                       "tifffile is required.\nInstall it with: pip install tifffile")
            self.after(0, self._stop_pbar)
            return
        try:
            self._slide.export_ome_tiff(dest, channels=channels, zoom_level=level)
            for name in channels:
                self._append_log(f"  channel: {name}")
            self._append_log(f"OK    {dest.name}  ({len(channels)} channels)")
            self.after(0, self._status, f"OME-TIFF stack saved: {dest}")
        except Exception as exc:
            self._append_log(f"ERR   {exc}")
            self.after(0, self._status, f"Error: {exc}")
        self.after(0, self._stop_pbar)

    def _export_composite(self):
        if self._slide is None:
            messagebox.showwarning("No slide", "Load a slide first.")
            return
        colors = {n: self._ch_colors[n].get()
                  for n, v in self._ch_vars.items() if v.get()}
        if not colors:
            messagebox.showwarning("No channels",
                                   "Check at least one channel in the sidebar.")
            return
        dest = filedialog.asksaveasfilename(
            title="Save composite",
            defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("TIFF image", "*.tiff"),
                       ("JPEG image", "*.jpg")])
        if not dest:
            return
        self._nb.select(2)
        self._spawn(self._task_export_composite,
                    Path(dest), colors, self._exp_zoom.get())

    def _task_export_composite(self, dest: Path, colors: Dict[str, str],
                                level: int):
        self._status("Building composite for export…")
        arrays = {}
        for name in colors:
            try:
                arr = self._slide.read_channel(name, level)
                if arr is not None:
                    arrays[name] = arr
            except Exception as exc:
                self._append_log(f"ERR   {name}: {exc}")

        if not arrays:
            self.after(0, self._status, "No data to composite")
            self.after(0, self._stop_pbar)
            return

        comp = _build_composite_arr(arrays, colors)
        Image.fromarray(comp, mode="RGB").save(str(dest))
        self._append_log(
            f"OK    composite → {dest.name}  ({comp.shape[1]}×{comp.shape[0]})")
        self.after(0, self._status, f"Composite saved: {dest}")
        self.after(0, self._stop_pbar)

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _get_array(self, name: str, level: int) -> Optional[np.ndarray]:
        key = f"{name}@{level}"
        if key not in self._cache:
            if self._slide is None:
                return None
            arr = self._slide.read_channel(name, level)
            if arr is not None:
                self._cache[key] = arr
        return self._cache.get(key)

    @staticmethod
    def _scale_preview(arr: np.ndarray, max_dim: int = 1200) -> np.ndarray:
        """Scale an RGB array so its longest edge is ≤ max_dim."""
        img = Image.fromarray(arr, mode="RGB")
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                             Image.LANCZOS)
        return np.array(img)

    def _placeholder(self, canvas: tk.Canvas):
        canvas.update_idletasks()
        cx = max(canvas.winfo_width() // 2, 300)
        cy = max(canvas.winfo_height() // 2, 200)
        canvas.create_text(cx, cy, text="No image loaded",
                           fill="#2a3060", font=("Segoe UI", 16))

    def _on_zoom_drag(self, val):
        self._zoom_var.set(int(float(val)))

    def _spawn(self, func, *args):
        if self._worker and self._worker.is_alive():
            return  # don't stack workers
        self._start_pbar()
        t = threading.Thread(target=func, args=args, daemon=True)
        self._worker = t
        t.start()

    def _start_pbar(self):
        self._pbar.start(12)

    def _stop_pbar(self):
        self._pbar.stop()

    def _status(self, msg: str):
        self.after(0, self._status_var.set, msg)

    def _append_log(self, msg: str):
        def _do():
            self._log.configure(state="normal")
            self._log.insert("end", msg + "\n")
            self._log.see("end")
            self._log.configure(state="disabled")
        self.after(0, _do)

    def _show_about(self):
        messagebox.showinfo(
            "About MRXS Viewer",
            "MRXS Reader v0.2.0\n\n"
            "Pure-Python browser for 3DHISTECH MRXS\n"
            "multi-channel fluorescence slides.\n\n"
            "• Channel View — browse individual channels\n"
            "• Composite — false-color multi-channel blend\n"
            "• Export — save channels or composites to disk\n\n"
            "Tip: click a channel name to view it;\n"
            "click the color swatch to change its composite color.")

    def destroy(self):
        if self._slide is not None:
            try:
                self._slide.__exit__(None, None, None)
            except Exception:
                pass
        super().destroy()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    app = MrxsViewerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
