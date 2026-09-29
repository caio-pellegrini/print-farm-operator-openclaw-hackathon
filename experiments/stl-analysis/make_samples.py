#!/usr/bin/env python3
"""Generate deterministic binary STL sample solids for local experiments."""
import math
import struct
from pathlib import Path

OUT = Path(__file__).with_name("samples")


def write_stl(name, triangles):
    with (OUT / name).open("wb") as f:
        f.write((b"synthetic sample; mm" + b" " * 60)[:80])
        f.write(struct.pack("<I", len(triangles)))
        for tri in triangles:
            a, b, c = tri
            ux, uy, uz = (b[i] - a[i] for i in range(3))
            vx, vy, vz = (c[i] - a[i] for i in range(3))
            n = (uy*vz-uz*vy, uz*vx-ux*vz, ux*vy-uy*vx)
            mag = math.sqrt(sum(q*q for q in n)) or 1
            f.write(struct.pack("<12fH", *(q/mag for q in n), *a, *b, *c, 0))


def box(x0, y0, z0, x1, y1, z1):
    p = [(x0,y0,z0),(x1,y0,z0),(x1,y1,z0),(x0,y1,z0),
         (x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1)]
    faces = [(0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),
             (1,2,6),(1,6,5),(2,3,7),(2,7,6),(3,0,4),(3,4,7)]
    return [tuple(p[i] for i in face) for face in faces]


def cylinder(radius, height, segments=48):
    tri = []
    bottom, top = (0,0,0), (0,0,height)
    for i in range(segments):
        a, b = 2*math.pi*i/segments, 2*math.pi*(i+1)/segments
        p0, p1 = (radius*math.cos(a), radius*math.sin(a), 0), (radius*math.cos(b), radius*math.sin(b), 0)
        q0, q1 = (p0[0],p0[1],height), (p1[0],p1[1],height)
        tri.extend([(p0,p1,q1),(p0,q1,q0),(bottom,p1,p0),(top,q0,q1)])
    return tri


OUT.mkdir(exist_ok=True)
write_stl("small-box-20mm.stl", box(0,0,0,20,20,12))
write_stl("medium-cylinder-60x40mm.stl", cylinder(30,40))
write_stl("complex-stepped-block.stl", box(0,0,0,60,25,8) + box(0,0,8,20,25,38) + box(40,0,8,60,25,38))
write_stl("two-separated-boxes.stl", box(0,0,0,20,20,10) + box(40,0,0,60,20,10))
