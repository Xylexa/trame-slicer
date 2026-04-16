# trame-slicer (Spike Fork)

## Context

This repository is being used as a browser delivery spike for CT study workflows.

`trame-slicer` wraps Slicer's Python runtime (MRML, VTK, scripted modules) and serves
interactive viewports through a trame/Vue web stack with server-side rendering.
The expected execution model is one backend process per user session.

## Immediate Goal (Throwaway Spike)

1. Pull upstream demo code.
2. Run the web application.
3. Load a CT study.
4. Confirm 3D viewport rendering in browser.
5. Check whether SlicerVMTK / ExtractCenterline is available.

If `SlicerVMTK` is missing, centerline extraction is a blocker.

## Scope

In scope:

- Web proof-of-concept using `trame-slicer`
- Mount existing scripted modules and centerline helper scripts (minimal rewrites)
- Lightweight FastAPI sidecar for:
  - auth
  - DICOM upload
  - segmentation persistence

Out of scope:

- Replacing desktop Slicer
- Multi-tenancy/hard isolation design
- Full feature parity with desktop app

## Architecture (With Auth)

```text
Browser (Vue/trame client)
  -> Auth (FastAPI sidecar)
  -> trame-slicer session backend (Slicer Python core + MRML/VTK)
  -> Optional storage services (DICOM/SEG persistence)
```

Notes:

- Session backend owns MRML scene lifecycle.
- Frontend interactions are state/event synchronized over websocket.
- Rendering is server-driven; browser receives frames + interaction state.

## Features/Extensions to Port

- Segmentation editing
- Centerline extraction
- VMTK library usage
- CPR view extension

## Porting Difficulty (Practical)

Easy:

- Script-only utilities
- MRML operations
- Segmentation logic when dependencies already exist in runtime

Medium:

- Python modules requiring light UI rewrite into trame components/state

Hard:

- VMTK/CPR-style extensions depending on:
  - full Slicer extension packaging
  - compiled binaries
  - desktop-only module/widget APIs

Bottom line:

- Extension **features** can often be ported.
- Extension **drop-in compatibility** is typically not available in this runtime.

## Startup Instructions (Fresh Clone)

```bash
git clone https://github.com/Xylexa/trame-slicer
cd trame-slicer
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e '.[standalone]'
python examples/medical_viewer_app.py --server --host 0.0.0.0 -p 8090
```

Open:

- Local: `http://localhost:8090`
- LAN: `http://<server-ip>:8090`

## Spike Validation Checklist

1. App starts and web UI loads.
2. CT study loads in browser.
3. 2D slice views render and respond to interaction.
4. 3D viewport renders and responds to camera interaction.
5. Segmentation overlays are visible in 2D/3D.
6. Confirm ExtractCenterline/VMTK availability:

```bash
source .venv/bin/activate
python - <<'PY'
import slicer
print('extractcenterline module:', hasattr(slicer.modules, 'extractcenterline'))
PY
```

## Current Fork Notes

- Mixed upload flow supports volume + segmentation loading.
- DICOM SEG (`segmentation.dcm`) import path reconstructs per-segment masks and
  applies segment labels/colors from DICOM segment metadata.
- Segment editor binding prefers non-empty imported segmentation nodes.


