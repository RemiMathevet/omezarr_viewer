# OME-Zarr Viewer

Viewer pyramidal pour lames virtuelles au format OME-Zarr avec support des archives ZIP et des annotations GeoJSON.

**Pathologie numérique — Projet open source**

---

## ✨ Fonctionnalités

- **Navigation pyramidale** : Zoom fluide multi-niveaux avec cache de tuiles LRU
- **Support ZIP** : Lecture directe des archives `.zarr.zip` et `.ome.zarr.zip`
- **Double mode d'affichage** : Liste arborescente ou grille de vignettes
- **Vignettes automatiques** : Génération asynchrone des previews
- **Annotations GeoJSON** : Affichage des polygones, points et lignes avec couleurs par classe
- **Centrage automatique** : L'image s'ouvre centrée dans la vue
- **Contraintes de navigation** : Impossible de sortir des limites de l'image

---

## 🔧 Installation

### Prérequis

**Ubuntu/Debian :**
```bash
sudo apt install python3-tk
```

**macOS / Windows :**
tkinter est inclus avec Python.

### Installation des dépendances

```bash
pip install -r requirements.txt
```

Ou manuellement :
```bash
pip install zarr numpy Pillow
```

---

## 🚀 Utilisation

```bash
python viewer3.py
```

### Interface

L'interface est divisée en deux panneaux :

| Panneau gauche | Panneau droit |
|----------------|---------------|
| Liste des fichiers | Viewer d'image |
| Boutons de navigation | Contrôles de zoom |
| Mode liste/vignettes | Barre de statut |

### Ouvrir des fichiers

1. **Dossier racine** : Cliquer sur `📂 Dossier` pour scanner un répertoire
2. **Fichier unique** : Cliquer sur `📄 Ouvrir fichier` pour un seul OME-Zarr
3. **Double-clic** : Sur un fichier dans la liste pour le charger

### Navigation

| Action | Commande |
|--------|----------|
| Déplacer | Clic gauche + glisser |
| Zoom avant | Molette ↑ |
| Zoom arrière | Molette ↓ |
| Centrer | Bouton `⌂` ou touche `Home` |
| Changer niveau | Menu déroulant "Niveau" |

### Raccourcis clavier

| Touche | Action |
|--------|--------|
| `Home` | Centrer la vue |
| `F5` | Rafraîchir la liste |
| `A` | Afficher/masquer les annotations |

---

## 📁 Formats supportés

### Structures OME-Zarr

Le viewer détecte automatiquement :

- **Zarr v2** : `.zgroup`, `.zattrs`
- **Zarr v3** : `zarr.json`
- **Extensions** : `.zarr`, `.ome.zarr`

### Archives ZIP

Formats reconnus :
- `*.zarr.zip`
- `*.ome.zarr.zip`
- Tout ZIP contenant "zarr" dans le nom

### Annotations

Le viewer charge les annotations depuis :

1. **Attributs Zarr** : `zarr_store.attrs['annotations']`
2. **Fichiers GeoJSON** : `*.geojson` ou `*.json` dans le dossier Zarr
3. **Dans les ZIP** : Fichiers JSON/GeoJSON intégrés

Format GeoJSON supporté :
```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[x1, y1], [x2, y2], ...]]
      },
      "properties": {
        "level_id": 2,
        "class_name": "villosité",
        "color": "#8BC34A"
      }
    }
  ]
}
```

---

## 🎨 Modes d'affichage

### Mode Liste (≡)

Arborescence classique avec icônes :
- `📁` Dossier
- `🔬` Fichier OME-Zarr
- `📦` Archive ZIP

### Mode Vignettes (▦)

Grille de previews générées automatiquement depuis le niveau le plus bas de la pyramide.

---

## 🔍 Debug

Le bouton `🔍` affiche une fenêtre de diagnostic montrant :
- Contenu du dossier scanné
- Marqueurs Zarr détectés (`.zgroup`, `.zattrs`, `zarr.json`)
- Fichiers OME-Zarr validés

---

## ⚙️ Configuration

### Cache de tuiles

Le viewer utilise un cache LRU de 100 tuiles par défaut. Modifiable dans le code :

```python
self.tile_cache = TileCache(max_size=100)
```

### Taille des vignettes

```python
self.thumbnail_size = 80  # pixels
```

---

## 🏗️ Architecture

```
viewer3.py
├── TileCache          # Cache LRU pour les tuiles
└── OMEZarrViewer      # Application principale
    ├── _setup_ui()           # Construction de l'interface
    ├── _scan_zarr_files()    # Détection des OME-Zarr
    ├── _load_zarr()          # Chargement (dossier ou ZIP)
    ├── _render()             # Rendu de l'image
    ├── _draw_annotations()   # Superposition des annotations
    └── _generate_thumbnail() # Création des previews
```

---

## 🔗 Compatibilité

Ce viewer est compatible avec les fichiers générés par :
- `mrxszarr6.py` (convertisseur MRXS → OME-Zarr)
- `omezarr_annotator2.py` (annotations hiérarchiques)

Les annotations créées avec l'annotateur s'affichent automatiquement avec leurs couleurs de classe.

---

## 📊 Performances

| Opération | Temps typique |
|-----------|---------------|
| Scan dossier (non-récursif) | < 1s |
| Chargement OME-Zarr | < 500ms |
| Chargement ZIP | < 1s |
| Génération vignette | ~200ms |
| Rendu tuile (cache miss) | ~50ms |
| Rendu tuile (cache hit) | < 1ms |

---

## 🐛 Dépannage

### "Aucun OME-Zarr trouvé"

- Vérifier que les fichiers ont l'extension `.zarr` ou `.ome.zarr`
- Utiliser le bouton `🔍` pour diagnostiquer la structure
- Le scan est non-récursif pour éviter les dossiers MRXS volumineux

### Annotations non visibles

- Vérifier que la checkbox `📝 Annotations` est cochée
- Appuyer sur `A` pour basculer l'affichage
- Les annotations doivent être au format GeoJSON valide

### Fichier ZIP non reconnu

Le nom doit contenir "zarr" :
- ✅ `lame.ome.zarr.zip`
- ✅ `lame.zarr.zip`
- ✅ `lame_zarr.zip`
- ❌ `lame.zip`

---

## 📄 Licence

Projet open source développé sur le temps libre de l'auteur, qui luttait contre l'attrait de son chat pour le clavier. 🐱⌨️

MIT License - Utilisation libre.
