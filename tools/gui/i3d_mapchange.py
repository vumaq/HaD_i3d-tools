#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import shutil
import struct
import subprocess
import ctypes

from dataclasses import dataclass, field
from typing import List, Optional

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QListWidget, QListWidgetItem, QFileDialog, QCheckBox,
    QScrollArea, QGridLayout, QFrame, QMessageBox, QSizePolicy, QMenu
)
from PyQt5.QtGui import QPixmap
from PyQt5.QtCore import Qt

# -----------------------------------------------------------------------------
# CONFIG FILE ALWAYS NEXT TO SCRIPT
# -----------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "model_texture_browser_config.json")

# -----------------------------------------------------------------------------
# WINDOWS ADMIN CHECK
# -----------------------------------------------------------------------------

def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except:
        return False

# =============================================================================
# 3DS / I3D Chunk structures
# =============================================================================

@dataclass
class ChunkNode:
    cid: int
    start: int
    size: int
    children: List["ChunkNode"] = field(default_factory=list)
    parent: Optional["ChunkNode"] = field(default=None, repr=False)

    @property
    def payload_start(self): return self.start + 6
    @property
    def payload_end(self): return self.start + self.size
    @property
    def payload_size(self): return max(0, self.size - 6)

    def add_child(self, node):
        node.parent = self
        self.children.append(node)


class ChunkParser:
    def __init__(self, data):
        self.data = data

    @staticmethod
    def u16(b, o): return struct.unpack_from("<H", b, o)[0]
    @staticmethod
    def u32(b, o): return struct.unpack_from("<I", b, o)[0]

    def parse_chunk(self, off, end, parent):
        d = self.data
        if off + 6 > end:
            return None, off

        try:
            cid = self.u16(d, off)
            length = self.u32(d, off + 2)
        except:
            return None, end

        if length < 6 or off + length > end:
            return None, end

        node = ChunkNode(cid, off, length)
        parent.add_child(node)

        pstart = node.payload_start
        pend = node.payload_end

        # Named object (string then subchunks)
        if cid == 0x4000:
            pos = pstart
            while pos < pend and d[pos] != 0:
                pos += 1
            if pos < pend:
                pos += 1

            while pos + 6 <= pend:
                ch, nxt = self.parse_chunk(pos, pend, node)
                if not ch or nxt <= pos:
                    break
                pos = nxt

        # Container chunks
        elif cid in (
            0x4D4D, 0x3D3D, 0x4100, 0xAFFF, 0xA200,
            0xB000, 0xB002, 0xB003, 0xB004,
            0xB005, 0xB006, 0xB007, 0xB010
        ):
            pos = pstart
            while pos + 6 <= pend:
                ch, nxt = self.parse_chunk(pos, pend, node)
                if not ch or nxt <= pos:
                    break
                pos = nxt

        return node, off + length

    def parse(self):
        root = ChunkNode(0xFFFF, 0, len(self.data))
        pos = 0
        end = len(self.data)

        while pos + 6 <= end:
            ch, nxt = self.parse_chunk(pos, end, root)
            if not ch or nxt <= pos:
                break
            pos = nxt

        return root


# =============================================================================
# MODEL / TEXTURE BROWSER CLASS
# =============================================================================

class ModelTextureBrowser(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Model / Texture Browser")
        self.resize(1500, 900)

        # Full config including backup flag
        self.config = {
            "models_dir": "",
            "maps_dir": "",
            "recursive": False,
            "edit_apps": {},
            "make_backup": True
        }

        self.current_model_path = ""
        self.current_texture_chunks = {}

        self.is_admin = is_admin()

        self.load_config()
        self.init_ui()
        self.refresh_models()

    # =========================================================================
    # CONFIG
    # =========================================================================

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    self.config = json.load(f)
            except:
                pass

    def save_config(self):
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.config, f, indent=4)

    @property
    def models_dir(self): return self.config.get("models_dir", "")
    @models_dir.setter
    def models_dir(self, v): self.config["models_dir"] = v

    @property
    def maps_dir(self): return self.config.get("maps_dir", "")
    @maps_dir.setter
    def maps_dir(self, v): self.config["maps_dir"] = v

    @property
    def recursive(self): return self.config.get("recursive", False)
    @recursive.setter
    def recursive(self, v): self.config["recursive"] = v

    @property
    def make_backup(self): return self.config.get("make_backup", True)
    @make_backup.setter
    def make_backup(self, v): self.config["make_backup"] = v


    # =========================================================================
    # UI INITIALIZATION
    # =========================================================================

    def init_ui(self):
        main = QVBoxLayout(self)
        top = QHBoxLayout()
        left = QVBoxLayout()
        right = QVBoxLayout()

        # --- Directory controls ---
        self.models_label = QLabel(f"Models Directory: {self.models_dir or '(none)'}")
        btn_models = QPushButton("Browse Models…")
        btn_models.clicked.connect(self.pick_models_dir)

        self.maps_label = QLabel(f"Maps Directory: {self.maps_dir or '(none)'}")
        btn_maps = QPushButton("Browse Maps…")
        btn_maps.clicked.connect(self.pick_maps_dir)

        self.chk_recursive = QCheckBox("Recursive Search")
        self.chk_recursive.setChecked(self.recursive)
        self.chk_recursive.stateChanged.connect(self.on_recursive_change)

        # --- NEW: Create Backup Checkbox ---
        self.chk_backup = QCheckBox("Create Backup (-original) Before Edit/Replace")
        self.chk_backup.setChecked(self.make_backup)
        self.chk_backup.setToolTip("Requires Administrator privileges.")
        self.chk_backup.stateChanged.connect(self.toggle_backup)

        if not self.is_admin:
            self.chk_backup.setDisabled(True)

        left.addWidget(self.models_label)
        left.addWidget(btn_models)
        left.addWidget(self.maps_label)
        left.addWidget(btn_maps)
        left.addWidget(self.chk_recursive)
        left.addWidget(self.chk_backup)
        left.addWidget(QLabel("Models Found:"))

        # --- Models list ---
        self.model_list = QListWidget()
        self.model_list.itemSelectionChanged.connect(self.on_model_selected)
        left.addWidget(self.model_list, 1)

        # --- Right panel ---
        right.addWidget(QLabel("Textures Used:"))

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.grid_frame = QFrame()
        self.grid_layout = QGridLayout(self.grid_frame)
        self.grid_layout.setContentsMargins(10, 10, 10, 10)
        self.grid_layout.setSpacing(12)

        self.scroll.setWidget(self.grid_frame)
        right.addWidget(self.scroll, 1)

        top.addLayout(left, 1)
        top.addLayout(right, 2)

        # Status bar
        self.status = QLabel("Ready.")
        self.status.setStyleSheet("padding:6px; background:#efefef; border-top:1px solid #ccc;")

        main.addLayout(top)
        main.addWidget(self.status)
    # =========================================================================
    # BACKUP TOGGLE HANDLER
    # =========================================================================

    def toggle_backup(self):
        if self.is_admin:
            self.make_backup = self.chk_backup.isChecked()
            self.save_config()


    # =========================================================================
    # DIRECTORY PICKERS
    # =========================================================================

    def pick_models_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select Models Directory")
        if path:
            self.models_dir = path
            self.models_label.setText(f"Models Directory: {path}")
            self.save_config()
            self.refresh_models()

    def pick_maps_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select Maps Directory")
        if path:
            self.maps_dir = path
            self.maps_label.setText(f"Maps Directory: {path}")
            self.save_config()

    def on_recursive_change(self):
        self.recursive = self.chk_recursive.isChecked()
        self.save_config()
        self.refresh_models()


    # =========================================================================
    # SCAN MODELS DIRECTORY (WITH NESTED FOLDERS)
    # =========================================================================

    def refresh_models(self):
        self.model_list.clear()
        if not self.models_dir:
            return

        exts = {".i3d", ".3ds", ".I3D", ".3DS"}

        for root, dirs, files in os.walk(self.models_dir):

            rel = os.path.relpath(root, self.models_dir)

            # ROOT folder
            if rel == ".":
                folder_label = "(root)\\"
                indent = 0
            else:
                rel = rel.replace("/", "\\")
                folder_label = rel + "\\"
                indent = rel.count("\\")

            # Add folder header
            header_text = ("    " * indent) + folder_label
            item = QListWidgetItem(header_text)
            item.setFlags(Qt.NoItemFlags)
            item.setForeground(Qt.gray)
            self.model_list.addItem(item)

            # Add model files under this folder
            for f in sorted(files):
                if os.path.splitext(f)[1] in exts:
                    full = os.path.join(root, f)
                    display_name = ("    " * (indent + 1)) + f
                    it = QListWidgetItem(display_name)
                    it.setData(Qt.UserRole, full)
                    self.model_list.addItem(it)


    # =========================================================================
    # MODEL SELECTED → Load texture list
    # =========================================================================

    def on_model_selected(self):
        items = self.model_list.selectedItems()
        if not items:
            return

        path = items[0].data(Qt.UserRole)
        if not path:
            return  # user clicked a folder line

        self.current_model_path = path

        textures, chunk_info = self.extract_textures_from_model(path)
        self.current_texture_chunks = chunk_info

        texture_paths = self.find_maps(textures)
        self.display_textures(texture_paths)


    # =========================================================================
    # PARSE TEXTURES FROM MODEL (A300 MATERIAL MAP)
    # =========================================================================

    def extract_textures_from_model(self, path):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except:
            return set(), {}

        parser = ChunkParser(data)
        root = parser.parse()

        textures = set()
        chunks = {}

        def walk(node):
            if node.cid == 0xA300:
                payload = data[node.payload_start:node.payload_end]
                name = payload.split(b"\x00")[0].decode("ascii", "ignore").strip()
                if name:
                    textures.add(name)
                    chunks[name] = (node.payload_start, len(payload))
            for c in node.children:
                walk(c)

        walk(root)
        return textures, chunks


    # =========================================================================
    # FIND MAP FILES IN MAPS DIRECTORY
    # =========================================================================

    def find_maps(self, texture_names):
        if not self.maps_dir:
            return []

        found = []
        req = {t.lower() for t in texture_names}

        for root, dirs, files in os.walk(self.maps_dir):
            for f in files:
                if f.lower() in req:
                    found.append(os.path.join(root, f))

        # preserve A300 order
        ordered = []
        for t in texture_names:
            for p in found:
                if os.path.basename(p).lower() == t.lower():
                    ordered.append(p)

        return ordered


    # =========================================================================
    # TEXTURE CARD (RIGHT-CLICK MENU ONLY, NO BUTTONS)
    # =========================================================================

    def build_texture_card(self, path):
        card = QWidget()
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        layout = QVBoxLayout(card)
        layout.setSpacing(4)
        layout.setContentsMargins(4, 4, 4, 4)

        filename = os.path.basename(path)
        pix = QPixmap(path)

        # Preview
        img = QLabel()
        img.setAlignment(Qt.AlignCenter)
        img.original_pixmap = pix if not pix.isNull() else None

        if pix.isNull():
            img.setText(filename)
        else:
            img.setPixmap(
                pix.scaled(100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        layout.addWidget(img)

        # Filename label
        lbl_name = QLabel(filename)
        lbl_name.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_name)

        # Info label
        if pix.isNull():
            info = "0 × 0 | 0 KB"
        else:
            w, h = pix.width(), pix.height()
            kb = os.path.getsize(path) / 1024
            info = f"{w} × {h} | {kb:.0f} KB"

        lbl_info = QLabel(info)
        lbl_info.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_info)

        # Full path
        lbl_path = QLabel(os.path.normpath(path))
        lbl_path.setAlignment(Qt.AlignCenter)
        lbl_path.setWordWrap(True)
        lbl_path.setStyleSheet("font-size:10px; color:#555;")
        layout.addWidget(lbl_path)

        # Right-click context menu
        card.setContextMenuPolicy(Qt.CustomContextMenu)
        card.customContextMenuRequested.connect(
            lambda pos, p=path, w=card: self.open_texture_context_menu(p, w, pos)
        )

        return card


    # =========================================================================
    # DISPLAY TEXTURES (4 ACROSS)
    # =========================================================================

    def display_textures(self, paths):
        # Clear old widgets
        for i in reversed(range(self.grid_layout.count())):
            w = self.grid_layout.itemAt(i).widget()
            if w:
                w.deleteLater()

        col = row = 0
        for p in paths:
            card = self.build_texture_card(p)
            self.grid_layout.addWidget(card, row, col)

            col += 1
            if col >= 4:
                col = 0
                row += 1

        self.resize_texture_previews()


    # =========================================================================
    # RESIZE EVENT → RESPONSIVE THUMBNAILS
    # =========================================================================

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resize_texture_previews()

    def resize_texture_previews(self):
        if self.grid_layout.count() == 0:
            return

        scroll_width = self.scroll.viewport().width()
        col_width = scroll_width // 4  # max 25% width

        for i in range(self.grid_layout.count()):
            card = self.grid_layout.itemAt(i).widget()
            if not card:
                continue

            img_label = card.layout().itemAt(0).widget()
            pix = getattr(img_label, "original_pixmap", None)

            if pix and not pix.isNull():
                scaled = pix.scaled(
                    col_width - 20,
                    col_width - 20,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                img_label.setPixmap(scaled)
    # =========================================================================
    # RIGHT-CLICK CONTEXT MENU
    # =========================================================================

    def open_texture_context_menu(self, path, widget, pos):
        menu = QMenu(widget)

        act_edit = menu.addAction("Open / Edit")
        act_replace = menu.addAction("Replace Texture…")
        menu.addSeparator()
        act_copy = menu.addAction("Copy Path")
        act_explorer = menu.addAction("Show in Explorer")

        action = menu.exec_(widget.mapToGlobal(pos))
        if not action:
            return

        if action == act_edit:
            self.edit_texture(path)

        elif action == act_replace:
            self.replace_texture(path)

        elif action == act_copy:
            self.copy_path(path)

        elif action == act_explorer:
            self.show_in_explorer(path)


    # =========================================================================
    # COPY PATH TO CLIPBOARD
    # =========================================================================

    def copy_path(self, path):
        clean = os.path.normpath(path)
        QApplication.clipboard().setText(clean)
        self.status.setText(f"Copied path: {clean}")


    # =========================================================================
    # SHOW FILE IN WINDOWS EXPLORER
    # =========================================================================

    def show_in_explorer(self, path):
        clean = os.path.normpath(path)
        try:
            subprocess.Popen(["explorer", "/select,", clean])
            self.status.setText(f"Opened Explorer for: {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not open Explorer:\n{e}")


    # =========================================================================
    # INTERNAL: CREATE BACKUP IF ENABLED
    # =========================================================================

    def maybe_backup(self, path):
        """
        Creates a -original backup if:
         • user has enabled backups
         • running as admin
         • backup does not already exist
        """
        if not self.is_admin:
            return  # backup disabled because admin required

        if not self.make_backup:
            return

        base, ext = os.path.splitext(path)
        backup = base + "-original" + ext

        if os.path.exists(backup):
            return  # already backed up

        try:
            shutil.copy2(path, backup)
        except Exception as e:
            QMessageBox.warning(self, "Backup Failed",
                                f"Could not create backup:\n{e}")


    # =========================================================================
    # OPEN / EDIT TEXTURE IN USER-SELECTED APP (WITH BACKUP OPTION)
    # =========================================================================

    def edit_texture(self, path):
        if not os.path.isfile(path):
            QMessageBox.warning(self, "Error", f"File not found:\n{path}")
            return

        # create backup if enabled
        self.maybe_backup(path)

        ext = os.path.splitext(path)[1].lower()
        app_map = self.config.get("edit_apps", {})
        editor = app_map.get(ext)

        # first time selecting editor
        if editor is None:
            QMessageBox.information(
                self,
                "Choose Editor",
                f"Select the application to edit *{ext}* files."
            )

            exe, _ = QFileDialog.getOpenFileName(
                self,
                "Choose Editor",
                "C:\\Program Files",
                "Applications (*.exe)"
            )

            if exe:
                app_map[ext] = exe
                self.config["edit_apps"] = app_map
                self.save_config()
                editor = exe

        # launch editor (fallback to system default)
        try:
            if editor:
                subprocess.Popen([editor, path])
            else:
                os.startfile(path)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to open editor:\n{e}")

        self.status.setText(f"Editing {os.path.basename(path)}")


    # =========================================================================
    # REPLACE TEXTURE (OVERWRITE + OPTIONAL BACKUP)
    # =========================================================================

    def replace_texture(self, old_path):
        if not os.path.isfile(old_path):
            QMessageBox.warning(self, "Error", f"File not found:\n{old_path}")
            return

        old_name = os.path.basename(old_path)

        # select replacement
        new_file, _ = QFileDialog.getOpenFileName(
            self,
            f"Replace {old_name}",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tga *.dds *.pcx);;All Files (*)"
        )
        if not new_file:
            return

        # backup if enabled
        self.maybe_backup(old_path)

        # overwrite
        try:
            shutil.copy2(new_file, old_path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to replace texture:\n{e}")
            return

        self.status.setText(f"Replaced texture: {old_name}")
        self.on_model_selected()
# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main():
    app = QApplication(sys.argv)
    window = ModelTextureBrowser()
    window.show()
    exit_code = app.exec_()
    window.save_config()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
