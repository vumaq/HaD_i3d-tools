#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
3DS / I3D Chunk Explorer — Final Refactored Version
===================================================

Features:
---------
• Full 3DS + I3D chunk classification
• Paul Bourke 3DS chunk naming preserved
• I3D chunk naming: Friendly Title Case (acronyms preserved)
• 3 groups: 3DS (green), I3D (blue), Unknown (red)
• Hex viewer
• Info viewer
• Interpreted payload decoding
• Unknown chunk registry
• PyQt5 GUI

"""

import sys
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QTreeWidget,
    QTreeWidgetItem, QTextEdit, QFileDialog, QAction, QSplitter, QStatusBar,
    QDialog, QPlainTextEdit
)
from PyQt5.QtGui import QFont, QColor, QBrush
from PyQt5.QtCore import Qt



# ============================================================================
# Utility: Friendly Title Case Name Builder (I3D)
# ============================================================================

def friendly_name(enum_name: str) -> str:
    """
    Convert names like:
        CHUNK_TRIMESH  -> "Trim Mesh"
        CHUNK_NURBS_CVS -> "NURBS CVS"
        MAT_REFBLUR -> "Mat Refblur"
        VIEWPORT_LAYOUT -> "Viewport Layout"

    Rules:
    - Remove CHUNK_ prefix
    - Split by underscores
    - Title-case each part
    - Preserve acronyms as-is (NURBS, CVS, etc.)
    - Do NOT expand concepts (Option A)
    """
    name = enum_name.strip()

    # Remove standard prefixes
    for p in ("CHUNK_", "MAT_", "NURBS_"):
        if name.startswith(p):
            name = name[len(p):]

    parts = name.split("_")
    out = []
    for p in parts:
        if p.isupper():        # acronym
            out.append(p)
        else:
            out.append(p.capitalize())
    return " ".join(out)


# ============================================================================
# Group Containers
# ============================================================================

CHUNK_NAMES_3DS: Dict[int, str] = {}        # Paul Bourke official 3DS names
CHUNK_NAMES_I3D: Dict[int, str] = {}        # I3D extension chunks (friendly names)
CHUNK_NAMES_UNKNOWN: Dict[int, str] = {}    # Runtime-discovered unknowns

# Combined lookup (rebuilt after both groups populated)
CHUNK_NAMES: Dict[int, str] = {}

# ============================================================================
# SECTION 2 — FULL 3DS + I3D CHUNK TABLES
# ============================================================================

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

    # Lights
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

CHUNK_NAMES_I3D = {
    0x4200: "CHUNK_MAP_CHANNEL",

}

# Build CHUNK_NAMES_I3D with friendly names
for cid, raw_name in CHUNK_NAMES_I3D.items():
    if cid not in CHUNK_NAMES_3DS:   # avoid overriding Paul Bourke
        CHUNK_NAMES_I3D[cid] = friendly_name(raw_name)

# Combined map
CHUNK_NAMES = {**CHUNK_NAMES_3DS, **CHUNK_NAMES_I3D, **CHUNK_NAMES_UNKNOWN}

# ============================================================================
# SECTION 3 — ChunkNode and ChunkParser
# ============================================================================

@dataclass
class ChunkNode:
    cid: int
    start: int
    size: int
    children: List["ChunkNode"] = field(default_factory=list)
    parent: Optional["ChunkNode"] = field(default=None, repr=False)

    @property
    def payload_start(self) -> int:
        return self.start + 6

    @property
    def payload_end(self) -> int:
        return self.start + self.size

    @property
    def payload_size(self) -> int:
        return max(0, self.size - 6)

    def add_child(self, child: "ChunkNode"):
        child.parent = self
        self.children.append(child)


class ChunkParser:
    """
    Reads I3D/3DS chunked file structure.
    """

    def __init__(self, data: bytes):
        self.data = data

    @staticmethod
    def u16(buf, off) -> int:
        return struct.unpack_from("<H", buf, off)[0]

    @staticmethod
    def u32(buf, off) -> int:
        return struct.unpack_from("<I", buf, off)[0]

    # ---------------------------------------------------------

    def parse_chunk(self, offset: int, file_end: int, parent: ChunkNode):
        data = self.data

        # not enough space left for a chunk header
        if offset + 6 > file_end:
            return None, offset

        try:
            cid = self.u16(data, offset)
            length = self.u32(data, offset + 2)
        except Exception:
            return None, file_end

        if length < 6 or offset + length > file_end:
            return None, file_end

        node = ChunkNode(cid, offset, length)
        parent.add_child(node)

        payload_start = node.payload_start
        payload_end = node.payload_end

        # SPECIAL HANDLING 1 — named object (0x4000)
        if cid == 0x4000:
            pos = payload_start
            # read zero-terminated string
            while pos < payload_end and data[pos] != 0:
                pos += 1
            if pos < payload_end:
                pos += 1  # skip null terminator

            # parse subchunks
            while pos + 6 <= payload_end:
                child, next_pos = self.parse_chunk(pos, payload_end, node)
                if child is None or next_pos <= pos:
                    break
                pos = next_pos

                

        # SPECIAL HANDLING 2 — multi-subchunk containers
        elif cid in (
            0x4D4D,  # MAIN
            0x3D3D,  # EDITOR
            0x4100,  # TRI MESH
            0xAFFF,  # MATERIAL
            0xA200,  # TEXTURE BLOCK
            0xB000,  # KEYFRAMER ROOT

            # KEYFRAMER NODE TAGS
            0xB002,  # OBJECT NODE
            0xB003,  # CAMERA NODE
            0xB004,  # TARGET NODE
            0xB005,  # LIGHT NODE
            0xB006,  # LIGHT TARGET
            0xB007,  # SPOTLIGHT NODE

            # NODE HEADER (MUST BE CONTAINER)
            0xB010,
        ):
            pos = payload_start
            while pos + 6 <= payload_end:
                child, next_pos = self.parse_chunk(pos, payload_end, node)
                if child is None or next_pos <= pos:
                    break
                pos = next_pos

        return node, offset + length

    # ---------------------------------------------------------

    def parse(self) -> ChunkNode:
        """
        Parses the entire file and returns the root node.
        Unknown chunks are recorded later (in a post-traversal)
        to allow grouping rules to apply.
        """
        root = ChunkNode(0xFFFF, 0, len(self.data))  # synthetic root
        pos = 0
        file_end = len(self.data)

        while pos + 6 <= file_end:
            node, next_pos = self.parse_chunk(pos, file_end, root)
            if node is None or next_pos <= pos:
                break
            pos = next_pos

        return root

# ============================================================================
# ChunkDecoder (interprets common chunk payloads)
# ============================================================================

class ChunkDecoder:

    # --------------------------------------------------------------
    # Utility functions for reading numbers
    # --------------------------------------------------------------
    def u16(self, b, o=0): return int.from_bytes(b[o:o+2], "little")
    def u32(self, b, o=0): return int.from_bytes(b[o:o+4], "little")
    def f32(self, b, o=0): return struct.unpack("<f", b[o:o+4])[0]

    # ======================================================================
    # MAIN DECODER
    # ======================================================================
    def decode(self, node, data):
        """
        Human-readable interpretation of chunk payloads.
        This uses clean routing blocks and is fully extendable.
        """
        cid = node.cid
        out = []

        # ------------------------------------------------------------------
        # SMALL HELPER FUNCTIONS used by many animation tracks
        # ------------------------------------------------------------------
        def read_track_header(buf):
            # U16 flags, U16 unknown, U32 key count
            if len(buf) < 8:
                return None, None, None, 0
            flags = self.u16(buf, 0)
            unk   = self.u16(buf, 2)
            key_count = self.u32(buf, 4)
            return flags, unk, key_count, 8

        def read_key_header(buf, off):
            # U32 frame, U16 flags
            if off + 6 > len(buf):
                return None, None, off
            frame = self.u32(buf, off)
            kflags = self.u16(buf, off+4)
            return frame, kflags, off+6

        # ==================================================================
        # GEOMETRY BLOCKS (VERTICES / FACES / UV / TRANSFORMS / MAP CHANNELS)
        # ==================================================================

        # --------------------------------------------------------------
        # 0x4110 — Vertex List (TRI_VERTEXL)
        # --------------------------------------------------------------
        if cid == 0x4110:
            if len(data) < 2:
                return "Invalid vertex list (too short)."
            count = self.u16(data, 0)
            out.append(f"Vertex count: {count}")
            off = 2

            for i in range(min(count, 10)):
                if off + 12 > len(data):
                    break
                x = self.f32(data, off)
                y = self.f32(data, off+4)
                z = self.f32(data, off+8)
                out.append(f"[{i}] ({x:.4f}, {y:.4f}, {z:.4f})")
                off += 12

            return "\n".join(out)

        # --------------------------------------------------------------
        # 0x4120 — Face List (indices + flags)
        # --------------------------------------------------------------
        if cid == 0x4120:
            if len(data) < 2:
                return "Invalid face list (too short)."
            count = self.u16(data, 0)
            out.append(f"Face count: {count}")
            off = 2

            for i in range(min(count, 10)):
                if off + 8 > len(data):
                    break
                a = self.u16(data, off)
                b = self.u16(data, off+2)
                c = self.u16(data, off+4)
                flags = self.u16(data, off+6)
                out.append(f"[{i}] {a}, {b}, {c}  flags=0x{flags:04X}")
                off += 8

            return "\n".join(out)

        # --------------------------------------------------------------
        # 0x4140 — UV Coordinates (U, V floats)
        # --------------------------------------------------------------
        if cid == 0x4140:
            if len(data) < 2:
                return "Invalid UV list."
            count = self.u16(data, 0)
            off = 2
            out.append(f"UV count: {count}")

            for i in range(min(count, 10)):
                if off + 8 > len(data):
                    break
                u = self.f32(data, off)
                v = self.f32(data, off+4)
                out.append(f"[{i}] U={u:.4f}, V={v:.4f}")
                off += 8

            return "\n".join(out)

        # --------------------------------------------------------------
        # 0x4160 — Object Transform Matrix (3x4 floats)
        # --------------------------------------------------------------
        if cid == 0x4160:
            if len(data) < 48:
                return "Invalid transform matrix (need 48 bytes)."

            vals = struct.unpack("<12f", data[:48])
            out.append("Transform Matrix (3×4):")
            out.append(f"{vals[0]:.4f} {vals[1]:.4f} {vals[2]:.4f} {vals[3]:.4f}")
            out.append(f"{vals[4]:.4f} {vals[5]:.4f} {vals[6]:.4f} {vals[7]:.4f}")
            out.append(f"{vals[8]:.4f} {vals[9]:.4f} {vals[10]:.4f} {vals[11]:.4f}")

            return "\n".join(out)

        # --------------------------------------------------------------
        # 0x4200 — GIANTS I3D Map Channel (UV Set + UV Faces)
        # --------------------------------------------------------------
        if cid == 0x4200:
            if len(data) < 6:
                return "Invalid MAP_CHANNEL payload."

            chan = self.u32(data, 0)
            count = self.u16(data, 4)
            out.append(f"Channel index: {chan}")
            out.append(f"UV vertex count: {count}")

            off = 6

            out.append("\nFirst vertices:")
            for i in range(min(count, 10)):
                if off + 8 > len(data): break
                u = self.f32(data, off)
                v = self.f32(data, off+4)
                out.append(f"[{i}] U={u:.4f}, V={v:.4f}")
                off += 8

            # UV faces
            if off + 2 <= len(data):
                faces = self.u16(data, off)
                off += 2
                out.append(f"\nUV face count: {faces}")

                for i in range(min(faces, 10)):
                    if off + 6 > len(data): break
                    a = self.u16(data, off)
                    b = self.u16(data, off+2)
                    c = self.u16(data, off+4)
                    out.append(f"[{i}] {a}, {b}, {c}")
                    off += 6

            return "\n".join(out)

        # --------------------------------------------------------------
        # 0x4600 — Local Axis (GIANTS I3D)
        # --------------------------------------------------------------
        if cid == 0x4600:
            if len(data) < 12:
                return "Invalid Local Axis block."
            x = self.f32(data, 0)
            y = self.f32(data, 4)
            z = self.f32(data, 8)
            return f"Local Axis Vector: ({x:.4f}, {y:.4f}, {z:.4f})"

        # --------------------------------------------------------------
        # 0x4610 — Axis Matrix (GIANTS I3D, 3x3)
        # --------------------------------------------------------------
        if cid == 0x4610:
            if len(data) < 36:
                return "Invalid Axis Matrix (need 36 bytes)."
            m = struct.unpack("<9f", data[:36])
            out.append("Axis Matrix (3x3):")
            out.append(f"{m[0]:.4f} {m[1]:.4f} {m[2]:.4f}")
            out.append(f"{m[3]:.4f} {m[4]:.4f} {m[5]:.4f}")
            out.append(f"{m[6]:.4f} {m[7]:.4f} {m[8]:.4f}")
            return "\n".join(out)

        # ==================================================================
        # COLOR, PERCENTAGE, AND MATERIAL-SUPPORT DECODERS
        # ==================================================================

        # --------------------------------------------------------------
        # 0x0010 — RGBF (3 floats)
        # --------------------------------------------------------------
        if cid == 0x0010 and len(data) >= 12:
            r = self.f32(data, 0)
            g = self.f32(data, 4)
            b = self.f32(data, 8)
            return f"RGB Float: ({r:.4f}, {g:.4f}, {b:.4f})"

        # --------------------------------------------------------------
        # 0x0011 — RGBB (3 x uint8)
        # --------------------------------------------------------------
        if cid == 0x0011 and len(data) >= 3:
            r, g, b = data[0], data[1], data[2]
            return f"RGB (8-bit): ({r}, {g}, {b})"

        # --------------------------------------------------------------
        # 0x0012 — LIN_COLOR_24 (linear 8-bit)
        # --------------------------------------------------------------
        if cid == 0x0012 and len(data) >= 3:
            r, g, b = data[0], data[1], data[2]
            return f"Linear RGB (24-bit): ({r}, {g}, {b})"

        # --------------------------------------------------------------
        # 0x0013 — LIN_COLOR_F (linear float)
        # --------------------------------------------------------------
        if cid == 0x0013 and len(data) >= 12:
            r = self.f32(data, 0)
            g = self.f32(data, 4)
            b = self.f32(data, 8)
            return f"Linear RGB Float: ({r:.4f}, {g:.4f}, {b:.4f})"

        # --------------------------------------------------------------
        # 0x0030 — INT_PERCENTAGE (0–100 as u16)
        # --------------------------------------------------------------
        if cid == 0x0030 and len(data) >= 2:
            pct = self.u16(data, 0)
            return f"Percentage (int16): {pct}%"

        # --------------------------------------------------------------
        # 0x0031 — FLOAT_PERCENTAGE (0–1.0 float)
        # --------------------------------------------------------------
        if cid == 0x0031 and len(data) >= 4:
            pct = self.f32(data, 0)
            return f"Percentage (float): {pct * 100:.2f}%"

        # --------------------------------------------------------------
        # 0xA000 — Material Name (ASCIIZ)
        # --------------------------------------------------------------
        if cid == 0xA000:
            s = []
            off = 0
            while off < len(data) and data[off] != 0:
                s.append(data[off]); off += 1
            name = bytes(s).decode("ascii", "replace")
            return f"Material Name: \"{name}\""

        # --------------------------------------------------------------
        # 0xA010 — Ambient Color
        # --------------------------------------------------------------
        if cid == 0xA010:
            return "Ambient Color (container)"

        # --------------------------------------------------------------
        # 0xA020 — Diffuse Color
        # --------------------------------------------------------------
        if cid == 0xA020:
            return "Diffuse Color (container)"

        # --------------------------------------------------------------
        # 0xA030 — Specular Color
        # --------------------------------------------------------------
        if cid == 0xA030:
            return "Specular Color (container)"

        # --------------------------------------------------------------
        # 0xA040 — Shininess (Specular Exponent)
        # --------------------------------------------------------------
        if cid == 0xA040:
            return "Shininess (container)"

        # --------------------------------------------------------------
        # 0xA041 — Shininess Strength
        # --------------------------------------------------------------
        if cid == 0xA041:
            return "Shininess Strength (container)"

        # --------------------------------------------------------------
        # 0xA042 — Transparency
        # --------------------------------------------------------------
        if cid == 0xA042:
            return "Transparency (container)"

        # --------------------------------------------------------------
        # 0xA050 — Reflection Color
        # --------------------------------------------------------------
        if cid == 0xA050:
            out.append("Material Transparency:")
            if len(data) >= 2:
                pct = self.u16(data, 0)
                out.append(f"  Transparency: {pct}% (integer)")
            elif len(data) >= 4:
                pctf = self.f32(data, 0)
                out.append(f"  Transparency: {pctf*100:.2f}% (float)")
            else:
                out.append(f"  (Unexpected payload: {len(data)} bytes)")
            return "\n".join(out)

        # --------------------------------------------------------------
        # 0xA200 — Texture Map 1 container
        # (subchunks contain texture filename, mapping params, etc.)
        # --------------------------------------------------------------
        if cid == 0xA200:
            return "Texture Map Block (MAP 1)"

        # --------------------------------------------------------------
        # 0xA300 — Texture Filename (ASCIIZ)
        # --------------------------------------------------------------
        if cid == 0xA300:
            s = []
            off = 0
            while off < len(data) and data[off] != 0:
                s.append(data[off]); off += 1
            return f"Texture Filename: \"{bytes(s).decode('ascii','replace')}\""

        # --------------------------------------------------------------
        # 0xA351 — Texture Mapping: Scale (u, v floats)
        # --------------------------------------------------------------
        if cid == 0xA351 and len(data) >= 8:
            u = self.f32(data, 0)
            v = self.f32(data, 4)
            return f"Texture Scale: U={u:.4f}, V={v:.4f}"

        # --------------------------------------------------------------
        # 0xA352 — Texture Mapping: Offset (u,v floats)
        # --------------------------------------------------------------
        if cid == 0xA352 and len(data) >= 8:
            u = self.f32(data, 0)
            v = self.f32(data, 4)
            return f"Texture Offset: U={u:.4f}, V={v:.4f}"

        # --------------------------------------------------------------
        # 0xA353 — Texture Rotation (float)
        # --------------------------------------------------------------
        if cid == 0xA353 and len(data) >= 4:
            rot = self.f32(data, 0)
            return f"Texture Rotation: {rot:.4f} radians"
        
        # ==================================================================
        # KEYFRAMER CORE BLOCKS (B00A, B010–B014, B00E, B008, B009)
        # ==================================================================

        # --------------------------------------------------------------
        # 0xB00A — Keyframe Header
        # --------------------------------------------------------------
        if cid == 0xB00A:
            out.append("Keyframe Header:")
            off = 0

            if len(data) < 2:
                return "Invalid Keyframe Header"

            flags = self.u16(data, off); off += 2
            out.append(f"  Flags/Version: {flags} (0x{flags:04X})")

            # ASCIIZ scene name
            name = []
            while off < len(data) and data[off] != 0:
                name.append(data[off]); off += 1
            if off < len(data): off += 1
            out.append(f"  Scene Name: {bytes(name).decode('ascii','replace')}")

            # Optional frame range
            if off + 8 <= len(data):
                start = self.u32(data, off)
                end   = self.u32(data, off+4)
                out.append(f"  Start Frame: {start}")
                out.append(f"  End Frame:   {end}")

            return "\n".join(out)

        # --------------------------------------------------------------
        # 0xB010 — Node Header
        # --------------------------------------------------------------
        if cid == 0xB010:
            out.append("Node Header:")

            if len(data) < 8:
                return "Invalid Node Header"

            node_id = self.u16(data, 0)
            flags   = self.u16(data, 2)
            parent  = self.u16(data, 4)

            out.append(f"  Node ID: {node_id}")
            out.append(f"  Flags: 0x{flags:04X}")
            out.append(f"  Parent Node: {parent}")

            # Remaining bytes are padding or unused
            return "\n".join(out)

        # --------------------------------------------------------------
        # 0xB011 — Node Instance Name (ASCIIZ)
        # --------------------------------------------------------------
        if cid == 0xB011:
            out.append("Node Instance Name:")
            s = []
            off = 0
            while off < len(data) and data[off] != 0:
                s.append(data[off]); off += 1
            return f"  \"{bytes(s).decode('ascii','replace')}\""

        # --------------------------------------------------------------
        # 0xB00B — Object Info
        # --------------------------------------------------------------
        if cid == 0xB00B:
            out.append("Object Info:")
            if len(data) < 6:
                return "Invalid Object Info"
            obj_id = self.u16(data, 0)
            f1     = self.u16(data, 2)
            f2     = self.u16(data, 4)
            out.append(f"  Object ID: {obj_id}")
            out.append(f"  Flags1: 0x{f1:04X}")
            out.append(f"  Flags2: 0x{f2:04X}")
            return "\n".join(out)

        # --------------------------------------------------------------
        # 0xB013 — Pivot Point (3 floats)
        # --------------------------------------------------------------
        if cid == 0xB013:
            if len(data) < 12:
                return "Invalid Pivot"
            x = self.f32(data, 0)
            y = self.f32(data, 4)
            z = self.f32(data, 8)
            return f"Pivot: ({x:.4f}, {y:.4f}, {z:.4f})"

        # --------------------------------------------------------------
        # 0xB014 — Node Bounding Box
        # --------------------------------------------------------------
        if cid == 0xB014:
            if len(data) < 24:
                return "Invalid Bounding Box"
            minx = self.f32(data, 0)
            miny = self.f32(data, 4)
            minz = self.f32(data, 8)
            maxx = self.f32(data, 12)
            maxy = self.f32(data, 16)
            maxz = self.f32(data, 20)
            return (
                "Bounding Box:\n"
                f"  Min: ({minx:.4f}, {miny:.4f}, {minz:.4f})\n"
                f"  Max: ({maxx:.4f}, {maxy:.4f}, {maxz:.4f})"
            )

        # --------------------------------------------------------------
        # 0xB00E — Bone Node (GIANTS I3D)
        # --------------------------------------------------------------
        if cid == 0xB00E:
            out.append("Bone Node:")
            if len(data) < 2:
                return "Invalid Bone Node"
            bone_id = self.u16(data, 0)
            out.append(f"  Bone ID: {bone_id}")
            if len(data) > 2:
                extra = " ".join(f"{b:02X}" for b in data[2:])
                out.append(f"  Extra: {extra}")
            return "\n".join(out)

        # --------------------------------------------------------------
        # 0xB008 — Node Flags (float value)
        # --------------------------------------------------------------
        if cid == 0xB008 and len(data) >= 8:
            f1 = self.f32(data, 0)
            f2 = self.f32(data, 4)
            return f"Node Flags:\n  Value1={f1}\n  Value2={f2}"

        # --------------------------------------------------------------
        # 0xB009 — Node Float (single float)
        # --------------------------------------------------------------
        if cid == 0xB009 and len(data) >= 4:
            v = self.f32(data, 0)
            return f"Node Float: {v}"

        # ==================================================================
        # ANIMATION TRACK TAGS (0xB020 – 0xB02A)
        # ==================================================================

        # Helper — read TrackHeader
        def read_track_header(buf):
            if len(buf) < 8:
                return None, None, 0, 0
            flags  = self.u16(buf, 0)
            unk    = self.u16(buf, 2)
            keys   = self.u32(buf, 4)
            return flags, unk, keys, 8

        # Helper — read a single key header
        def read_key_header(buf, off):
            if off + 6 > len(buf):
                return None, None, off
            frame  = self.u32(buf, off)
            kflags = self.u16(buf, off+4)
            return frame, kflags, off+6


        # --------------------------------------------------------------
        # 0xB020 — Position Track
        # --------------------------------------------------------------
        if cid == 0xB020:
            out.append("Position Track:")
            flags, unk, key_count, off = read_track_header(data)
            out.append(f"  Flags: 0x{flags:04X}")
            out.append(f"  Key Count: {key_count}")

            for i in range(min(key_count, 50)):
                frame, kflags, off = read_key_header(data, off)
                if off + 12 > len(data): break
                x = self.f32(data, off)
                y = self.f32(data, off+4)
                z = self.f32(data, off+8)
                off += 12
                out.append(f"  [{i}] Frame={frame}  Flags=0x{kflags:04X}  Pos=({x:.4f}, {y:.4f}, {z:.4f})")

            return "\n".join(out)


        # --------------------------------------------------------------
        # 0xB021 — Rotation Track (Angle + Axis)
        # --------------------------------------------------------------
        if cid == 0xB021:
            out.append("Rotation Track:")
            flags, unk, key_count, off = read_track_header(data)
            out.append(f"  Flags: 0x{flags:04X}")
            out.append(f"  Key Count: {key_count}")

            for i in range(min(key_count, 50)):
                frame, kflags, off = read_key_header(data, off)
                if off + 16 > len(data): break
                angle = self.f32(data, off)
                ax = self.f32(data, off+4)
                ay = self.f32(data, off+8)
                az = self.f32(data, off+12)
                off += 16

                out.append(
                    f"  [{i}] Frame={frame} Flags=0x{kflags:04X}  "
                    f"Angle={angle:.4f}  Axis=({ax:.3f}, {ay:.3f}, {az:.3f})"
                )

            return "\n".join(out)


        # --------------------------------------------------------------
        # 0xB022 — Scale Track (sx, sy, sz)
        # --------------------------------------------------------------
        if cid == 0xB022:
            out.append("Scale Track:")
            flags, unk, key_count, off = read_track_header(data)
            out.append(f"  Flags: 0x{flags:04X}")
            out.append(f"  Key Count: {key_count}")

            for i in range(min(key_count, 50)):
                frame, kflags, off = read_key_header(data, off)
                if off + 12 > len(data): break
                sx = self.f32(data, off)
                sy = self.f32(data, off+4)
                sz = self.f32(data, off+8)
                off += 12

                out.append(
                    f"  [{i}] Frame={frame} Flags=0x{kflags:04X}  "
                    f"Scale=({sx:.4f}, {sy:.4f}, {sz:.4f})"
                )

            return "\n".join(out)


        # --------------------------------------------------------------
        # 0xB023 — FOV Track (float)
        # --------------------------------------------------------------
        if cid == 0xB023:
            out.append("FOV Track:")
            flags, unk, key_count, off = read_track_header(data)
            out.append(f"  Key Count: {key_count}")

            for i in range(min(key_count, 50)):
                frame, kflags, off = read_key_header(data, off)
                if off + 4 > len(data): break
                fov = self.f32(data, off)
                off += 4
                out.append(f"  [{i}] Frame={frame}  FOV={fov:.4f}")

            return "\n".join(out)


        # --------------------------------------------------------------
        # 0xB024 — Roll Track (float)
        # --------------------------------------------------------------
        if cid == 0xB024:
            out.append("Roll Track:")

            if len(data) < 8:
                return "Invalid Roll Track"

            flags  = self.u16(data, 0)
            unk    = self.u16(data, 2)
            count  = self.u32(data, 4)
            out.append(f"  Flags: 0x{flags:04X}")
            out.append(f"  Key Count: {count}")

            off = 8
            for i in range(min(count, 50)):
                if off + 10 > len(data): break

                frame  = self.u32(data, off)
                kflags = self.u16(data, off+4)
                roll   = self.f32(data, off+6)

                out.append(f"  [{i}] Frame={frame}  Roll={roll:.6f}  Flags=0x{kflags:04X}")
                off += 10

            return "\n".join(out)



        # --------------------------------------------------------------
        # 0xB025 — Color Track (3 floats)
        # --------------------------------------------------------------
        if cid == 0xB025:
            out.append("Color Track:")

            if len(data) < 8:
                return "Invalid Color Track"

            flags  = self.u16(data, 0)
            unk    = self.u16(data, 2)
            count  = self.u32(data, 4)

            out.append(f"  Flags: 0x{flags:04X}")
            out.append(f"  Key Count: {count}")

            off = 8
            for i in range(min(count, 50)):
                if off + 18 > len(data): break

                frame  = self.u32(data, off)
                kflags = self.u16(data, off+4)
                r      = self.f32(data, off+6)
                g      = self.f32(data, off+10)
                b      = self.f32(data, off+14)

                out.append(
                    f"  [{i}] Frame={frame} RGB=({r:.4f}, {g:.4f}, {b:.4f}) Flags=0x{kflags:04X}"
                )
                off += 18

            return "\n".join(out)


        # --------------------------------------------------------------
        # 0xB027 — Hot Spot Track (float)
        # --------------------------------------------------------------
        if cid == 0xB027:
            out.append("Hotspot Track:")

            if len(data) < 8:
                return "Invalid Hotspot Track"

            flags  = self.u16(data, 0)
            unk    = self.u16(data, 2)
            count  = self.u32(data, 4)

            out.append(f"  Flags: 0x{flags:04X}")
            out.append(f"  Key Count: {count}")

            off = 8
            for i in range(min(count, 50)):
                if off + 10 > len(data): break

                frame  = self.u32(data, off)
                kflags = self.u16(data, off+4)
                value  = self.f32(data, off+6)

                out.append(
                    f"  [{i}] Frame={frame} Hotspot={value:.6f} Flags=0x{kflags:04X}"
                )

                off += 10

            return "\n".join(out)



        # --------------------------------------------------------------
        # 0xB028 — Falloff Track (float)
        # --------------------------------------------------------------
        if cid == 0xB028:
            out.append("Falloff Track:")

            if len(data) < 8:
                return "Invalid Falloff Track"

            flags  = self.u16(data, 0)
            unk    = self.u16(data, 2)
            count  = self.u32(data, 4)

            out.append(f"  Flags: 0x{flags:04X}")
            out.append(f"  Key Count: {count}")

            off = 8
            for i in range(min(count, 50)):
                if off + 10 > len(data): break

                frame  = self.u32(data, off)
                kflags = self.u16(data, off+4)
                value  = self.f32(data, off+6)

                out.append(
                    f"  [{i}] Frame={frame} Falloff={value:.6f} Flags=0x{kflags:04X}"
                )

                off += 10

            return "\n".join(out)


        # --------------------------------------------------------------
        # 0xB029 — Hide Track (U16 → visibility)
        # --------------------------------------------------------------
        if cid == 0xB029:
            out.append("Hide Track:")
            flags, unk, key_count, off = read_track_header(data)

            for i in range(min(key_count, 50)):
                frame, kflags, off = read_key_header(data, off)
                if off + 2 > len(data): break
                v = self.u16(data, off)
                off += 2
                out.append(f"  [{i}] Frame={frame}  Visible={v == 0}")

            return "\n".join(out)


        # --------------------------------------------------------------
        # 0xB02A — Note Track (Text)
        # --------------------------------------------------------------
        if cid == 0xB02A:
            out.append("Note Track:")
            flags, unk, key_count, off = read_track_header(data)

            for i in range(min(key_count, 50)):
                frame, kflags, off = read_key_header(data, off)

                # null-terminated text
                text = []
                while off < len(data) and data[off] != 0:
                    text.append(data[off]); off += 1
                if off < len(data): off += 1

                out.append(
                    f"  [{i}] Frame={frame}  Note: {bytes(text).decode('ascii','replace')}"
                )

            return "\n".join(out)
        
        # ==================================================================
        # KEYFRAMER CORE BLOCKS
        # ==================================================================

        # --------------------------------------------------------------
        # 0xB00A — Keyframe Header
        # --------------------------------------------------------------
        if cid == 0xB00A:
            out.append("Keyframe Header:")
            if len(data) < 2:
                return "Invalid Keyframe Header"
            flags = self.u16(data, 0)
            out.append(f"  Flags/Version: {flags} (0x{flags:04X})")
            off = 2

            # Scene name (ASCIIZ)
            name_bytes = []
            while off < len(data) and data[off] != 0:
                name_bytes.append(data[off]); off += 1
            if off < len(data): off += 1
            scene_name = bytes(name_bytes).decode("ascii", "replace")

            out.append(f"  Scene Name: {scene_name}")

            # Optional start/end frames
            if off + 8 <= len(data):
                start = self.u32(data, off)
                end   = self.u32(data, off+4)
                out.append(f"  Start Frame: {start}")
                out.append(f"  End Frame:   {end}")

            return "\n".join(out)



        # ==================================================================
        # ADDITIONAL NODE BLOCKS (0xB002 – 0xB007)
        # ==================================================================

       # --------------------------------------------------------------
        # 0xB002 – 0xB007 Node Tag Containers
        # --------------------------------------------------------------
        if cid in (0xB002, 0xB003, 0xB004, 0xB005, 0xB006, 0xB007):
            names = {
                0xB002: "Object Node Tag",
                0xB003: "Camera Node Tag",
                0xB004: "Target Node Tag",
                0xB005: "Light Node Tag",
                0xB006: "Light Target Node Tag",
                0xB007: "Spotlight Node Tag",
            }
            return (
                f"{names[cid]} (container)\n"
                f"Child chunks:\n"
                f"  B010 – Node Header\n"
                f"  B011 – Instance Name\n"
                f"  B013 – Pivot\n"
                f"  B020/B021/B022… – Animation Tracks\n\n"
                f"(Payload contains only padding or legacy fields)"
            )




        # ==================================================================
        # NODE EXTRA / TRANSFORM / MISC BLOCKS (0xB013, 0xB014)
        # ==================================================================

        # --------------------------------------------------------------
        # 0xB013 — Pivot Point (vector3)
        # --------------------------------------------------------------
        if cid == 0xB013:
            out.append("Pivot Point:")
            if len(data) < 12:
                return "Invalid pivot point."
            x = self.f32(data, 0)
            y = self.f32(data, 4)
            z = self.f32(data, 8)
            out.append(f"  ({x:.4f}, {y:.4f}, {z:.4f})")
            return "\n".join(out)

        # --------------------------------------------------------------
        # 0xB014 — Bounding Box (6 floats)
        # --------------------------------------------------------------
        if cid == 0xB014:
            out.append("Bounding Box:")
            if len(data) < 24:
                return "Invalid bounding box."
            minx = self.f32(data, 0)
            miny = self.f32(data, 4)
            minz = self.f32(data, 8)
            maxx = self.f32(data, 12)
            maxy = self.f32(data, 16)
            maxz = self.f32(data, 20)
            out.append(f"  Min = ({minx:.4f}, {miny:.4f}, {minz:.4f})")
            out.append(f"  Max = ({maxx:.4f}, {maxy:.4f}, {maxz:.4f})")
            return "\n".join(out)

        # --------------------------------------------------------------
        # 0xB00E — Bone Node (GIANTS I3D Extension)
        # --------------------------------------------------------------
        if cid == 0xB00E:
            out.append("Bone Node:")
            if len(data) < 2:
                return "Invalid Bone Node."
            bone_id = self.u16(data, 0)
            out.append(f"  Bone ID: {bone_id}")

            if len(data) > 2:
                extra = " ".join(f"{b:02X}" for b in data[2:])
                out.append(f"  Extra Data: {extra}")

            return "\n".join(out)



        # ==================================================================
        # FALLBACK — Unknown / Undecoded
        # ==================================================================
        return (
            f"No dedicated decoder for chunk 0x{cid:04X}\n"
            f"Payload size: {len(data)}"
        )


# ============================================================================
# Unknown Chunk Registry + Guess Heuristics
# ============================================================================

unknown_chunks: Dict[int, Dict] = {}  # runtime registry


def register_unknown(cid: int, parent_cid: Optional[int], payload: bytes, guess: str):
    """
    Record info about a chunk ID we do not know yet.
    """
    entry = unknown_chunks.setdefault(cid, {
        "count": 0,
        "parents": set(),
        "sizes": [],
        "samples": [],
        "guesses": [],
    })
    entry["count"] += 1
    entry["sizes"].append(len(payload))
    entry["guesses"].append(guess)
    if parent_cid:
        entry["parents"].add(parent_cid)
    if len(entry["samples"]) < 3:
        entry["samples"].append(payload[:128])


# ---------------------------------------------------------------------------
# Heuristic recognisers (payload guesser)
# ---------------------------------------------------------------------------

def looks_ascii(data: bytes) -> bool:
    if not data:
        return False
    good = sum(1 for b in data if 32 <= b <= 126 or b in (9, 10, 13))
    return good / len(data) > 0.85


def looks_vector3(data: bytes) -> bool:
    if len(data) != 12:
        return False
    try:
        struct.unpack("<3f", data)
        return True
    except Exception:
        return False


def looks_matrix3x4(data: bytes) -> bool:
    if len(data) != 48:
        return False
    try:
        struct.unpack("<12f", data)
        return True
    except Exception:
        return False


def looks_float_array(data: bytes) -> bool:
    if len(data) < 4 or len(data) % 4 != 0:
        return False
    try:
        struct.unpack("<f", data[:4])
        return True
    except Exception:
        return False


def looks_u16_array(data: bytes) -> bool:
    return len(data) >= 2 and len(data) % 2 == 0


def guess_payload_type(data: bytes) -> str:
    if not data:
        return "Empty"
    if looks_ascii(data):
        return "ASCII"
    if looks_matrix3x4(data):
        return "Matrix3x4"
    if looks_vector3(data):
        return "Vector3"
    if looks_float_array(data):
        return "Float array"
    if looks_u16_array(data):
        return "UInt16 array"
    return "Binary"


# ---------------------------------------------------------------------------
# Legend Colors (3 groups)
# ---------------------------------------------------------------------------

COLOR_3DS   = QColor(0, 130, 0)       # dark green
COLOR_I3D   = QColor(0, 90, 200)      # deep blue
COLOR_UNK   = QColor(180, 0, 0)       # red


# ---------------------------------------------------------------------------
# Unknown Chunk Dialog
# ---------------------------------------------------------------------------

class DiscoveryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unknown Chunk Report")
        self.resize(850, 650)

        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFont("Consolas", 10))
        layout.addWidget(self.text)

    def populate(self, unknown_dict):
        if not unknown_dict:
            self.text.setPlainText("No unknown chunks detected.")
            return

        lines = []
        for cid, info in sorted(unknown_dict.items()):
            lines.append(f"Chunk 0x{cid:04X}")
            lines.append(f"  Count: {info['count']}")
            sizes = info['sizes']
            lines.append(f"  Size: min={min(sizes)} max={max(sizes)} avg={sum(sizes)/len(sizes):.1f}")
            parents = ", ".join(f"0x{p:04X}" for p in sorted(info["parents"]))
            lines.append(f"  Parents: {parents if parents else '(root)'}")

            gmap = {}
            for g in info["guesses"]:
                gmap[g] = gmap.get(g, 0) + 1
            gline = ", ".join(f"{k} ×{v}" for k, v in gmap.items())
            lines.append(f"  Guess: {gline}")

            if info["samples"]:
                sample_hex = " ".join(f"{b:02X}" for b in info["samples"][0])
                lines.append(f"  Sample: {sample_hex}")

            lines.append("")

        self.text.setPlainText("\n".join(lines))


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

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

    # ----------------------------------------------------------------------

    def init_ui(self):
        central = QWidget()
        vbox = QVBoxLayout(central)

        # Status bar with legend
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage(
            "Legend:  3DS = Green   |   I3D = Blue   |   Unknown = Red"
        )

        splitter = QSplitter(Qt.Horizontal)

        # Left tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Chunks"])
        self.tree.itemSelectionChanged.connect(self.on_tree_select)
        splitter.addWidget(self.tree)

        # Right side: vertical splitter
        right = QSplitter(Qt.Vertical)

        # Hex
        self.hex_view = QTextEdit()
        self.hex_view.setReadOnly(True)
        self.hex_view.setFont(QFont("Consolas", 9))
        self.hex_view.setLineWrapMode(QTextEdit.NoWrap)
        right.addWidget(self.hex_view)

        # Info
        self.info_view = QTextEdit()
        self.info_view.setReadOnly(True)
        self.info_view.setFont(QFont("Consolas", 9))
        self.info_view.setLineWrapMode(QTextEdit.NoWrap)
        right.addWidget(self.info_view)

        # Interpretation
        self.interpret_view = QTextEdit()
        self.interpret_view.setReadOnly(True)
        self.interpret_view.setFont(QFont("Consolas", 9))
        self.interpret_view.setLineWrapMode(QTextEdit.NoWrap)
        right.addWidget(self.interpret_view)

        splitter.addWidget(right)
        splitter.setSizes([500, 1000])

        vbox.addWidget(splitter)
        self.setCentralWidget(central)

    # ----------------------------------------------------------------------

    def init_menu(self):
        menu = self.menuBar()

        f = menu.addMenu("&File")
        open_action = QAction("Open…", self)
        open_action.triggered.connect(self.load_file)
        f.addAction(open_action)
        f.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        f.addAction(quit_action)

        t = menu.addMenu("&Tools")
        disc = QAction("Unknown Chunk Report", self)
        disc.triggered.connect(self.show_discovery_dialog)
        t.addAction(disc)

    # ----------------------------------------------------------------------

    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open 3DS/I3D File", "", "All Files (*)"
        )
        if not path:
            return

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

    # ----------------------------------------------------------------------

    def populate_tree(self):
        self.tree.clear()
        if not self.root_node:
            return

        for c in self.root_node.children:
            self.tree.addTopLevelItem(self.make_tree_item(c))

        self.tree.expandToDepth(4)

    # ----------------------------------------------------------------------

    def classify_color(self, cid):
        if cid in CHUNK_NAMES_3DS:
            return COLOR_3DS
        if cid in CHUNK_NAMES_I3D:
            return COLOR_I3D
        return COLOR_UNK

    # ----------------------------------------------------------------------

    def make_tree_item(self, node: ChunkNode):
        cid = node.cid

        # group name
        if cid in CHUNK_NAMES_3DS:
            name = CHUNK_NAMES_3DS[cid]
        elif cid in CHUNK_NAMES_I3D:
            name = CHUNK_NAMES_I3D[cid]
        else:
            name = f"0x{cid:04X}"

        txt = f"{name}  (off={node.start}, size={node.size})"
        item = QTreeWidgetItem([txt])
        item.setData(0, Qt.UserRole, node)

        # color
        item.setForeground(0, QBrush(self.classify_color(cid)))

        # recurse
        for ch in node.children:
            item.addChild(self.make_tree_item(ch))

        return item

    # ----------------------------------------------------------------------

    def on_tree_select(self):
        sel = self.tree.selectedItems()
        if not sel or not self.file_data:
            return

        item = sel[0]
        node = item.data(0, Qt.UserRole)
        if not isinstance(node, ChunkNode):
            return

        region = self.file_data[node.start:node.start + node.size]
        self.hex_view.setPlainText(self.format_hex(region))

        payload = self.file_data[node.payload_start:node.payload_end]
        guess = guess_payload_type(payload)

        # classify
        if node.cid in CHUNK_NAMES_3DS:
            group = "3DS Standard"
        elif node.cid in CHUNK_NAMES_I3D:
            group = "I3D Extension"
        else:
            group = "Unknown"

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
            preview = payload.decode("ascii", "replace")
            info.append("\nASCII Preview:\n" + preview[:300])

        self.info_view.setPlainText("\n".join(info))

        interp = self.decoder.decode(node, payload)
        self.interpret_view.setPlainText(interp)

    # ----------------------------------------------------------------------

    def show_discovery_dialog(self):
        dlg = DiscoveryDialog(self)

        # fill unknown entries
        populate_runtime_unknowns(self.root_node, self.file_data)

        dlg.populate(unknown_chunks)
        dlg.exec_()

    # ----------------------------------------------------------------------

    @staticmethod
    def format_hex(data: bytes, width=16) -> str:
        out = []
        for i in range(0, len(data), width):
            blk = data[i:i+width]
            hexp = " ".join(f"{b:02X}" for b in blk)
            asc = "".join(chr(b) if 32 <= b < 127 else "." for b in blk)
            out.append(f"{i:08X}  {hexp:<{width*3}}  {asc}")
        return "\n".join(out)


# ============================================================================
# Runtime Unknown Collector + Main Entry Point
# ============================================================================

def populate_runtime_unknowns(root: ChunkNode, data: bytes):
    """
    Traverses the tree and registers unknown chunks in the runtime registry.
    """
    unknown_chunks.clear()

    def walk(n: ChunkNode):
        if n.cid != 0xFFFF:  # skip artificial root
            payload = data[n.payload_start:n.payload_end]
            guess = guess_payload_type(payload)

            # unknown = not in 3DS, not in I3D
            if n.cid not in CHUNK_NAMES_3DS and n.cid not in CHUNK_NAMES_I3D:
                parent = n.parent.cid if n.parent else None
                register_unknown(n.cid, parent, payload, guess)

        for ch in n.children:
            walk(ch)

    walk(root)


# ============================================================================
# Run GUI
# ============================================================================

def main():
    import sys
    app = QApplication(sys.argv)
    w = ChunkExplorerWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

