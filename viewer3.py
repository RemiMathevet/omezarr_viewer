"""
Viewer OME-Zarr amélioré avec navigation, zoom et liste de fichiers
Rémi - CHU Besançon

Améliorations:
- Centrage automatique de l'image à l'ouverture
- Contraintes pour ne pas sortir de l'image
- Panneau latéral pour charger plusieurs images d'un dossier
"""

import numpy as np
import zarr
import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
from PIL import Image, ImageTk, ImageDraw
from collections import OrderedDict


class TileCache:
    """Cache LRU simple pour les tuiles"""
    def __init__(self, max_size=50):
        self.cache = OrderedDict()
        self.max_size = max_size
    
    def get(self, key):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None
    
    def put(self, key, value):
        if key in self.cache:
            self.cache.move_to_end(key)
        else:
            if len(self.cache) >= self.max_size:
                self.cache.popitem(last=False)
            self.cache[key] = value
    
    def clear(self):
        self.cache.clear()


class OMEZarrViewer:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("OME-Zarr Viewer")
        self.root.geometry("1400x900")
        
        # État image
        self.zarr_store = None
        self.zarr_path = None
        self.pyramid = []
        self.current_level = 0
        self.view_x = 0
        self.view_y = 0
        self.canvas_width = 1000
        self.canvas_height = 700
        
        # Cache
        self.tile_cache = TileCache(max_size=100)
        
        # Drag
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.dragging = False
        
        # Liste des fichiers zarr trouvés
        self.zarr_files = []
        self.root_folder = None
        
        # Annotations
        self.annotations = []  # Liste des features GeoJSON
        self.annotations_visible = tk.BooleanVar(value=True)
        self.annotation_levels = {}  # Niveaux d'annotation (couleurs, etc.)
        
        # Mode d'affichage des fichiers
        self.view_mode = tk.StringVar(value="list")  # "list" ou "thumbnails"
        self.thumbnails = {}  # Cache des thumbnails {path: PhotoImage}
        self.thumbnail_size = 80  # Taille des vignettes
        
        self._setup_ui()
        self.root.mainloop()
    
    def _setup_ui(self):
        # === PanedWindow principal (gauche/droite) ===
        self.paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        self.paned.pack(fill=tk.BOTH, expand=True)
        
        # === Panneau gauche : liste des fichiers ===
        left_panel = ttk.Frame(self.paned, width=280)
        self.paned.add(left_panel, weight=0)
        
        # Toolbar fichiers
        files_toolbar = ttk.Frame(left_panel)
        files_toolbar.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Button(files_toolbar, text="📂 Dossier", command=self._open_root_folder).pack(side=tk.LEFT, padx=2)
        ttk.Button(files_toolbar, text="🔄", width=3, command=self._refresh_file_list).pack(side=tk.LEFT, padx=2)
        ttk.Button(files_toolbar, text="🔍", width=3, command=self._debug_folder).pack(side=tk.LEFT, padx=2)
        
        # Boutons mode d'affichage
        ttk.Separator(files_toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)
        self.list_btn = ttk.Button(files_toolbar, text="≡", width=2, command=lambda: self._set_view_mode("list"))
        self.list_btn.pack(side=tk.LEFT, padx=1)
        self.thumb_btn = ttk.Button(files_toolbar, text="▦", width=2, command=lambda: self._set_view_mode("thumbnails"))
        self.thumb_btn.pack(side=tk.LEFT, padx=1)
        
        # Label dossier courant
        self.folder_label = ttk.Label(left_panel, text="Aucun dossier", wraplength=260, foreground="gray")
        self.folder_label.pack(fill=tk.X, padx=5, pady=(0, 5))
        
        # Container pour les fichiers (liste ou thumbnails)
        tree_frame = ttk.LabelFrame(left_panel, text="Fichiers OME-Zarr", padding=5)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Container qui contiendra soit le treeview soit le canvas de thumbnails
        self.files_container = ttk.Frame(tree_frame)
        self.files_container.pack(fill=tk.BOTH, expand=True)
        
        # Treeview pour le mode liste
        self.tree_frame = ttk.Frame(self.files_container)
        self.tree_frame.pack(fill=tk.BOTH, expand=True)
        
        self.file_tree = ttk.Treeview(self.tree_frame, selectmode='browse', show='tree')
        tree_scroll = ttk.Scrollbar(self.tree_frame, orient=tk.VERTICAL, command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=tree_scroll.set)
        
        self.file_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Bindings treeview
        self.file_tree.bind("<Double-1>", self._on_file_double_click)
        self.file_tree.bind("<Return>", self._on_file_double_click)
        
        # Canvas pour le mode thumbnails (créé mais caché)
        self.thumb_frame = ttk.Frame(self.files_container)
        # Ne pas pack - sera affiché quand on bascule en mode thumbnails
        
        # Utiliser grid pour garantir que la scrollbar est toujours visible
        self.thumb_frame.columnconfigure(0, weight=1)
        self.thumb_frame.rowconfigure(0, weight=1)
        
        self.thumb_canvas = tk.Canvas(self.thumb_frame, bg="#2a2a3a", highlightthickness=0, width=100)
        self.thumb_scrollbar = ttk.Scrollbar(self.thumb_frame, orient=tk.VERTICAL, command=self.thumb_canvas.yview)
        self.thumb_canvas.configure(yscrollcommand=self.thumb_scrollbar.set)
        
        self.thumb_canvas.grid(row=0, column=0, sticky="nsew")
        self.thumb_scrollbar.grid(row=0, column=1, sticky="ns")
        
        # Frame interne pour les thumbnails
        self.thumb_inner = ttk.Frame(self.thumb_canvas)
        self.thumb_canvas_window = self.thumb_canvas.create_window((0, 0), window=self.thumb_inner, anchor='nw')
        
        # Binding pour redimensionner
        self.thumb_inner.bind("<Configure>", self._on_thumb_frame_configure)
        self.thumb_canvas.bind("<Configure>", self._on_thumb_canvas_configure)
        
        # Binding molette souris pour scroll
        self.thumb_canvas.bind("<MouseWheel>", self._on_thumb_mousewheel)
        self.thumb_canvas.bind("<Button-4>", lambda e: self.thumb_canvas.yview_scroll(-1, "units"))
        self.thumb_canvas.bind("<Button-5>", lambda e: self.thumb_canvas.yview_scroll(1, "units"))
        self.thumb_inner.bind("<MouseWheel>", self._on_thumb_mousewheel)
        self.thumb_inner.bind("<Button-4>", lambda e: self.thumb_canvas.yview_scroll(-1, "units"))
        self.thumb_inner.bind("<Button-5>", lambda e: self.thumb_canvas.yview_scroll(1, "units"))
        
        # Compteur fichiers
        self.file_count_label = ttk.Label(left_panel, text="0 fichier(s)")
        self.file_count_label.pack(fill=tk.X, padx=5, pady=5)
        
        # === Panneau droit : viewer ===
        right_panel = ttk.Frame(self.paned)
        self.paned.add(right_panel, weight=1)
        
        # Toolbar viewer
        ctrl_frame = ttk.Frame(right_panel, padding=5)
        ctrl_frame.pack(fill=tk.X)
        
        ttk.Button(ctrl_frame, text="📄 Ouvrir fichier", command=self._open_single_file).pack(side=tk.LEFT, padx=5)
        
        ttk.Separator(ctrl_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        
        ttk.Label(ctrl_frame, text="Niveau:").pack(side=tk.LEFT, padx=(5, 2))
        self.level_var = tk.StringVar(value="0")
        self.level_combo = ttk.Combobox(ctrl_frame, textvariable=self.level_var, width=5, state="readonly")
        self.level_combo.pack(side=tk.LEFT)
        self.level_combo.bind("<<ComboboxSelected>>", self._on_level_change)
        
        ttk.Button(ctrl_frame, text="⌂", width=3, command=self._center_view).pack(side=tk.LEFT, padx=10)
        
        # Bouton annotations
        ttk.Separator(ctrl_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)
        self.annot_check = ttk.Checkbutton(ctrl_frame, text="📝 Annotations", 
                                            variable=self.annotations_visible,
                                            command=self._render)
        self.annot_check.pack(side=tk.LEFT, padx=5)
        
        self.annot_count_label = ttk.Label(ctrl_frame, text="", foreground="gray")
        self.annot_count_label.pack(side=tk.LEFT, padx=2)
        
        self.info_label = ttk.Label(ctrl_frame, text="Aucun fichier chargé")
        self.info_label.pack(side=tk.LEFT, padx=20)
        
        self.pos_label = ttk.Label(ctrl_frame, text="")
        self.pos_label.pack(side=tk.RIGHT, padx=10)
        
        # Canvas
        canvas_frame = ttk.Frame(right_panel)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.canvas = tk.Canvas(canvas_frame, bg="#1a1a2e", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        # Barre de statut
        self.status_var = tk.StringVar(value="Prêt - Ouvrez un dossier ou un fichier OME-Zarr")
        status_bar = ttk.Label(right_panel, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(fill=tk.X)
        
        # Bindings canvas
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_drag_end)
        self.canvas.bind("<MouseWheel>", self._on_scroll)
        self.canvas.bind("<Button-4>", self._on_scroll_up)
        self.canvas.bind("<Button-5>", self._on_scroll_down)
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<Motion>", self._on_mouse_move)
        
        # Raccourcis clavier
        self.root.bind("<Home>", lambda e: self._center_view())
        self.root.bind("<F5>", lambda e: self._refresh_file_list())
        self.root.bind("<a>", lambda e: self._toggle_annotations())
        self.root.bind("<A>", lambda e: self._toggle_annotations())
    
    # =========================================================================
    # Gestion des fichiers et dossiers
    # =========================================================================
    
    def _open_root_folder(self):
        """Ouvre un dossier racine et scanne les .zarr"""
        folder = filedialog.askdirectory(title="Sélectionner le dossier contenant les fichiers OME-Zarr")
        if not folder:
            return
        
        self.root_folder = Path(folder)
        self.folder_label.config(text=str(self.root_folder), foreground="black")
        self._scan_zarr_files()
        self._populate_file_tree()
    
    def _scan_zarr_files(self):
        """Scanne le dossier pour trouver les fichiers .zarr (non récursif pour éviter les blocages MRXS)"""
        self.zarr_files = []
        
        if not self.root_folder:
            return
        
        def is_ome_zarr(path):
            """Vérifie si c'est un OME-Zarr valide (v2 ou v3)"""
            p = Path(path)
            try:
                # Méthode 1: Zarr v3 (zarr.json à la racine)
                if (p / 'zarr.json').exists():
                    # Vérifie qu'il y a au moins un niveau pyramidal (dossier "0")
                    if (p / '0').is_dir():
                        return True
                
                # Méthode 2: Zarr v2 (.zgroup ou .zattrs)
                has_zgroup = (p / '.zgroup').exists()
                has_zattrs = (p / '.zattrs').exists()
                
                if has_zgroup or has_zattrs:
                    if (p / '0').is_dir():
                        return True
                    if (p / '.zarray').exists():
                        return True
                
                # Méthode 3: Dossier .zarr/.ome.zarr avec sous-dossiers numériques
                if p.suffix in ['.zarr'] or '.zarr' in p.name:
                    if (p / '0').is_dir():
                        return True
                
                return False
            except (PermissionError, OSError):
                return False
        
        def is_zarr_zip(path):
            """Vérifie si c'est un fichier ZIP contenant un Zarr"""
            p = Path(path)
            if not p.is_file():
                return False
            if p.suffix.lower() != '.zip':
                return False
            
            # Patterns reconnus: *.zarr.zip, *.ome.zarr.zip, *_zarr.zip, etc.
            name_lower = p.name.lower()
            if '.zarr.zip' in name_lower:
                return True
            if 'zarr' in p.stem.lower():
                return True
            
            # Vérifier le contenu du ZIP (optionnel, plus lent)
            # On pourrait ouvrir le ZIP et chercher zarr.json ou .zgroup
            
            return False
        
        def is_mrxs_folder(path):
            """Détecte les dossiers MRXS à ignorer (contiennent des milliers de tuiles)"""
            p = Path(path)
            # Les dossiers MRXS ont souvent un fichier .mrxs associé ou contiennent des .dat/.jpg
            if p.suffix.lower() == '.mrxs':
                return True
            # Vérifie si c'est un dossier data MRXS (nom commence par le fichier mrxs)
            parent = p.parent
            mrxs_file = parent / f"{p.name}.mrxs"
            if mrxs_file.exists():
                return True
            return False
        
        scanned_count = 0
        skipped_count = 0
        zip_count = 0
        
        # Scan non-récursif du dossier principal
        try:
            for item in self.root_folder.iterdir():
                # Fichiers ZIP zarr
                if item.is_file():
                    if is_zarr_zip(item):
                        self.zarr_files.append(item)
                        zip_count += 1
                    continue
                
                # Dossiers
                if not item.is_dir():
                    continue
                
                # Ignore les dossiers cachés et MRXS
                if item.name.startswith('.'):
                    continue
                if is_mrxs_folder(item):
                    skipped_count += 1
                    continue
                
                scanned_count += 1
                
                # Vérifie si c'est un OME-Zarr (par extension ou contenu)
                if item.suffix in ['.zarr', '.ome.zarr'] or 'zarr' in item.name.lower():
                    if is_ome_zarr(item):
                        self.zarr_files.append(item)
                elif is_ome_zarr(item):
                    self.zarr_files.append(item)
            
            # Scan un niveau plus profond (sous-dossiers directs) - mais pas dans les dossiers MRXS
            for subdir in self.root_folder.iterdir():
                if not subdir.is_dir():
                    continue
                if subdir.name.startswith('.'):
                    continue
                if is_mrxs_folder(subdir):
                    continue
                # Ignore les dossiers déjà identifiés comme zarr
                if subdir in self.zarr_files:
                    continue
                
                try:
                    for item in subdir.iterdir():
                        # Fichiers ZIP zarr dans le sous-dossier
                        if item.is_file():
                            if is_zarr_zip(item):
                                self.zarr_files.append(item)
                                zip_count += 1
                            continue
                        
                        if not item.is_dir():
                            continue
                        if item.name.startswith('.'):
                            continue
                        if is_mrxs_folder(item):
                            continue
                        
                        scanned_count += 1
                        
                        if item.suffix in ['.zarr', '.ome.zarr'] or 'zarr' in item.name.lower():
                            if is_ome_zarr(item):
                                self.zarr_files.append(item)
                        elif is_ome_zarr(item):
                            self.zarr_files.append(item)
                except (PermissionError, OSError):
                    pass
                    
        except (PermissionError, OSError) as e:
            self._set_status(f"Erreur de scan: {e}")
        
        self.zarr_files = sorted(set(self.zarr_files))
        self.file_count_label.config(text=f"{len(self.zarr_files)} fichier(s)")
        
        if self.zarr_files:
            zip_info = f", {zip_count} ZIP" if zip_count > 0 else ""
            self._set_status(f"{len(self.zarr_files)} OME-Zarr trouvé(s){zip_info} (scanné: {scanned_count}, ignoré: {skipped_count} MRXS)")
        else:
            self._set_status(f"Aucun OME-Zarr trouvé (scanné: {scanned_count} dossiers, ignoré: {skipped_count} MRXS)")
    
    def _populate_file_tree(self):
        """Remplit l'arborescence des fichiers"""
        self.file_tree.delete(*self.file_tree.get_children())
        
        if not self.root_folder or not self.zarr_files:
            return
        
        # Organise par sous-dossiers
        tree_structure = {}
        
        for zarr_path in self.zarr_files:
            try:
                rel_path = zarr_path.relative_to(self.root_folder)
                parts = rel_path.parts
            except ValueError:
                parts = (zarr_path.name,)
            
            # Construit la hiérarchie
            current = tree_structure
            for part in parts[:-1]:
                if part not in current:
                    current[part] = {"__children__": {}}
                current = current[part]["__children__"]
            
            # Feuille = chemin complet
            current[parts[-1]] = str(zarr_path)
        
        def insert_tree(parent, structure):
            for name, value in sorted(structure.items()):
                if name == "__children__":
                    continue
                if isinstance(value, dict):
                    # Dossier
                    node = self.file_tree.insert(parent, 'end', text=f"📁 {name}", open=True)
                    if "__children__" in value:
                        insert_tree(node, value["__children__"])
                else:
                    # Fichier zarr (dossier ou ZIP)
                    path_obj = Path(value)
                    if path_obj.suffix == '.zip':
                        # Fichier ZIP
                        display_name = name.replace('.ome.zarr.zip', '').replace('.zarr.zip', '').replace('.zip', '')
                        self.file_tree.insert(parent, 'end', text=f"📦 {display_name}", values=(value,))
                    else:
                        # Dossier zarr
                        display_name = name.replace('.ome.zarr', '').replace('.zarr', '')
                        self.file_tree.insert(parent, 'end', text=f"🔬 {display_name}", values=(value,))
        
        insert_tree("", tree_structure)
    
    def _set_view_mode(self, mode):
        """Change le mode d'affichage (list ou thumbnails)"""
        if mode == self.view_mode.get():
            return
        
        self.view_mode.set(mode)
        
        if mode == "list":
            self.thumb_frame.pack_forget()
            self.tree_frame.pack(fill=tk.BOTH, expand=True)
            self.list_btn.state(['pressed'])
            self.thumb_btn.state(['!pressed'])
        else:
            self.tree_frame.pack_forget()
            self.thumb_frame.pack(fill=tk.BOTH, expand=True)
            self.list_btn.state(['!pressed'])
            self.thumb_btn.state(['pressed'])
            self._populate_thumbnails()
    
    def _on_thumb_frame_configure(self, event):
        """Met à jour la zone de scroll du canvas"""
        self.thumb_canvas.configure(scrollregion=self.thumb_canvas.bbox("all"))
    
    def _on_thumb_canvas_configure(self, event):
        """Ajuste la largeur du frame interne"""
        self.thumb_canvas.itemconfig(self.thumb_canvas_window, width=event.width)
        # Recalculer les colonnes si nécessaire
        if self.view_mode.get() == "thumbnails" and self.zarr_files:
            self._populate_thumbnails()
    
    def _on_thumb_mousewheel(self, event):
        """Gère le scroll molette sur le canvas des thumbnails"""
        self.thumb_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    
    def _populate_thumbnails(self):
        """Remplit le canvas avec les thumbnails"""
        # Vider les anciens widgets
        for widget in self.thumb_inner.winfo_children():
            widget.destroy()
        
        if not self.zarr_files:
            return
        
        # Calculer le nombre de colonnes
        canvas_width = self.thumb_canvas.winfo_width()
        if canvas_width < 50:
            canvas_width = 250
        cols = max(1, canvas_width // (self.thumbnail_size + 20))
        
        row = 0
        col = 0
        
        for zarr_path in self.zarr_files:
            # Créer un frame pour chaque thumbnail
            thumb_widget = self._create_thumbnail_widget(zarr_path, row, col)
            
            col += 1
            if col >= cols:
                col = 0
                row += 1
        
        # Forcer la mise à jour du scroll
        self.thumb_inner.update_idletasks()
        self.thumb_canvas.configure(scrollregion=self.thumb_canvas.bbox("all"))
    
    def _create_thumbnail_widget(self, zarr_path, row, col):
        """Crée un widget thumbnail pour un fichier zarr"""
        frame = ttk.Frame(self.thumb_inner, padding=5)
        frame.grid(row=row, column=col, padx=5, pady=5, sticky="n")
        
        # Binding scroll molette pour propager au canvas parent
        def bind_mousewheel(widget):
            widget.bind("<MouseWheel>", self._on_thumb_mousewheel)
            widget.bind("<Button-4>", lambda e: self.thumb_canvas.yview_scroll(-1, "units"))
            widget.bind("<Button-5>", lambda e: self.thumb_canvas.yview_scroll(1, "units"))
        
        bind_mousewheel(frame)
        
        # Générer ou récupérer le thumbnail
        path_str = str(zarr_path)
        
        if path_str not in self.thumbnails:
            # Générer le thumbnail en arrière-plan
            self._generate_thumbnail_async(zarr_path, frame)
            # Placeholder en attendant
            placeholder = tk.Canvas(frame, width=self.thumbnail_size, height=self.thumbnail_size, 
                                   bg="#3a3a4a", highlightthickness=1, highlightbackground="#555")
            placeholder.create_text(self.thumbnail_size//2, self.thumbnail_size//2, 
                                   text="⏳", fill="white", font=("Arial", 20))
            placeholder.pack()
            placeholder.bind("<Double-1>", lambda e, p=path_str: self._load_zarr(p))
            bind_mousewheel(placeholder)
        else:
            # Afficher le thumbnail existant
            thumb_label = ttk.Label(frame, image=self.thumbnails[path_str])
            thumb_label.image = self.thumbnails[path_str]  # Garder référence
            thumb_label.pack()
            thumb_label.bind("<Double-1>", lambda e, p=path_str: self._load_zarr(p))
            bind_mousewheel(thumb_label)
        
        # Nom du fichier
        name = zarr_path.name.replace('.ome.zarr', '').replace('.zarr', '').replace('.zip', '')
        if len(name) > 12:
            name = name[:10] + "…"
        
        name_label = ttk.Label(frame, text=name, font=("Arial", 8), wraplength=self.thumbnail_size)
        name_label.pack()
        name_label.bind("<Double-1>", lambda e, p=path_str: self._load_zarr(p))
        bind_mousewheel(name_label)
        
        # Stocker la référence au path
        frame.zarr_path = path_str
        frame.bind("<Double-1>", lambda e, p=path_str: self._load_zarr(p))
        
        return frame
    
    def _generate_thumbnail_async(self, zarr_path, frame):
        """Génère un thumbnail en arrière-plan"""
        import threading
        
        def generate():
            try:
                thumb_image = self._generate_thumbnail(zarr_path)
                if thumb_image:
                    # Mettre à jour l'UI dans le thread principal
                    self.root.after(0, lambda: self._update_thumbnail_widget(frame, zarr_path, thumb_image))
            except Exception as e:
                print(f"Erreur génération thumbnail {zarr_path}: {e}")
        
        threading.Thread(target=generate, daemon=True).start()
    
    def _generate_thumbnail(self, zarr_path):
        """Génère un thumbnail depuis le niveau le plus bas de la pyramide"""
        try:
            path_str = str(zarr_path)
            path_obj = Path(zarr_path)
            is_zip = path_obj.is_file() and path_obj.suffix.lower() == '.zip'
            
            if is_zip:
                import zipfile
                # Ouvrir le ZIP et trouver le chemin racine
                with zipfile.ZipFile(path_str, 'r') as zf:
                    namelist = zf.namelist()
                    root_path = ""
                    for name in namelist:
                        if name.endswith('zarr.json') or name.endswith('.zgroup') or name.endswith('.zattrs'):
                            parts = name.replace('\\', '/').split('/')
                            if len(parts) > 1:
                                root_path = '/'.join(parts[:-1])
                            break
                
                zip_store = zarr.storage.ZipStore(path_str, mode='r')
                if root_path:
                    store = zarr.open_group(zip_store, mode='r', path=root_path)
                else:
                    store = zarr.open(zip_store, mode='r')
            else:
                store = zarr.open(path_str, mode='r')
            
            # Trouver le niveau le plus bas (thumbnail)
            levels = []
            for key in store.keys():
                if key.isdigit():
                    levels.append(int(key))
            
            if not levels:
                return None
            
            max_level = max(levels)
            arr = store[str(max_level)]
            
            # Lire l'image entière du niveau le plus bas
            shape = arr.shape
            if len(shape) == 2:
                data = arr[:]
                data = np.stack([data]*3, axis=-1)
            elif len(shape) == 3:
                if shape[0] <= 4:  # (C, Y, X)
                    data = np.moveaxis(arr[:], 0, -1)
                else:  # (Y, X, C)
                    data = arr[:]
            else:
                # (T, C, Y, X) ou similaire
                data = np.moveaxis(arr[0, :], 0, -1)
            
            # S'assurer qu'on a 3 canaux RGB
            if data.ndim == 2:
                data = np.stack([data]*3, axis=-1)
            elif data.shape[-1] == 1:
                data = np.repeat(data, 3, axis=-1)
            elif data.shape[-1] > 3:
                data = data[..., :3]
            
            # Normaliser si nécessaire
            if data.dtype != np.uint8:
                if data.max() > 0:
                    data = (data.astype(np.float32) / data.max() * 255).astype(np.uint8)
                else:
                    data = data.astype(np.uint8)
            
            # Créer l'image PIL et redimensionner
            pil_img = Image.fromarray(data)
            pil_img.thumbnail((self.thumbnail_size, self.thumbnail_size), Image.Resampling.LANCZOS)
            
            # Créer un carré avec fond gris
            thumb = Image.new('RGB', (self.thumbnail_size, self.thumbnail_size), (50, 50, 60))
            # Centrer l'image
            x = (self.thumbnail_size - pil_img.width) // 2
            y = (self.thumbnail_size - pil_img.height) // 2
            thumb.paste(pil_img, (x, y))
            
            # Convertir en PhotoImage
            photo = ImageTk.PhotoImage(thumb)
            
            # Stocker dans le cache
            self.thumbnails[path_str] = photo
            
            return photo
            
        except Exception as e:
            print(f"Erreur lecture thumbnail {zarr_path}: {e}")
            return None
    
    def _update_thumbnail_widget(self, frame, zarr_path, thumb_image):
        """Met à jour le widget thumbnail avec l'image générée"""
        path_str = str(zarr_path)
        
        # Supprimer le placeholder
        for widget in frame.winfo_children():
            if isinstance(widget, tk.Canvas):
                widget.destroy()
                break
        
        # Ajouter le vrai thumbnail
        thumb_label = ttk.Label(frame, image=thumb_image)
        thumb_label.image = thumb_image
        thumb_label.pack(before=frame.winfo_children()[0] if frame.winfo_children() else None)
        thumb_label.bind("<Double-1>", lambda e, p=path_str: self._load_zarr(p))
        
        # Binding scroll molette
        thumb_label.bind("<MouseWheel>", self._on_thumb_mousewheel)
        thumb_label.bind("<Button-4>", lambda e: self.thumb_canvas.yview_scroll(-1, "units"))
        thumb_label.bind("<Button-5>", lambda e: self.thumb_canvas.yview_scroll(1, "units"))
        thumb_label.bind("<Double-1>", lambda e, p=path_str: self._load_zarr(p))
    
    def _refresh_file_list(self):
        """Rafraîchit la liste des fichiers"""
        if self.root_folder:
            self.thumbnails.clear()  # Vider le cache des thumbnails
            self._scan_zarr_files()
            self._populate_file_tree()
            if self.view_mode.get() == "thumbnails":
                self._populate_thumbnails()
    
    def _debug_folder(self):
        """Affiche le contenu du dossier pour debug"""
        if not self.root_folder:
            messagebox.showinfo("Debug", "Aucun dossier sélectionné")
            return
        
        info_lines = [f"Dossier: {self.root_folder}\n"]
        
        try:
            for item in sorted(self.root_folder.iterdir()):
                if item.is_dir():
                    # Check structure Zarr v2 et v3
                    has_zgroup = (item / '.zgroup').exists()
                    has_zattrs = (item / '.zattrs').exists()
                    has_zarr_json = (item / 'zarr.json').exists()
                    has_level0 = (item / '0').is_dir() if (item / '0').exists() else False
                    
                    markers = []
                    if has_zarr_json:
                        markers.append("zarr.json")
                    if has_zgroup:
                        markers.append(".zgroup")
                    if has_zattrs:
                        markers.append(".zattrs")
                    if has_level0:
                        markers.append("0/")
                    
                    # Indication si c'est un OME-Zarr valide
                    is_valid = (has_zarr_json or has_zgroup or has_zattrs) and has_level0
                    valid_mark = " ✓" if is_valid else ""
                    
                    marker_str = f" [{', '.join(markers)}]{valid_mark}" if markers else ""
                    info_lines.append(f"📁 {item.name}{marker_str}")
                else:
                    # Fichier - vérifier si c'est un ZIP zarr
                    if item.suffix == '.zip' and 'zarr' in item.name.lower():
                        info_lines.append(f"📦 {item.name} [ZIP Zarr]")
                    else:
                        info_lines.append(f"📄 {item.name}")
        except Exception as e:
            info_lines.append(f"Erreur: {e}")
        
        info_lines.append(f"\n--- Fichiers détectés: {len(self.zarr_files)} ---")
        for zf in self.zarr_files:
            info_lines.append(f"  ✓ {zf.name}")
        
        # Afficher dans une fenêtre
        debug_win = tk.Toplevel(self.root)
        debug_win.title("Debug - Contenu du dossier")
        debug_win.geometry("600x400")
        
        text = tk.Text(debug_win, wrap=tk.WORD)
        text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        text.insert("1.0", "\n".join(info_lines))
        text.config(state=tk.DISABLED)
        
        ttk.Button(debug_win, text="Fermer", command=debug_win.destroy).pack(pady=5)
    
    def _on_file_double_click(self, event):
        """Double-clic sur un fichier dans l'arborescence"""
        selection = self.file_tree.selection()
        if not selection:
            return
        
        item = selection[0]
        values = self.file_tree.item(item, 'values')
        
        if values:
            zarr_path = values[0]
            self._load_zarr(zarr_path)
    
    def _open_single_file(self):
        """Ouvre un fichier OME-Zarr unique"""
        folder = filedialog.askdirectory(title="Sélectionner un dossier OME-Zarr (.zarr)")
        if not folder:
            return
        
        try:
            self._load_zarr(folder)
        except Exception as e:
            messagebox.showerror("Erreur", f"Impossible de charger le fichier:\n{e}")
    
    # =========================================================================
    # Chargement et rendu
    # =========================================================================
    
    def _load_zarr(self, path):
        """Charge un OME-Zarr (structure pyramidale) - supporte dossier ou ZIP"""
        self.zarr_path = str(path)
        path_obj = Path(path)
        
        # Déterminer si c'est un ZIP
        is_zip = path_obj.is_file() and path_obj.suffix.lower() == '.zip'
        
        if is_zip:
            # Ouvrir le ZIP et trouver le chemin racine du zarr
            import zipfile
            
            with zipfile.ZipFile(self.zarr_path, 'r') as zf:
                namelist = zf.namelist()
                
                # Trouver le chemin racine (chercher zarr.json, .zgroup, ou .zattrs)
                root_path = ""
                for name in namelist:
                    if name.endswith('zarr.json') or name.endswith('.zgroup') or name.endswith('.zattrs'):
                        # Extraire le chemin parent
                        parts = name.replace('\\', '/').split('/')
                        if len(parts) > 1:
                            root_path = '/'.join(parts[:-1])
                        break
                
                # Si pas trouvé, chercher un dossier "0" (niveau pyramidal)
                if not root_path:
                    for name in namelist:
                        parts = name.replace('\\', '/').split('/')
                        if '0' in parts:
                            idx = parts.index('0')
                            if idx > 0:
                                root_path = '/'.join(parts[:idx])
                            break
            
            # Créer le store ZIP avec le bon chemin
            zip_store = zarr.storage.ZipStore(self.zarr_path, mode='r')
            
            if root_path:
                self._set_status(f"ZIP: racine trouvée à '{root_path}'")
                # Utiliser un chemin dans le store
                self.zarr_store = zarr.open_group(zip_store, mode='r', path=root_path)
            else:
                self.zarr_store = zarr.open(zip_store, mode='r')
        else:
            self.zarr_store = zarr.open(self.zarr_path, mode='r')
        
        self.pyramid = []
        
        # Vide le cache pour le nouveau fichier
        self.tile_cache.clear()
        
        # Détecte les niveaux de résolution (0, 1, 2, ...)
        level = 0
        while str(level) in self.zarr_store:
            arr = self.zarr_store[str(level)]
            self.pyramid.append(arr)
            level += 1
        
        if not self.pyramid:
            if isinstance(self.zarr_store, zarr.Array):
                self.pyramid = [self.zarr_store]
            else:
                raise ValueError("Structure OME-Zarr non reconnue")
        
        # Config UI
        self.level_combo['values'] = list(range(len(self.pyramid)))
        
        # Démarre au niveau le plus bas (vue d'ensemble) puis centre
        start_level = len(self.pyramid) - 1
        self.level_combo.current(start_level)
        self.current_level = start_level
        
        # Centre la vue
        self._center_view()
        
        # Info
        base = self.pyramid[0]
        h, w = self._get_image_size(0)
        name = Path(path).name
        self.info_label.config(text=f"{name} | {w}×{h} | {base.dtype} | {len(self.pyramid)} niv.")
        self._set_status(f"Chargé: {name}")
        
        # Charger les annotations
        self._load_annotations()
        
        self._render()
    
    def _get_image_size(self, level):
        """Retourne (height, width) pour un niveau"""
        shape = self.pyramid[level].shape
        if len(shape) == 2:
            return shape[0], shape[1]
        elif len(shape) == 3:
            if shape[0] <= 4:
                return shape[1], shape[2]
            return shape[0], shape[1]
        else:
            return shape[-2], shape[-1]
    
    def _center_view(self):
        """Centre la vue sur l'image"""
        if not self.pyramid:
            return
        
        # Actualise les dimensions du canvas
        self.canvas_width = self.canvas.winfo_width()
        self.canvas_height = self.canvas.winfo_height()
        if self.canvas_width < 10:
            self.canvas_width = 800
            self.canvas_height = 600
        
        h, w = self._get_image_size(self.current_level)
        
        # Centre l'image
        self.view_x = (w - self.canvas_width) / 2
        self.view_y = (h - self.canvas_height) / 2
        
        # Clamp aux bords
        self._clamp_view()
        
        self._render()
    
    def _clamp_view(self):
        """Contraint la vue pour rester dans l'image"""
        if not self.pyramid:
            return
        
        h, w = self._get_image_size(self.current_level)
        
        # Limites maximales
        max_x = max(0, w - self.canvas_width)
        max_y = max(0, h - self.canvas_height)
        
        # Si l'image est plus petite que le canvas, centre-la
        if w <= self.canvas_width:
            self.view_x = -(self.canvas_width - w) / 2
        else:
            self.view_x = max(0, min(self.view_x, max_x))
        
        if h <= self.canvas_height:
            self.view_y = -(self.canvas_height - h) / 2
        else:
            self.view_y = max(0, min(self.view_y, max_y))
    
    def _get_tile(self, level, x, y, width, height):
        """Extrait une région de l'image avec cache"""
        # Clé de cache
        cache_key = (self.zarr_path, level, x, y, width, height)
        cached = self.tile_cache.get(cache_key)
        if cached is not None:
            return cached
        
        arr = self.pyramid[level]
        shape = arr.shape
        img_h, img_w = self._get_image_size(level)
        
        # Gère les coordonnées négatives (image plus petite que canvas)
        pad_left = max(0, -x)
        pad_top = max(0, -y)
        
        # Ajuste les coordonnées de lecture
        read_x = max(0, x)
        read_y = max(0, y)
        read_w = min(width - pad_left, img_w - read_x)
        read_h = min(height - pad_top, img_h - read_y)
        
        if read_w <= 0 or read_h <= 0:
            # Retourne une image noire si hors limites
            return np.zeros((height, width, 3), dtype=np.uint8)
        
        # Lecture selon le format
        if len(shape) == 2:
            tile = arr[read_y:read_y+read_h, read_x:read_x+read_w]
        elif len(shape) == 3:
            if shape[0] <= 4:  # (C, Y, X)
                tile = arr[:, read_y:read_y+read_h, read_x:read_x+read_w]
                tile = np.moveaxis(tile, 0, -1)
            else:  # (Y, X, C)
                tile = arr[read_y:read_y+read_h, read_x:read_x+read_w, :]
        elif len(shape) >= 4:
            if len(shape) == 4:
                tile = arr[0, :, read_y:read_y+read_h, read_x:read_x+read_w]
            else:
                tile = arr[0, 0, :, read_y:read_y+read_h, read_x:read_x+read_w]
            tile = np.moveaxis(tile, 0, -1)
        else:
            raise ValueError(f"Format non supporté: {shape}")
        
        tile = np.array(tile)
        
        # Crée l'image finale avec padding si nécessaire
        if pad_left > 0 or pad_top > 0 or tile.shape[0] < height or tile.shape[1] < width:
            if len(tile.shape) == 2:
                full_tile = np.zeros((height, width), dtype=tile.dtype)
                full_tile[pad_top:pad_top+tile.shape[0], pad_left:pad_left+tile.shape[1]] = tile
            else:
                full_tile = np.zeros((height, width, tile.shape[2]), dtype=tile.dtype)
                full_tile[pad_top:pad_top+tile.shape[0], pad_left:pad_left+tile.shape[1], :] = tile
            tile = full_tile
        
        self.tile_cache.put(cache_key, tile)
        return tile
    
    def _render(self):
        """Rendu de l'image"""
        if not self.pyramid:
            return
        
        # Dimensions canvas
        self.canvas_width = self.canvas.winfo_width()
        self.canvas_height = self.canvas.winfo_height()
        if self.canvas_width < 10:
            self.canvas_width = 800
            self.canvas_height = 600
        
        # Contraint la position
        self._clamp_view()
        
        # Extrait la tuile
        tile = self._get_tile(
            self.current_level,
            int(self.view_x), int(self.view_y),
            self.canvas_width, self.canvas_height
        )
        
        # Normalise pour affichage
        if tile.dtype != np.uint8:
            if tile.max() > 0:
                tile = (tile.astype(np.float32) / tile.max() * 255).astype(np.uint8)
            else:
                tile = tile.astype(np.uint8)
        
        # Convertit en RGB si nécessaire
        if len(tile.shape) == 2:
            img = Image.fromarray(tile, mode='L')
        elif tile.shape[2] == 1:
            img = Image.fromarray(tile[:, :, 0], mode='L')
        elif tile.shape[2] == 3:
            img = Image.fromarray(tile, mode='RGB')
        elif tile.shape[2] == 4:
            img = Image.fromarray(tile, mode='RGBA')
        else:
            img = Image.fromarray(tile[:, :, :3], mode='RGB')
        
        # Dessiner les annotations
        if self.annotations and self.annotations_visible.get():
            img = self._draw_annotations(img)
        
        # Affiche
        self.photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.photo)
        
        # Update position label
        h, w = self._get_image_size(self.current_level)
        self.pos_label.config(text=f"Vue: ({int(max(0, self.view_x))}, {int(max(0, self.view_y))}) | Image: {w}×{h}")
    
    # =========================================================================
    # Événements
    # =========================================================================
    
    def _on_level_change(self, event=None):
        old_level = self.current_level
        new_level = int(self.level_var.get())
        
        if old_level != new_level and self.pyramid:
            old_h, old_w = self._get_image_size(old_level)
            new_h, new_w = self._get_image_size(new_level)
            
            # Centre de la vue actuelle
            center_x = self.view_x + self.canvas_width / 2
            center_y = self.view_y + self.canvas_height / 2
            
            # Ratio
            ratio_x = new_w / old_w
            ratio_y = new_h / old_h
            
            # Nouvelle position centrée
            self.view_x = center_x * ratio_x - self.canvas_width / 2
            self.view_y = center_y * ratio_y - self.canvas_height / 2
            self.current_level = new_level
            
            self._render()
    
    def _on_drag_start(self, event):
        self.drag_start_x = event.x
        self.drag_start_y = event.y
        self.dragging = True
        self.canvas.config(cursor="fleur")
    
    def _on_drag(self, event):
        if self.dragging and self.pyramid:
            dx = self.drag_start_x - event.x
            dy = self.drag_start_y - event.y
            self.view_x += dx
            self.view_y += dy
            self.drag_start_x = event.x
            self.drag_start_y = event.y
            self._render()
    
    def _on_drag_end(self, event):
        self.dragging = False
        self.canvas.config(cursor="")
    
    def _on_scroll(self, event):
        if event.delta > 0:
            self._zoom_in(event.x, event.y)
        else:
            self._zoom_out(event.x, event.y)
    
    def _on_scroll_up(self, event):
        self._zoom_in(event.x, event.y)
    
    def _on_scroll_down(self, event):
        self._zoom_out(event.x, event.y)
    
    def _zoom_in(self, mouse_x, mouse_y):
        """Zoom in = niveau de résolution plus élevé (plus de détails)"""
        if self.current_level > 0:
            abs_x = self.view_x + mouse_x
            abs_y = self.view_y + mouse_y
            
            old_h, old_w = self._get_image_size(self.current_level)
            new_level = self.current_level - 1
            new_h, new_w = self._get_image_size(new_level)
            
            ratio_x = new_w / old_w
            ratio_y = new_h / old_h
            
            self.view_x = abs_x * ratio_x - mouse_x
            self.view_y = abs_y * ratio_y - mouse_y
            self.current_level = new_level
            self.level_var.set(str(new_level))
            
            self._render()
    
    def _zoom_out(self, mouse_x, mouse_y):
        """Zoom out = niveau de résolution plus bas (moins de détails)"""
        if self.current_level < len(self.pyramid) - 1:
            abs_x = self.view_x + mouse_x
            abs_y = self.view_y + mouse_y
            
            old_h, old_w = self._get_image_size(self.current_level)
            new_level = self.current_level + 1
            new_h, new_w = self._get_image_size(new_level)
            
            ratio_x = new_w / old_w
            ratio_y = new_h / old_h
            
            self.view_x = abs_x * ratio_x - mouse_x
            self.view_y = abs_y * ratio_y - mouse_y
            self.current_level = new_level
            self.level_var.set(str(new_level))
            
            self._render()
    
    def _on_resize(self, event):
        if self.pyramid:
            self._render()
    
    def _on_mouse_move(self, event):
        """Affiche les coordonnées sous le curseur"""
        if self.pyramid:
            # Coordonnées dans l'image au niveau courant
            img_x = int(self.view_x + event.x)
            img_y = int(self.view_y + event.y)
            
            # Coordonnées au niveau 0 (pleine résolution)
            h0, w0 = self._get_image_size(0)
            h_curr, w_curr = self._get_image_size(self.current_level)
            
            x0 = int(img_x * w0 / w_curr)
            y0 = int(img_y * h0 / h_curr)
            
            self._set_status(f"Position: ({x0}, {y0}) @ niveau 0 | Niveau actuel: {self.current_level}")
    
    def _toggle_annotations(self):
        """Bascule la visibilité des annotations"""
        self.annotations_visible.set(not self.annotations_visible.get())
        self._render()
    
    def _load_annotations(self):
        """Charge les annotations GeoJSON depuis le dossier zarr, ZIP ou les attrs"""
        self.annotations = []
        self.annotation_levels = {}
        
        if not self.zarr_path:
            self.annot_count_label.config(text="")
            return
        
        zarr_path = Path(self.zarr_path)
        is_zip = zarr_path.is_file() and zarr_path.suffix == '.zip'
        
        if is_zip:
            # Chercher les annotations dans le ZIP
            import zipfile
            try:
                with zipfile.ZipFile(self.zarr_path, 'r') as zf:
                    for name in zf.namelist():
                        if name.endswith('.geojson') or (name.endswith('.json') and 'annot' in name.lower()):
                            try:
                                with zf.open(name) as f:
                                    data = json.load(f)
                                if data.get("type") == "FeatureCollection":
                                    self.annotations.extend(data.get("features", []))
                                    props = data.get("properties", {})
                                    if "annotation_levels" in props:
                                        for level in props["annotation_levels"]:
                                            self.annotation_levels[level["id"]] = level
                            except Exception as e:
                                print(f"Erreur lecture annotation {name} dans ZIP: {e}")
            except Exception as e:
                print(f"Erreur ouverture ZIP pour annotations: {e}")
        else:
            # Méthode 1: Chercher un fichier .geojson dans le dossier zarr
            geojson_files = list(zarr_path.glob("*.geojson")) + list(zarr_path.glob("*.json"))
            
            for gj_file in geojson_files:
                try:
                    with open(gj_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    
                    if data.get("type") == "FeatureCollection":
                        self.annotations.extend(data.get("features", []))
                        props = data.get("properties", {})
                        if "annotation_levels" in props:
                            for level in props["annotation_levels"]:
                                self.annotation_levels[level["id"]] = level
                except Exception as e:
                    print(f"Erreur chargement {gj_file}: {e}")
        
        # Méthode 2: Chercher dans les attributs zarr (fonctionne pour ZIP et dossier)
        try:
            if self.zarr_store and 'annotations' in self.zarr_store.attrs:
                data = self.zarr_store.attrs['annotations']
                if isinstance(data, str):
                    data = json.loads(data)
                if isinstance(data, dict) and data.get("type") == "FeatureCollection":
                    self.annotations.extend(data.get("features", []))
                    props = data.get("properties", {})
                    if "annotation_levels" in props:
                        for level in props["annotation_levels"]:
                            self.annotation_levels[level["id"]] = level
        except Exception as e:
            print(f"Erreur chargement attrs: {e}")
        
        # Mise à jour UI
        count = len(self.annotations)
        if count > 0:
            self.annot_count_label.config(text=f"({count})")
            self._set_status(f"Chargé {count} annotation(s)")
        else:
            self.annot_count_label.config(text="")
    
    def _get_annotation_color(self, feature):
        """Retourne la couleur pour une annotation"""
        props = feature.get("properties", {})
        
        # Couleur explicite dans les propriétés
        if "color" in props:
            return props["color"]
        
        # Couleur basée sur le niveau d'annotation
        level_id = props.get("level_id")
        if level_id and level_id in self.annotation_levels:
            level = self.annotation_levels[level_id]
            # Chercher la couleur de la classe
            class_name = props.get("class_name", "")
            for cls in level.get("classes", []):
                if cls.get("name") == class_name:
                    return cls.get("color", level.get("color", "#FF0000"))
            return level.get("color", "#FF0000")
        
        # Couleur par défaut basée sur le type
        class_name = props.get("class_name", "").lower()
        if "villosit" in class_name:
            return "#8BC34A"
        elif "vaisseau" in class_name:
            return "#00BCD4"
        elif "calcif" in class_name:
            return "#FF9800"
        elif "fibrin" in class_name:
            return "#795548"
        elif "infarct" in class_name:
            return "#F44336"
        
        return "#FF5722"  # Orange par défaut
    
    def _draw_annotations(self, img):
        """Dessine les annotations sur l'image PIL"""
        if not self.annotations or not self.annotations_visible.get():
            return img
        
        # Créer un calque avec transparence
        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        
        # Facteur d'échelle pour convertir coordonnées niveau 0 -> niveau courant
        h0, w0 = self._get_image_size(0)
        h_curr, w_curr = self._get_image_size(self.current_level)
        scale = w_curr / w0
        
        for feature in self.annotations:
            geom = feature.get("geometry", {})
            geom_type = geom.get("type", "")
            coords = geom.get("coordinates", [])
            
            color_hex = self._get_annotation_color(feature)
            try:
                r = int(color_hex[1:3], 16)
                g = int(color_hex[3:5], 16)
                b = int(color_hex[5:7], 16)
            except:
                r, g, b = 255, 87, 34
            
            if geom_type == "Polygon" and coords:
                self._draw_polygon(draw, coords[0], scale, (r, g, b))
            elif geom_type == "MultiPolygon" and coords:
                for polygon in coords:
                    if polygon:
                        self._draw_polygon(draw, polygon[0], scale, (r, g, b))
            elif geom_type == "Point" and coords:
                self._draw_point(draw, coords, scale, (r, g, b))
            elif geom_type == "LineString" and coords:
                self._draw_line(draw, coords, scale, (r, g, b))
        
        # Fusionner avec l'image originale
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        img = Image.alpha_composite(img, overlay)
        return img.convert('RGB')
    
    def _draw_polygon(self, draw, coords, scale, color):
        """Dessine un polygone"""
        points = []
        for x, y in coords:
            # Convertir en coordonnées écran
            px = x * scale - self.view_x
            py = y * scale - self.view_y
            points.append((px, py))
        
        if len(points) < 3:
            return
        
        # Vérifier si le polygone est visible
        min_x = min(p[0] for p in points)
        max_x = max(p[0] for p in points)
        min_y = min(p[1] for p in points)
        max_y = max(p[1] for p in points)
        
        if max_x < 0 or min_x > self.canvas_width or max_y < 0 or min_y > self.canvas_height:
            return  # Hors écran
        
        r, g, b = color
        # Remplissage semi-transparent
        draw.polygon(points, fill=(r, g, b, 50), outline=(r, g, b, 200))
        # Contour plus épais
        for i in range(len(points)):
            p1 = points[i]
            p2 = points[(i + 1) % len(points)]
            draw.line([p1, p2], fill=(r, g, b, 255), width=2)
    
    def _draw_point(self, draw, coords, scale, color):
        """Dessine un point"""
        x, y = coords[0], coords[1] if len(coords) > 1 else coords[0]
        px = x * scale - self.view_x
        py = y * scale - self.view_y
        
        if px < -10 or px > self.canvas_width + 10 or py < -10 or py > self.canvas_height + 10:
            return
        
        r, g, b = color
        radius = 6
        draw.ellipse([px - radius, py - radius, px + radius, py + radius],
                     fill=(r, g, b, 200), outline=(255, 255, 255, 255))
    
    def _draw_line(self, draw, coords, scale, color):
        """Dessine une ligne"""
        points = []
        for x, y in coords:
            px = x * scale - self.view_x
            py = y * scale - self.view_y
            points.append((px, py))
        
        if len(points) < 2:
            return
        
        r, g, b = color
        draw.line(points, fill=(r, g, b, 255), width=2)
    
    def _set_status(self, message):
        """Met à jour la barre de statut"""
        self.status_var.set(message)


if __name__ == "__main__":
    OMEZarrViewer()
