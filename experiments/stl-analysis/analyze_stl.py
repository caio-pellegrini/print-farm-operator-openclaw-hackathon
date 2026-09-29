#!/usr/bin/env python3
"""Small standard-library STL inspector. Assumes coordinates are millimetres."""
import argparse
import json
import math
import re
import struct
from collections import Counter
from pathlib import Path


def load_vertices(path):
    data = path.read_bytes()
    if len(data) >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if 84 + count * 50 == len(data):
            vertices = []
            for i in range(count):
                vals = struct.unpack_from("<12fH", data, 84 + i * 50)
                vertices.extend((vals[3:6], vals[6:9], vals[9:12]))
            return vertices, count, "binary"
    text = data.decode("utf-8", errors="strict")
    vertices = [tuple(map(float, xyz)) for xyz in re.findall(
        r"\bvertex\s+([-+\deE.]+)\s+([-+\deE.]+)\s+([-+\deE.]+)", text, re.I)]
    if not vertices or len(vertices) % 3:
        raise ValueError("Input is not a valid ASCII/binary STL triangle stream")
    return vertices, len(vertices) // 3, "ascii"


def inspect(path):
    verts, triangle_count, encoding = load_vertices(path)
    mins = [min(v[i] for v in verts) for i in range(3)]
    maxs = [max(v[i] for v in verts) for i in range(3)]
    dims = [maxs[i] - mins[i] for i in range(3)]
    # Exact (rounded) shared-vertex edge counting; STL has no explicit topology.
    keys = [tuple(round(c, 5) for c in v) for v in verts]
    edges = Counter()
    parents = list(range(triangle_count))
    def find(x):
        while parents[x] != x:
            parents[x] = parents[parents[x]]
            x = parents[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parents[rb] = ra
    first_face_for_vertex = {}
    for i in range(0, len(keys), 3):
        face = i // 3
        tri = keys[i:i + 3]
        for vertex in tri:
            if vertex in first_face_for_vertex:
                union(face, first_face_for_vertex[vertex])
            else:
                first_face_for_vertex[vertex] = face
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            edges[tuple(sorted((a, b)))] += 1
    components = len({find(face) for face in range(triangle_count)})
    watertight = bool(edges) and all(n == 2 for n in edges.values())
    signed6 = 0.0
    for i in range(0, len(verts), 3):
        a, b, c = verts[i:i + 3]
        signed6 += (a[0] * (b[1]*c[2] - b[2]*c[1])
                    - a[1] * (b[0]*c[2] - b[2]*c[0])
                    + a[2] * (b[0]*c[1] - b[1]*c[0]))
    volume = abs(signed6 / 6.0) if watertight else None
    return {
        "file": str(path), "format": encoding, "triangle_count": triangle_count,
        "connected_components_by_shared_vertices": components,
        "dimensions_mm": dict(zip("xyz", (round(n, 3) for n in dims))),
        "bounds_mm": {"min": mins, "max": maxs},
        "volume_cm3": round(volume / 1000, 4) if volume is not None else None,
        "watertight_heuristic": watertight,
        "boundary_or_nonmanifold_edges": sum(n != 2 for n in edges.values()),
        "fits_example_220x220x250mm": all(dims[i] <= (220, 220, 250)[i] for i in range(3)),
        "orientation": "STL has coordinates but no reliable print orientation metadata",
        "units_assumption": "millimetres; STL itself does not encode units",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("file", type=Path)
    p.add_argument("--json", type=Path)
    args = p.parse_args()
    result = inspect(args.file)
    dumped = json.dumps(result, indent=2)
    if args.json:
        args.json.write_text(dumped + "\n")
    print(dumped)


if __name__ == "__main__":
    main()
