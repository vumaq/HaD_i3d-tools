#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
3DS / I3D Chunk Explorer — Cleaned Version (Option A1)

Rules:
------
• Keep all official 3DS chunk names + decoders.
• Keep only I3D chunk 0x4200 (MAP_CHANNEL).
• All other decoders and I3D logic removed.
• Unknown chunks still detected and shown.
• Cleaned comments, reduced separators, no duplicate decoders.
"""

import sys
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QTreeWidget,
    QTreeWidgetItem, QTextEdit, QFileDialog, QAction, QSplitter, QStatusBar,
    QDialog, QPlainTextEdit
)
from PyQt5.QtGui import QFont, QColor, QBrush
from PyQt5.QtCore import Qt


# ------------------------------------------------------------
# Friendly naming (I3D)
# ------------------------------------------------------------

def friendly_name(name: str) -> str:
    """Convert CHUNK_XYZ style to 'Xyz' while preserving acronyms."""
    name = name.strip()
    for p in ("CHUNK_", "MAT_", "NURBS_"):
        if name.startswith(p):
            name = name[len(p):]

    parts = name.split("_")
    out = []
    for p in parts:
        if p.isupper():
            out.append(p)
        else:
            out.append(p.capitalize())
    return " ".join(out)


# ------------------------------------------------------------
# Chunk name tables
# ------------------------------------------------------------

CHUNK_NAMES_3DS = {
    0x4D4D: "Main",
    0x3D3D: "Editor",
    0x0000: "Null",
    0x0002: "M3D Version",

    0x0010: "RGB Float",
    0x0011: "RGB 24",
    0x0012: "Linear Color 24",
    0x0013: "Linear Color Float",
    0x0030: "Int Percentage",
    0x0031: "Float Percentage",
    0x0100: "Master Scale",

    # Named object
    0x4000: "Named Object",
    0x4010: "Object Hidden",
    0x4016: "Object Frozen",

    # TriMesh
    0x4100: "Tri Mesh",
    0x4110: "Vertex List",
    0x4111: "Vertex Selection",
    0x4120: "Face List",
    0x4130: "Face Material",
    0x4140: "Mapping Coordinates",
    0x4150: "Smoothing Group List",
    0x4160: "Transform Matrix",
    0x4165: "Mesh Color",

    # Lights (3DS)
    0x4600: "Light",
    0x4610: "Spotlight",
    0x4620: "Light Off",
    0x4625: "Spotlight Range",

    # Cameras
    0x4700: "Camera",
    0x4720: "Camera Ranges",

    # Material
    0xAFFF: "Material",
    0xA000: "Material Name",
    0xA010: "Material Ambient",
    0xA020: "Material Diffuse",
    0xA030: "Material Specular",
    0xA040: "Material Shininess",
    0xA041: "Material Shin Strength",
    0xA050: "Material Transparency",

    0xA081: "Material Two Sided",
    0xA084: "Material Self Illumination Percent",
    0xA085: "Material Wire",
    0xA087: "Material Wire Size",
    0xA08A: "Material XPFall-In",
    0xA08C: "Material Phong Softness",

    0xA100: "Material Shading",
    0xA200: "Material Texture",
    0xA204: "Material Specular Map",
    0xA210: "Material Opacity Map",
    0xA220: "Material Reflection Map",
    0xA230: "Material Bump Map",
    0xA300: "Material Map File",
    0xA351: "Material Map Params",
    0xA353: "Material Map TexBlur",

    # Keyframer
    0xB000: "Keyframer",
    0xB002: "Object Node Tag",
    0xB003: "Camera Node Tag",
    0xB004: "Target Node Tag",
    0xB005: "Light Node Tag",
    0xB006: "Light Target Node Tag",
    0xB007: "Spotlight Node Tag",

    0xB008: "Frame Segment",
    0xB009: "Current Time",
    0xB00A: "Keyframe Header",
    0xB00B: "Object Info",
    0xB00E: "Bone Node",
    0xB010: "Node Header",
    0xB011: "Instance Name",
    0xB013: "Pivot",
    0xB014: "Bounding Box",
    0xB020: "Position Track Tag",
    0xB021: "Rotation Track Tag",
    0xB022: "Scale Track Tag",
    0xB023: "FOV Track Tag",
    0xB024: "Roll Track Tag",
    0xB025: "Color Track Tag",
    0xB027: "Hot Spot Track Tag",
    0xB028: "Fall Track Tag",
    0xB029: "Hide Track Tag",
    0xB02A: "Note Track Tag",
    0xB030: "Node ID",
}

# Only I3D chunk we keep
CHUNK_NAMES_I3D = {
    0x4200: "Map Channel",
}

# Combined lookup
CHUNK_NAMES = {**CHUNK_NAMES_3DS, **CHUNK_NAMES_I3D}


# ------------------------------------------------------------
# Chunk node structure
# ------------------------------------------------------------

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

    def add_child(self, c): 
        c.parent = self
        self.children.append(c)


# ------------------------------------------------------------
# Chunk parser
# ------------------------------------------------------------

class ChunkParser:
    def __init__(self, data): self.data = data

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

        pstart, pend = node.payload_start, node.payload_end

        # Named object: read name, then subchunks
        if cid == 0x4000:
            pos = pstart
            while pos < pend and d[pos] != 0:
                pos += 1
            if pos < pend: pos += 1
            while pos + 6 <= pend:
                ch, next_pos = self.parse_chunk(pos, pend, node)
                if not ch or next_pos <= pos: break
                pos = next_pos

        # Container chunks
        elif cid in (
            0x4D4D,  # MAIN
            0x3D3D,  # EDITOR

            # GEOMETRY BLOCKS
            0x4100,  # TRI MESH

            # MATERIAL BLOCKS
            0xAFFF,  # MATERIAL
            0xA200,  # TEXTURE BLOCK

            # KEYFRAMER ROOT
            0xB000,  # KEYFRAMER

            # KEYFRAMER NODE TAGS
            0xB002,  # OBJECT NODE
            0xB003,  # CAMERA NODE
            0xB004,  # TARGET NODE
            0xB005,  # LIGHT NODE
            0xB006,  # LIGHT TARGET NODE
            0xB007,  # SPOTLIGHT NODE

            # NODE HEADER (ALWAYS A CONTAINER)
            0xB010,  # NODE HEADER
        ):
            pos = pstart
            while pos + 6 <= pend:
                ch, next_pos = self.parse_chunk(pos, pend, node)
                if not ch or next_pos <= pos: break
                pos = next_pos

        return node, off + length

    def parse(self):
        root = ChunkNode(0xFFFF, 0, len(self.data))
        pos, end = 0, len(self.data)

        while pos + 6 <= end:
            ch, next_pos = self.parse_chunk(pos, end, root)
            if not ch or next_pos <= pos: break
            pos = next_pos

        return root

# ============================================================================
# ChunkDecoder (interprets common chunk payloads)
# ============================================================================

class ChunkDecoder:
    def u8(self, b, o=0): return b[o]
    def u16(self, b, o=0): return int.from_bytes(b[o:o+2], "little")
    def u32(self, b, o=0): return int.from_bytes(b[o:o+4], "little")
    def f32(self, b, o=0): return struct.unpack("<f", b[o:o+4])[0]

    # ===================================================================
    # MAIN DECODER ENTRY
    # ===================================================================
    def decode(self, node, data, full_data):
        cid = node.cid

        # ===================================================================
        # BASIC 3DS CHUNKS
        # ===================================================================
        if cid == 0x0000:
            return "Null Chunk"

        if cid == 0x0002 and len(data) >= 2:
            return f"3DS Version: {self.u16(data,0)}"

        if cid == 0x0100 and len(data) >= 4:
            return f"Master Scale: {self.f32(data,0)}"

        # ===================================================================
        # GENERIC COLOR & PERCENTAGE BLOCKS
        # ===================================================================
        if cid == 0x0010 and len(data) >= 12:
            return f"RGB Float: ({self.f32(data,0):.4f}, {self.f32(data,4):.4f}, {self.f32(data,8):.4f})"

        if cid == 0x0011 and len(data) >= 3:
            return f"RGB 24: ({data[0]}, {data[1]}, {data[2]})"

        if cid == 0x0012 and len(data) >= 3:
            return f"Linear RGB 24: ({data[0]}, {data[1]}, {data[2]})"

        if cid == 0x0013 and len(data) >= 12:
            return f"Linear RGB Float: ({self.f32(data,0):.4f}, {self.f32(data,4):.4f}, {self.f32(data,8):.4f})"

        if cid == 0x0030 and len(data) >= 2:
            return f"Int Percentage: {self.u16(data,0)}%"

        if cid == 0x0031 and len(data) >= 4:
            return f"Float Percentage: {self.f32(data,0)*100:.2f}%"

        # ===================================================================
        # A000 — MATERIAL NAME
        # ===================================================================
        if cid == 0xA000:
            s = data.split(b"\x00")[0].decode("ascii", "replace")
            return f"Material Name: \"{s}\""

        # ===================================================================
        # MATERIAL COLOR BLOCKS (A010/A020/A030)
        # ===================================================================
        if cid in (0xA010, 0xA020, 0xA030):
            names = {
                0xA010: "Ambient Color",
                0xA020: "Diffuse Color",
                0xA030: "Specular Color",
            }

            # Try to decode actual subchunks
            for child in node.children:
                cp = full_data[child.payload_start:child.payload_end]

                if child.cid == 0x0010 and len(cp)>=12:
                    return f"{names[cid]}: ({self.f32(cp,0):.4f}, {self.f32(cp,4):.4f}, {self.f32(cp,8):.4f})"

                if child.cid == 0x0011 and len(cp)>=3:
                    return f"{names[cid]}: ({cp[0]}, {cp[1]}, {cp[2]})"

                if child.cid == 0x0012 and len(cp)>=3:
                    return f"{names[cid]} (Linear): ({cp[0]}, {cp[1]}, {cp[2]})"

                if child.cid == 0x0013 and len(cp)>=12:
                    return f"{names[cid]} (Linear): ({self.f32(cp,0):.4f}, {self.f32(cp,4):.4f}, {self.f32(cp,8):.4f})"

            # Try fallback in parent
            for sibling in node.parent.children:
                if sibling is node:
                    continue
                sp = full_data[sibling.payload_start:sibling.payload_end]
                if sibling.cid == 0x0011 and len(sp)>=3:
                    return f"{names[cid]} (from parent): ({sp[0]}, {sp[1]}, {sp[2]})"
                if sibling.cid == 0x0010 and len(sp)>=12:
                    return f"{names[cid]} (from parent): ({self.f32(sp,0):.4f}, {self.f32(sp,4):.4f}, {self.f32(sp,8):.4f})"

            # FINAL FALLBACK WITH DEFAULT
            return f"{names[cid]}: (not set) [default: (1.0, 1.0, 1.0)]"

        # ===================================================================
        # MATERIAL NUMERIC BLOCKS (with default display)
        # ===================================================================

        # A040 – Shininess
        if cid == 0xA040:
            for ch in node.children:
                cp = full_data[ch.payload_start:ch.payload_end]
                if ch.cid == 0x0030 and len(cp)>=2:
                    return f"Shininess: {self.u16(cp,0)}%"
                if ch.cid == 0x0031 and len(cp)>=4:
                    return f"Shininess: {self.f32(cp,0)*100:.2f}%"
            return "Shininess: (not set) [default: 0%]"

        # A041 – Shin Strength
        if cid == 0xA041:
            for ch in node.children:
                cp = full_data[ch.payload_start:ch.payload_end]
                if ch.cid == 0x0030 and len(cp)>=2:
                    return f"Shin Strength: {self.u16(cp,0)}%"
                if ch.cid == 0x0031 and len(cp)>=4:
                    return f"Shin Strength: {self.f32(cp,0)*100:.2f}%"
            return "Shin Strength: (not set) [default: 0%]"

        # A050 – Transparency
        if cid == 0xA050:
            for ch in node.children:
                cp = full_data[ch.payload_start:ch.payload_end]
                if ch.cid == 0x0030 and len(cp)>=2:
                    return f"Transparency: {self.u16(cp,0)}%"
                if ch.cid == 0x0031 and len(cp)>=4:
                    return f"Transparency: {self.f32(cp,0)*100:.2f}%"
            return "Transparency: (not set) [default: 0%]"

        # A084 – Self-Illumination
        if cid == 0xA084:
            for ch in node.children:
                cp = full_data[ch.payload_start:ch.payload_end]
                if ch.cid == 0x0030 and len(cp)>=2:
                    return f"Self Illumination: {self.u16(cp,0)}%"
                if ch.cid == 0x0031 and len(cp)>=4:
                    return f"Self Illumination: {self.f32(cp,0)*100:.2f}%"
            return "Self Illumination: (not set) [default: 0%]"

        # A087 – Wire Size
        if cid == 0xA087:
            if len(data) >= 4:
                return f"Wire Size: {self.f32(data,0):.4f}"
            return "Wire Size: (not set) [default: 1.0]"

        # A100 – Shading Type
        if cid == 0xA100 and len(data)>=2:
            mode = self.u16(data,0)
            names = {
                0: "Wireframe",
                1: "Flat",
                2: "Gouraud",
                3: "Phong",
                4: "Metal",
            }
            return f"Shading: {names.get(mode, f'Unknown ({mode})')}"

        # ===================================================================
        # GEOMETRY
        # ===================================================================
        if cid == 0x4110:
            if len(data)<2: return "Invalid Vertex List"
            count = self.u16(data,0)
            out=[f"Vertex Count: {count}"]
            off=2
            for i in range(min(count,20)):
                if off+12>len(data): break
                out.append(
                    f"[{i}] ({self.f32(data,off):.4f}, "
                    f"{self.f32(data,off+4):.4f}, "
                    f"{self.f32(data,off+8):.4f})"
                )
                off+=12
            return "\n".join(out)

        if cid == 0x4120:
            if len(data)<2: return "Invalid Face List"
            count=self.u16(data,0)
            out=[f"Face Count: {count}"]
            off=2
            for i in range(min(count,20)):
                a=self.u16(data,off)
                b=self.u16(data,off+2)
                c=self.u16(data,off+4)
                fl=self.u16(data,off+6)
                out.append(f"[{i}] ({a}, {b}, {c}) flags=0x{fl:04X}")
                off+=8
            return "\n".join(out)

        if cid == 0x4140:
            if len(data)<2: return "Invalid UV List"
            count=self.u16(data,0)
            out=[f"UV Count: {count}"]
            off=2
            for i in range(min(count,20)):
                u=self.f32(data,off)
                v=self.f32(data,off+4)
                out.append(f"[{i}] U={u:.4f}, V={v:.4f}")
                off+=8
            return "\n".join(out)

        if cid == 0x4160 and len(data)>=48:
            m = struct.unpack("<12f", data[:48])
            return (
                "Transform Matrix (3x4):\n"
                f"{m[0]:.4f} {m[1]:.4f} {m[2]:.4f} {m[3]:.4f}\n"
                f"{m[4]:.4f} {m[5]:.4f} {m[6]:.4f} {m[7]:.4f}\n"
                f"{m[8]:.4f} {m[9]:.4f} {m[10]:.4f} {m[11]:.4f}"
            )

        # ===================================================================
        # MATERIAL MAP BLOCKS
        # ===================================================================
        if cid in (0xA200,0xA204,0xA210,0xA220,0xA230):
            names = {
                0xA200:"Diffuse Texture Map",
                0xA204:"Specular Map",
                0xA210:"Opacity Map",
                0xA220:"Reflection Map",
                0xA230:"Bump Map",
            }

            out=[names[cid]]

            for child in node.children:
                cp = full_data[child.payload_start:child.payload_end]
                ccid = child.cid

                if ccid == 0xA300:
                    s = cp.split(b"\x00")[0].decode("ascii","replace")
                    out.append(f"  File: \"{s}\"")

                elif ccid == 0x0030 and len(cp)>=2:
                    out.append(f"  Percent (int): {self.u16(cp,0)}%")

                elif ccid == 0x0031 and len(cp)>=4:
                    out.append(f"  Percent (float): {self.f32(cp,0)*100:.2f}%")

                elif ccid == 0xA351:
                    if len(cp)<32:
                        out.append(f"  Map Params (short: {len(cp)} bytes)")
                    else:
                        us=self.f32(cp,0); vs=self.f32(cp,4)
                        uo=self.f32(cp,8); vo=self.f32(cp,12)
                        rot=self.f32(cp,16)
                        ut=self.f32(cp,20); vt=self.f32(cp,24)
                        out.append("  Map Params:")
                        out.append(f"    Scale=({us:.4f}, {vs:.4f})")
                        out.append(f"    Offset=({uo:.4f}, {vo:.4f})")
                        out.append(f"    Rotation={rot:.4f}°")
                        out.append(f"    Tiling=({ut:.4f}, {vt:.4f})")

                elif ccid == 0xA353 and len(cp)>=4:
                    out.append(f"  Blur: {self.f32(cp,0):.4f}")

                else:
                    out.append(f"  Subchunk 0x{ccid:04X} ({len(cp)} bytes)")

            return "\n".join(out)

        # ===================================================================
        # STANDALONE MATERIAL PARAM BLOCKS
        # ===================================================================
        if cid == 0xA300:
            s = data.split(b"\x00")[0].decode("ascii","replace")
            return f"Texture File: \"{s}\""

        if cid == 0xA351:
            if len(data)<32:
                return f"Map Params (short/empty: {len(data)} bytes)"
            us=self.f32(data,0); vs=self.f32(data,4)
            uo=self.f32(data,8); vo=self.f32(data,12)
            rot=self.f32(data,16)
            ut=self.f32(data,20); vt=self.f32(data,24)
            return (
                "Map Params:\n"
                f"  Scale=({us:.4f}, {vs:.4f})\n"
                f"  Offset=({uo:.4f}, {vo:.4f})\n"
                f"  Rotation={rot:.4f}°\n"
                f"  Tiling=({ut:.4f}, {vt:.4f})"
            )

        if cid == 0xA353 and len(data)>=4:
            return f"Blur: {self.f32(data,0):.4f}"

        # ===================================================================
        # KEYFRAMER BASIC BLOCKS
        # ===================================================================
        if cid == 0xB002:
            return f"Object Node Tag (payload {len(data)} bytes)"

        if cid == 0xB030 and len(data)>=2:
            return f"Node ID: {self.u16(data,0)}"

        # ===================================================================
        # I3D EXTENSION: MAP_CHANNEL
        # ===================================================================
        if cid == 0x4200:
            if len(data)<6: return "Invalid MAP_CHANNEL"
            chan=self.u32(data,0)
            count=self.u16(data,4)
            out=[f"Channel Index: {chan}", f"UV Count: {count}"]
            off=6

            for i in range(min(count,10)):
                if off+8 > len(data): break
                u=self.f32(data,off); v=self.f32(data,off+4)
                out.append(f"[{i}] U={u:.4f}, V={v:.4f}")
                off+=8

            if off+2 <= len(data):
                fcount=self.u16(data,off)
                out.append(f"UV Face Count: {fcount}")
                off+=2

                for i in range(min(fcount,10)):
                    if off+6 > len(data): break
                    a=self.u16(data,off)
                    b=self.u16(data,off+2)
                    c=self.u16(data,off+4)
                    out.append(f"[{i}] {a}, {b}, {c}")
                    off+=6

            return "\n".join(out)

        # ===================================================================
        # FALLBACK
        # ===================================================================
        return f"No decoder for 0x{cid:04X} (payload {len(data)} bytes)"

# ------------------------------------------------------------
# Unknown chunk heuristics
# ------------------------------------------------------------

unknown_chunks = {}

def looks_ascii(data):
    if not data: return False
    good = sum(1 for b in data if 32 <= b <= 126 or b in (9,10,13))
    return good / len(data) > 0.85

def guess_payload_type(data):
    if not data: return "Empty"
    if looks_ascii(data): return "ASCII"
    if len(data) % 4 == 0:
        try: struct.unpack("<f", data[:4]); return "Float array"
        except: pass
    if len(data) % 2 == 0: return "UInt16 array"
    return "Binary"

def register_unknown(cid, parent_cid, payload, guess):
    e = unknown_chunks.setdefault(cid, {
        "count": 0, "parents": set(), "sizes": [], "samples": [], "guesses": []
    })
    e["count"] += 1
    e["parents"].add(parent_cid)
    e["sizes"].append(len(payload))
    e["guesses"].append(guess)
    if len(e["samples"]) < 3:
        e["samples"].append(payload[:128])

def populate_runtime_unknowns(root, data):
    unknown_chunks.clear()

    def walk(n):
        if n.cid != 0xFFFF:
            if n.cid not in CHUNK_NAMES_3DS and n.cid not in CHUNK_NAMES_I3D:
                payload = data[n.payload_start:n.payload_end]
                guess = guess_payload_type(payload)
                register_unknown(n.cid, n.parent.cid if n.parent else None, payload, guess)
        for c in n.children:
            walk(c)

    walk(root)


# ------------------------------------------------------------
# Colors
# ------------------------------------------------------------

COLOR_3DS = QColor(0,130,0)
COLOR_I3D = QColor(0,90,200)
COLOR_UNK = QColor(180,0,0)


# ------------------------------------------------------------
# Unknown Chunk Dialog
# ------------------------------------------------------------

class DiscoveryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unknown Chunks")
        self.resize(800, 600)

        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFont("Consolas", 10))
        layout.addWidget(self.text)

    def populate(self, d):
        if not d:
            self.text.setPlainText("No unknown chunks detected.")
            return

        lines = []
        for cid, info in sorted(d.items()):
            lines.append(f"0x{cid:04X}")
            lines.append(f"  Count: {info['count']}")
            sizes = info["sizes"]
            lines.append(f"  Size: min={min(sizes)} max={max(sizes)} avg={sum(sizes)/len(sizes):.1f}")
            parents = ", ".join(f"0x{p:04X}" for p in sorted(info["parents"]))
            lines.append(f"  Parents: {parents}")
            gmap = {}
            for g in info["guesses"]:
                gmap[g] = gmap.get(g, 0) + 1
            lines.append("  Guess: " + ", ".join(f"{k}×{v}" for k,v in gmap.items()))
            if info["samples"]:
                lines.append("  Sample: " + " ".join(f"{b:02X}" for b in info["samples"][0]))
            lines.append("")
        self.text.setPlainText("\n".join(lines))


# ------------------------------------------------------------
# GUI
# ------------------------------------------------------------

class ChunkExplorerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("3DS / I3D Chunk Explorer")
        self.resize(1500, 900)

        self.file_data = b""
        self.root_node = None
        self.decoder = ChunkDecoder()

        self.init_ui()
        self.init_menu()

    def init_ui(self):
        central = QWidget()
        vbox = QVBoxLayout(central)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("3DS = Green,  I3D = Blue,  Unknown = Red")

        splitter = QSplitter(Qt.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Chunks"])
        self.tree.itemSelectionChanged.connect(self.on_tree_select)
        splitter.addWidget(self.tree)

        right = QSplitter(Qt.Vertical)

        self.hex_view = QTextEdit()
        self.hex_view.setReadOnly(True)
        self.hex_view.setFont(QFont("Consolas", 9))
        self.hex_view.setLineWrapMode(QTextEdit.NoWrap)
        right.addWidget(self.hex_view)

        self.info_view = QTextEdit()
        self.info_view.setReadOnly(True)
        self.info_view.setFont(QFont("Consolas", 9))
        self.info_view.setLineWrapMode(QTextEdit.NoWrap)
        right.addWidget(self.info_view)

        self.interpret_view = QTextEdit()
        self.interpret_view.setReadOnly(True)
        self.interpret_view.setFont(QFont("Consolas", 9))
        self.interpret_view.setLineWrapMode(QTextEdit.NoWrap)
        right.addWidget(self.interpret_view)

        splitter.addWidget(right)
        splitter.setSizes([500, 1000])

        vbox.addWidget(splitter)
        self.setCentralWidget(central)

    def init_menu(self):
        m = self.menuBar()

        f = m.addMenu("&File")
        open_act = QAction("Open…", self)
        open_act.triggered.connect(self.load_file)
        f.addAction(open_act)
        f.addSeparator()
        quit_act = QAction("Quit", self)
        quit_act.triggered.connect(self.close)
        f.addAction(quit_act)

        t = m.addMenu("&Tools")
        disc = QAction("Unknown Chunk Report", self)
        disc.triggered.connect(self.show_discovery_dialog)
        t.addAction(disc)

    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open 3DS/I3D File", "", "All Files (*)")
        if not path: return

        try:
            with open(path, "rb") as f:
                self.file_data = f.read()
        except Exception as e:
            self.status.showMessage(f"Error loading file: {e}")
            return

        parser = ChunkParser(self.file_data)
        self.root_node = parser.parse()

        self.populate_tree()
        self.hex_view.clear()
        self.info_view.clear()
        self.interpret_view.clear()
        self.status.showMessage(f"Loaded: {path}")

    def populate_tree(self):
        self.tree.clear()
        if not self.root_node: return
        for c in self.root_node.children:
            self.tree.addTopLevelItem(self.make_tree_item(c))
        self.tree.expandToDepth(4)

    def classify_color(self, cid):
        if cid in CHUNK_NAMES_3DS: return COLOR_3DS
        if cid in CHUNK_NAMES_I3D: return COLOR_I3D
        return COLOR_UNK

    def make_tree_item(self, node):
        cid = node.cid
        name = CHUNK_NAMES.get(cid, f"0x{cid:04X}")
        txt = f"{name}  (off={node.start}, size={node.size})"
        item = QTreeWidgetItem([txt])
        item.setData(0, Qt.UserRole, node)
        item.setForeground(0, QBrush(self.classify_color(cid)))

        for ch in node.children:
            item.addChild(self.make_tree_item(ch))

        return item

    def on_tree_select(self):
        sel = self.tree.selectedItems()
        if not sel or not self.file_data: return

        item = sel[0]
        node = item.data(0, Qt.UserRole)
        if not isinstance(node, ChunkNode): return

        region = self.file_data[node.start:node.start + node.size]
        self.hex_view.setPlainText(self.format_hex(region))

        payload = self.file_data[node.payload_start:node.payload_end]
        guess = guess_payload_type(payload)

        if node.cid in CHUNK_NAMES_3DS: group = "3DS Standard"
        elif node.cid in CHUNK_NAMES_I3D: group = "I3D Extension"
        else: group = "Unknown"

        info = [
            f"Chunk ID: 0x{node.cid:04X}",
            f"Group: {group}",
            "",
            f"Offset: {node.start}",
            f"Chunk Size: {node.size}",
            f"Payload Size: {node.payload_size}",
            "",
            f"Children: {len(node.children)}",
            f"Guess: {guess}",
        ]

        if looks_ascii(payload):
            preview = payload.decode("ascii","replace")
            info.append("\nASCII Preview:\n" + preview[:300])

        self.info_view.setPlainText("\n".join(info))
        self.interpret_view.setPlainText(
            self.decoder.decode(node, payload, self.file_data)
        )


    def show_discovery_dialog(self):
        dlg = DiscoveryDialog(self)
        populate_runtime_unknowns(self.root_node, self.file_data)
        dlg.populate(unknown_chunks)
        dlg.exec_()

    @staticmethod
    def format_hex(data, width=16):
        out = []
        for i in range(0, len(data), width):
            blk = data[i:i+width]
            hexp = " ".join(f"{b:02X}" for b in blk)
            asc = "".join(chr(b) if 32 <= b < 127 else "." for b in blk)
            out.append(f"{i:08X}  {hexp:<{width*3}}  {asc}")
        return "\n".join(out)


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    w = ChunkExplorerWindow()
    w.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
