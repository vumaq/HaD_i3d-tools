#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import shutil
import struct
import subprocess


from dataclasses import dataclass, field
from typing import List, Optional

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QListWidget, QListWidgetItem, QFileDialog, QCheckBox,
    QScrollArea, QGridLayout, QFrame, QMessageBox, QSizePolicy
)
from PyQt5.QtGui import QPixmap
from PyQt5.QtCore import Qt

CONFIG_FILE = "model_texture_browser_config.json"


# ================================================================
# 3DS / I3D Chunk structures
# ================================================================

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

        if cid == 0x4000:  # named object = string then subchunks
            pos = pstart
            while pos < pend and d[pos] != 0:
                pos += 1
            if pos < pend: pos += 1
            while pos + 6 <= pend:
                ch, nxt = self.parse_chunk(pos, pend, node)
                if not ch or nxt <= pos: break
                pos = nxt

        elif cid in (
            0x4D4D, 0x3D3D, 0x4100, 0xAFFF, 0xA200,
            0xB000, 0xB002, 0xB003, 0xB004,
            0xB005, 0xB006, 0xB007, 0xB010
        ):
            pos = pstart
            while pos + 6 <= pend:
                ch, nxt = self.parse_chunk(pos, pend, node)
                if not ch or nxt <= pos: break
                pos = nxt

        return node, off + length

    def parse(self):
        root = ChunkNode(0xFFFF, 0, len(self.data))
        pos = 0
        end = len(self.data)

        while pos + 6 <= end:
            ch, nxt = self.parse_chunk(pos, end, root)
            if not ch or nxt <= pos: break
            pos = nxt

        return root
# ================================================================
# MODEL / TEXTURE BROWSER WINDOW
# ================================================================

class ModelTextureBrowser(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Model / Texture Browser")
        self.resize(1500, 900)

        # main config dict
        self.config = {
            "models_dir": "",
            "maps_dir": "",
            "recursive": False,
            "edit_apps": {}
        }

        # runtime state
        self.current_model_path = ""
        self.current_texture_chunks = {}   # { texture_name : (offset, original_length) }

        self.load_config()
        self.init_ui()
        self.refresh_models()

    # ------------------------------------------------------------
    # LOAD + SAVE CONFIG
    # ------------------------------------------------------------
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

    # helper props
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

    # ------------------------------------------------------------
    # UI SETUP
    # ------------------------------------------------------------
    def init_ui(self):
        main = QVBoxLayout(self)
        top = QHBoxLayout()
        left = QVBoxLayout()
        right = QVBoxLayout()

        # -------------- LEFT PANEL --------------
        self.models_label = QLabel(f"Models Directory: {self.models_dir or '(none)'}")
        btn_models = QPushButton("Browse Models…")
        btn_models.clicked.connect(self.pick_models_dir)

        self.maps_label = QLabel(f"Maps Directory: {self.maps_dir or '(none)'}")
        btn_maps = QPushButton("Browse Maps…")
        btn_maps.clicked.connect(self.pick_maps_dir)

        self.chk_recursive = QCheckBox("Recursive Search")
        self.chk_recursive.setChecked(self.recursive)
        self.chk_recursive.stateChanged.connect(self.on_recursive_change)

        left.addWidget(self.models_label)
        left.addWidget(btn_models)
        left.addWidget(self.maps_label)
        left.addWidget(btn_maps)
        left.addWidget(self.chk_recursive)
        left.addWidget(QLabel("Models Found:"))

        self.model_list = QListWidget()
        self.model_list.itemSelectionChanged.connect(self.on_model_selected)
        left.addWidget(self.model_list, 1)

        # -------------- RIGHT PANEL (Textures) --------------
        right.addWidget(QLabel("Textures Used:"))

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.grid_frame = QFrame()
        self.grid_layout = QGridLayout(self.grid_frame)

        self.grid_layout.setContentsMargins(10, 10, 10, 10)


        self.scroll.setWidget(self.grid_frame)
        right.addWidget(self.scroll, 1)

        # layout packs
        top.addLayout(left, 1)
        top.addLayout(right, 2)

        # status bar
        self.status = QLabel("Ready.")
        self.status.setStyleSheet(
            "padding: 6px; background: #efefef; border-top: 1px solid #ccc;"
        )

        main.addLayout(top)
        main.addWidget(self.status)

    # ------------------------------------------------------------
    # PICK DIRECTORIES
    # ------------------------------------------------------------
    def pick_models_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select Models Directory")
        if path:
            self.models_dir = path
            self.models_label.setText(f"Models Directory: {path}")
            self.refresh_models()
            self.save_config()

    def pick_maps_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select Maps Directory")
        if path:
            self.maps_dir = path
            self.maps_label.setText(f"Maps Directory: {path}")
            self.save_config()

    def on_recursive_change(self):
        self.recursive = self.chk_recursive.isChecked()
        self.refresh_models()
        self.save_config()

    # ------------------------------------------------------------
    # SCAN MODELS DIRECTORY
    # ------------------------------------------------------------
    def refresh_models(self):
        self.model_list.clear()
        if not self.models_dir:
            return

        exts = {".i3d", ".I3D", ".3ds", ".3DS"}

        if self.recursive:
            walker = os.walk(self.models_dir)
        else:
            walker = [(self.models_dir, [], os.listdir(self.models_dir))]

        for root, dirs, files in walker:
            for f in files:
                if os.path.splitext(f)[1] in exts:
                    item = QListWidgetItem(f)
                    item.setData(Qt.UserRole, os.path.join(root, f))
                    self.model_list.addItem(item)

    # ------------------------------------------------------------
    # MODEL SELECTED
    # ------------------------------------------------------------
    def on_model_selected(self):
        items = self.model_list.selectedItems()
        if not items:
            return

        self.current_model_path = items[0].data(Qt.UserRole)

        textures, chunk_info = self.extract_textures_from_model(self.current_model_path)
        self.current_texture_chunks = chunk_info

        paths = self.find_maps(textures)
        self.display_textures(paths)

    # ------------------------------------------------------------
    # EXTRACT TEXTURES (only A300 Material Map File)
    # ------------------------------------------------------------
    def extract_textures_from_model(self, path):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except:
            return set(), {}

        parser = ChunkParser(data)
        root = parser.parse()

        textures = set()
        chunks = {}   # name → (offset, byte_length)

        def walk(node):
            if node.cid == 0xA300:
                payload = data[node.payload_start:node.payload_end]
                s = payload.split(b"\x00")[0]

                try:
                    name = s.decode("ascii", "replace").strip()
                except:
                    name = ""

                if name:
                    textures.add(name)
                    chunks[name] = (node.payload_start, len(payload))

            for c in node.children:
                walk(c)

        walk(root)
        return textures, chunks

    # ------------------------------------------------------------
    # LOCATE TEXTURES IN MAP FOLDER
    # ------------------------------------------------------------
    def find_maps(self, texture_names):
        if not self.maps_dir:
            return []

        found = []
        lower_req = {t.lower() for t in texture_names}

        if self.recursive:
            walker = os.walk(self.maps_dir)
        else:
            walker = [(self.maps_dir, [], os.listdir(self.maps_dir))]

        for root, dirs, files in walker:
            for f in files:
                if f.lower() in lower_req:
                    found.append(os.path.join(root, f))

        # sort matching the order of the model’s A300 entries
        ordered = []
        for t in texture_names:
            for p in found:
                if os.path.basename(p).lower() == t.lower():
                    ordered.append(p)

        return ordered

    # ------------------------------------------------------------
    # BUILD TEXTURE CARD UI
    # ------------------------------------------------------------
    def build_texture_card(self, path):
        card = QWidget()
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        layout = QVBoxLayout(card)
        layout.setSpacing(4)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSizeConstraint(QVBoxLayout.SetMinimumSize)

        filename = os.path.basename(path)
        pix = QPixmap(path)

        # ----- Preview Image -----
        img_label = QLabel()
        img_label.setAlignment(Qt.AlignCenter)
        img_label.original_pixmap = pix if not pix.isNull() else None

        if pix.isNull():
            img_label.setText(filename)
        else:
            img_label.setPixmap(pix.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))

        layout.addWidget(img_label)

        # ----- Filename -----
        lbl_name = QLabel(filename)
        lbl_name.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_name)

        # ----- Dimensions + size -----
        if pix.isNull():
            info = "0 × 0 | 0 KB"
        else:
            w, h = pix.width(), pix.height()
            kb = os.path.getsize(path) / 1024
            info = f"{w} × {h}   |   {kb:.0f} KB"

        lbl_info = QLabel(info)
        lbl_info.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_info)

        # ----- FULL FILE PATH -----
        lbl_path = QLabel(os.path.normpath(path))
        lbl_path.setAlignment(Qt.AlignCenter)
        lbl_path.setStyleSheet("font-size:10px; color: #555;")
        lbl_path.setWordWrap(True)
        layout.addWidget(lbl_path)


        # ----- Buttons row -----
        btn_row = QWidget()
        btns = QHBoxLayout(btn_row)
        btns.setContentsMargins(0, 0, 0, 0)
        btns.setSpacing(8)

        # EDIT button ------------
        btn_edit = QPushButton("Edit")
        btn_edit.setStyleSheet("padding:4px;")
        btn_edit.clicked.connect(lambda _, p=path: self.edit_texture(p))
        btns.addWidget(btn_edit)

        # REPLACE button ---------
        btn_replace = QPushButton("Replace")
        btn_replace.setStyleSheet("padding:4px;")
        btn_replace.clicked.connect(lambda _, p=path: self.replace_texture(p))
        btns.addWidget(btn_replace)

        layout.addWidget(btn_row)

        return card

    # ------------------------------------------------------------
    # DISPLAY 4-COLUMN TEXTURE GRID
    # ------------------------------------------------------------
    def display_textures(self, paths):

        # remove existing cards
        for i in reversed(range(self.grid_layout.count())):
            w = self.grid_layout.itemAt(i).widget()
            if w:
                w.deleteLater()

        col_count = 4
        row = col = 0

        for p in paths:
            card = self.build_texture_card(p)
            card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

            self.grid_layout.addWidget(card, row, col)

            col += 1
            if col >= col_count:
                col = 0
                row += 1

        self.grid_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.resize_texture_previews()

    # ------------------------------------------------------------
    # WINDOW RESIZE → RESCALE THUMBNAILS
    # ------------------------------------------------------------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resize_texture_previews()

    def resize_texture_previews(self):
        if self.grid_layout.count() == 0:
            return

        # Correct: width of the visible scroll viewport
        scroll_width = self.scroll.viewport().width()

        col_width = scroll_width // 4  # 4 columns

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
    # ------------------------------------------------------------
    # EDIT TEXTURE — Open With (saved app per extension)
    # ------------------------------------------------------------
    def edit_texture(self, path):
        ext = os.path.splitext(path)[1].lower()

        # load saved app preferences
        app_map = self.config.get("edit_apps", {})
        edit_app = app_map.get(ext)

        # If app known → open using subprocess
        if edit_app:
            try:
                subprocess.Popen([edit_app, path])
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to open editor:\n{e}")
            return

        # Ask user for an editor
        QMessageBox.information(
            self,
            "Choose Editor",
            f"Select the program to use for editing {ext} files."
        )

        exe_path, _ = QFileDialog.getOpenFileName(
            self,
            f"Choose Editor for {ext} textures",
            "C:\\Program Files",
            "Applications (*.exe)"
        )

        if not exe_path:
            # fallback to system default
            os.startfile(path)
            return

        # Save the chosen app to config
        app_map[ext] = exe_path
        self.config["edit_apps"] = app_map
        self.save_config()

        # Open the editor using subprocess — guaranteed to work
        try:
            subprocess.Popen([exe_path, path])
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to open:\n{e}")


    # ------------------------------------------------------------
    # REPLACE TEXTURE — update file + maps folder
    # ------------------------------------------------------------
    def replace_texture(self, old_texture_path):
        if not self.current_model_path:
            return

        old_name = os.path.basename(old_texture_path)

        # Choose new image
        file, _ = QFileDialog.getOpenFileName(
            self,
            f"Replace {old_name}",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tga *.dds *.pcx);;All Files (*)"
        )
        if not file:
            return

        new_name = os.path.basename(file)

        # Confirm chunk exists
        if old_name not in self.current_texture_chunks:
            QMessageBox.warning(self, "Error", "Texture not found in model (A300 chunk).")
            return

        offset, original_len = self.current_texture_chunks[old_name]
        target_len = original_len - 1  # remove null terminator

        # Build filename EXACT length
        base, ext = os.path.splitext(new_name)
        filtered = (base + ext)[:target_len]
        filtered = filtered.ljust(target_len, " ")

        new_bytes = filtered.encode("ascii", "replace") + b"\x00"

        # Write to the model file
        try:
            with open(self.current_model_path, "r+b") as f:
                f.seek(offset)
                f.write(new_bytes)
        except Exception as e:
            QMessageBox.critical(self, "I/O Error", str(e))
            return

        # -----------------------------------------
        # Copy texture → maps folder (preserve structure if given)
        # -----------------------------------------
        rel_path = old_name
        if "/" in old_name or "\\" in old_name:
            rel_path = old_name.replace("/", os.sep).replace("\\", os.sep)

        dest = os.path.join(self.maps_dir, rel_path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        try:
            shutil.copy2(file, dest)
        except Exception as e:
            QMessageBox.warning(self, "Copy Error", str(e))

        # Update status
        self.status.setText(f"Replaced '{old_name}' → '{filtered.strip()}'")

        # Refresh previews
        self.on_model_selected()


# ================================================================
# MAIN ENTRY POINT
# ================================================================

def main():
    app = QApplication(sys.argv)
    w = ModelTextureBrowser()
    w.show()
    ret = app.exec_()
    w.save_config()
    sys.exit(ret)


if __name__ == "__main__":
    main()
