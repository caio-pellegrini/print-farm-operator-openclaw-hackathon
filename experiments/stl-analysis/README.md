# STL inspection POC

Generate test assets and analyze a local STL:

```sh
python3 experiments/stl-analysis/make_samples.py
python3 experiments/stl-analysis/analyze_stl.py experiments/stl-analysis/samples/small-box-20mm.stl --json experiments/stl-analysis/small-box.json
```

Uses only Python standard library. Assumes STL coordinates are mm (STL does not
encode units). Reports bounds/dimensions, triangle count, signed tetrahedral
volume when an edge-count heuristic indicates watertightness, and an example
220×220×250 mm volume check. It does not find connected components, choose print
orientation, repair mesh, or guarantee slicer acceptance. The stepped fixture is
deliberately three overlapping closed shells; the basic checker flags its shared
interior edges and withholds volume instead of claiming an accurate net union.
