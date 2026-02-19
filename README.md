<p align="center">
  <h1 align="center">BPMN to Visio (.vsdx) Converter</h1>
  <p align="center">
    Convert BPMN 2.0 diagrams to Microsoft Visio files — zero dependencies, pure Python.
  </p>
  <p align="center">
    <a href="https://pypi.org/project/bpmn-to-visio/"><img src="https://img.shields.io/pypi/v/bpmn-to-visio?color=blue" alt="PyPI version"></a>
    <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/pypi/pyversions/bpmn-to-visio" alt="Python 3.7+"></a>
    <a href="LICENSE"><img src="https://img.shields.io/github/license/Mgabr90/bpmn-to-visio" alt="MIT License"></a>
    <a href="https://github.com/Mgabr90/bpmn-to-visio/stargazers"><img src="https://img.shields.io/github/stars/Mgabr90/bpmn-to-visio?style=social" alt="GitHub stars"></a>
  </p>
</p>

---

**bpmn-to-visio** converts BPMN 2.0 XML files (from [bpmn.io](https://bpmn.io), Camunda Modeler, Signavio, etc.) into Microsoft Visio `.vsdx` files — preserving the exact layout, shapes, and styling from your BPMN modeler.

No Visio installation required. No external dependencies. Just Python 3.7+.

```
BPMN 2.0 XML  ──►  Visio .vsdx
   (.bpmn)          (Open XML)
```

## Why?

- Your team uses **bpmn.io** or **Camunda Modeler** for process modeling, but stakeholders need **Visio** files
- You have **dozens or hundreds** of BPMN diagrams to deliver in Visio format
- Manual recreation in Visio is **slow, error-prone, and doesn't scale**
- Existing tools require paid licenses or don't preserve layout

This converter solves all of that with a single Python script.

## Features

- **Zero dependencies** — uses only Python standard library (no pip packages needed)
- **Layout preservation** — reads BPMN diagram coordinates to reproduce exact positions
- **Full BPMN support** — pools, lanes, tasks, events, gateways, sequence flows, message flows, annotations
- **Color preservation** — reads `bioc:fill` / `bioc:stroke` attributes from bpmn.io
- **Batch conversion** — convert entire folders of BPMN files in one command
- **Visio Desktop compatible** — outputs valid VSDX Open XML packages with proper text rendering

## Supported BPMN Elements

| BPMN Element | Visio Shape |
|---|---|
| Start Event | Green circle |
| End Event | Red bold circle |
| Intermediate Events (Timer, Message, Signal) | Orange circle |
| Task / User Task / Service Task | Rounded rectangle |
| Sub-Process / Call Activity | Rounded rectangle |
| Exclusive Gateway | Diamond with "X" |
| Parallel Gateway | Diamond with "+" |
| Inclusive Gateway | Diamond with "O" |
| Event-Based Gateway | Diamond |
| Pool (Participant) | Rectangle with vertical header band |
| Lane | Rectangle with vertical header band |
| Text Annotation | Open bracket with text |
| Sequence Flow | Solid arrow |
| Message Flow | Dashed arrow |
| Association | Dotted line |

## Installation

### Option 1: pip (recommended)

```bash
pip install bpmn-to-visio
```

### Option 2: Clone the repo

```bash
git clone https://github.com/Mgabr90/bpmn-to-visio.git
cd bpmn-to-visio
```

Python 3.7+ is required. No additional packages needed.

## Usage

### Single file

```bash
bpmn-to-visio diagram.bpmn
```

Or if running from source:

```bash
python bpmn_to_vsdx.py diagram.bpmn
```

Output: `diagram.vsdx` in the same directory.

### Custom output directory

```bash
bpmn-to-visio diagram.bpmn -o output/
```

### Batch conversion

Convert all `.bpmn` files in a folder (recursively):

```bash
bpmn-to-visio --batch ./bpmn-files/
```

Output `.vsdx` files are placed next to each `.bpmn` source, or in the directory specified by `-o`.

### Python API

```python
from bpmn_to_vsdx import convert_bpmn_to_vsdx

convert_bpmn_to_vsdx("process.bpmn", output_dir="output/")
```

## How It Works

1. **Parse** — Extracts BPMN elements, flows, and diagram coordinates from the XML
2. **Transform** — Converts BPMN coordinates (top-left origin, pixels at 96 PPI) to Visio coordinates (bottom-left origin, inches)
3. **Generate** — Builds the VSDX Open XML package (ZIP of XML files) with shapes, connectors, and styling

The converter reads `bpmndi:BPMNShape` bounds and `bpmndi:BPMNEdge` waypoints to preserve the exact layout from your BPMN modeler.

## Compatibility

| BPMN Source | Status |
|---|---|
| [bpmn.io](https://bpmn.io) | Fully supported |
| Camunda Modeler | Fully supported |
| Signavio | Supported |
| Bizagi Modeler | Supported (BPMN 2.0 export) |
| Any BPMN 2.0 compliant tool | Supported |

| Visio Target | Status |
|---|---|
| Visio Desktop (2016+) | Fully supported |
| Visio Online / Web | Supported |
| LibreOffice Draw (.vsdx import) | Basic support |

## Limitations

- Intermediate events render as single circle (no double-circle border)
- No task-type icons (user task, service task, etc. render as plain rounded rectangles)
- Message flow source end lacks open-circle marker
- No support for collapsed sub-processes or event sub-processes
- Groups and data objects are not rendered

## Contributing

Contributions are welcome! Please open an issue or pull request.

## License

[MIT](LICENSE) — Mahmoud Gabr
