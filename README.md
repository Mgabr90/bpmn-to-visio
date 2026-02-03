# BPMN to Visio (.vsdx) Converter

Convert BPMN 2.0 XML files to Microsoft Visio (.vsdx) format, preserving layout coordinates from the BPMN diagram.

## Features

- **Zero dependencies** — uses only Python standard library (no `python-vsdx` or other packages needed)
- Generates valid VSDX Open XML packages directly
- Preserves BPMN diagram coordinates (from bpmn.io, Camunda Modeler, etc.)
- Supports pools, lanes, tasks, events, gateways, sequence flows, message flows, and text annotations
- Preserves shape colors from `bioc:fill` / `bioc:stroke` attributes
- Handles horizontal pools with header bands and vertical text
- Connector routing with orthogonal waypoints
- Batch conversion of entire folders

## Supported BPMN Elements

| BPMN Element | Visio Shape |
|---|---|
| Start Event | Green circle |
| End Event | Red bold circle |
| Intermediate Events | Orange circle |
| Task / User Task / Service Task | Rounded rectangle |
| Sub-Process / Call Activity | Rounded rectangle |
| Exclusive Gateway | Diamond with "X" |
| Parallel Gateway | Diamond with "+" |
| Inclusive Gateway | Diamond with "O" |
| Event-Based Gateway | Diamond |
| Pool (Participant) | Rectangle with header band |
| Lane | Rectangle with header band |
| Text Annotation | Open bracket with text |
| Sequence Flow | Solid arrow |
| Message Flow | Dashed arrow |
| Association | Dotted line |

## Installation

No installation needed. Just download the script:

```bash
git clone https://github.com/Mgabr90/bpmn-to-visio.git
cd bpmn-to-visio
```

Python 3.7+ is required. No pip packages needed.

## Usage

### Single file

```bash
python bpmn_to_vsdx.py diagram.bpmn
```

Output: `diagram.vsdx` in the same directory.

### Custom output directory

```bash
python bpmn_to_vsdx.py diagram.bpmn -o output/
```

### Batch conversion

Convert all `.bpmn` files in a folder (recursively):

```bash
python bpmn_to_vsdx.py --batch ./bpmn-files/
```

Output `.vsdx` files are placed next to each `.bpmn` source, or in the directory specified by `-o`.

## How It Works

1. **Parse** — Extracts BPMN elements, sequence flows, message flows, and diagram coordinates from the XML
2. **Transform** — Converts BPMN coordinates (top-left origin, pixels at 96 PPI) to Visio coordinates (bottom-left origin, inches)
3. **Generate** — Builds the VSDX Open XML package (ZIP of XML files) with shapes, connectors, and styling

The converter reads `bpmndi:BPMNShape` bounds and `bpmndi:BPMNEdge` waypoints to preserve the exact layout from your BPMN modeler.

## Limitations

- Intermediate events render as single circle (no double-circle border)
- No task-type icons (user task, service task, etc. all render as plain rounded rectangles)
- Message flow source end lacks open-circle marker
- No support for collapsed sub-processes or event sub-processes
- Groups and data objects are not rendered

## License

MIT
