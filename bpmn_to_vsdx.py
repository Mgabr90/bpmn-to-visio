"""
BPMN 2.0 to Visio (.vsdx) Converter

Converts BPMN XML files to Visio .vsdx format, preserving layout coordinates.
Generates the VSDX Open XML package directly (no external dependencies).

Usage:
    python bpmn_to_vsdx.py <input.bpmn>                    # Single file
    python bpmn_to_vsdx.py --batch <folder>                 # All .bpmn in folder
    python bpmn_to_vsdx.py <input.bpmn> -o <output_dir>    # Custom output dir
"""
import argparse
import math
import os
import re
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET


# Element types we extract from BPMN
SHAPE_TYPES = {
    'startEvent', 'endEvent',
    'task', 'userTask', 'serviceTask', 'scriptTask', 'sendTask', 'receiveTask',
    'manualTask', 'businessRuleTask', 'subProcess', 'callActivity',
    'exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'eventBasedGateway',
    'intermediateCatchEvent', 'intermediateThrowEvent',
    'boundaryEvent',
    'textAnnotation',
}

TASK_TYPES = {
    'task', 'userTask', 'serviceTask', 'scriptTask', 'sendTask', 'receiveTask',
    'manualTask', 'businessRuleTask', 'subProcess', 'callActivity',
}

GATEWAY_TYPES = {
    'exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'eventBasedGateway',
}

EVENT_START = {'startEvent'}
EVENT_END = {'endEvent'}
EVENT_INTERMEDIATE = {'intermediateCatchEvent', 'intermediateThrowEvent', 'boundaryEvent'}

# Pixels per inch for coordinate conversion
PPI = 96.0

# ── Connector Style Constants ────────────────────────────────────────────────
CONNECTOR_COLOR = '#555555'         # Sequence flow line color
CONNECTOR_WEIGHT = '0.02'          # Line weight in inches
CONNECTOR_LABEL_COLOR = '#333333'  # Label text color for sequence flows
CONNECTOR_LABEL_SIZE = 7           # Label font size in points
CONNECTOR_ROUNDING = '0.15'        # Corner rounding radius in inches
MSG_FLOW_COLOR = '#555555'         # Message flow line color
MSG_FLOW_LABEL_COLOR = '#555555'   # Label text color for message flows
ARROW_LENGTH = 0.12                # Arrowhead length in inches
ARROW_WIDTH_RATIO = 0.35           # Arrowhead half-width as ratio of length


# ── BPMN Parser ──────────────────────────────────────────────────────────────

def parse_bpmn(bpmn_path):
    """Parse BPMN XML and extract elements, flows, and diagram coordinates."""
    tree = ET.parse(bpmn_path)
    root = tree.getroot()

    elements = {}   # id -> {type, name}
    flows = []      # [{id, sourceRef, targetRef, name}]
    shapes = {}     # bpmn_element_id -> {x, y, w, h}
    edges = {}      # bpmn_element_id -> [{x, y}, ...]

    # Track participant → process → lanes hierarchy
    participant_process = {}  # participant_id -> processRef
    process_lanes = {}        # processRef -> [lane_id, ...]

    # Pass 1: Extract process elements and hierarchy
    for elem in root.iter():
        local_tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag

        if local_tag in SHAPE_TYPES:
            elem_id = elem.get('id')
            elem_name = elem.get('name', '')
            event_def = ''
            if local_tag == 'textAnnotation' and not elem_name:
                # textAnnotation stores text in a child <text> element
                for child in elem:
                    child_tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                    if child_tag == 'text' and child.text:
                        elem_name = child.text
                        break
            # Capture event definition type for intermediate events
            if local_tag in ('intermediateCatchEvent', 'intermediateThrowEvent',
                             'startEvent', 'endEvent', 'boundaryEvent'):
                for child in elem:
                    child_tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                    if child_tag.endswith('EventDefinition'):
                        event_def = child_tag  # e.g., messageEventDefinition, timerEventDefinition
                        break
            if elem_id:
                elem_data = {'type': local_tag, 'name': elem_name}
                if event_def:
                    elem_data['event_def'] = event_def
                elements[elem_id] = elem_data

        elif local_tag in ('sequenceFlow', 'messageFlow', 'association'):
            flow_id = elem.get('id')
            source = elem.get('sourceRef')
            target = elem.get('targetRef')
            name = elem.get('name', '')
            if flow_id and source and target:
                flows.append({'id': flow_id, 'sourceRef': source, 'targetRef': target,
                              'name': name, 'type': local_tag})

        elif local_tag == 'participant':
            elem_id = elem.get('id')
            elem_name = elem.get('name', '')
            process_ref = elem.get('processRef', '')
            if elem_id:
                elements[elem_id] = {'type': local_tag, 'name': elem_name}
                if process_ref:
                    participant_process[elem_id] = process_ref

        elif local_tag == 'lane':
            elem_id = elem.get('id')
            elem_name = elem.get('name', '')
            if elem_id:
                elements[elem_id] = {'type': local_tag, 'name': elem_name}

        elif local_tag == 'laneSet':
            # Find the parent process to map lanes to it
            pass  # lanes are collected below

    # Collect lanes per process by walking the tree structure
    for elem in root.iter():
        local_tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
        if local_tag == 'process':
            proc_id = elem.get('id', '')
            lanes = []
            for child in elem.iter():
                child_tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                if child_tag == 'lane':
                    lane_id = child.get('id')
                    if lane_id:
                        lanes.append(lane_id)
            if lanes:
                process_lanes[proc_id] = lanes

    # Build participant → lanes mapping
    participant_lanes = {}  # participant_id -> [lane_id, ...]
    for part_id, proc_ref in participant_process.items():
        if proc_ref in process_lanes:
            participant_lanes[part_id] = process_lanes[proc_ref]

    # Pass 2: Extract diagram coordinates
    for elem in root.iter():
        local_tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag

        if local_tag == 'BPMNShape':
            bpmn_element = elem.get('bpmnElement')
            is_horiz_attr = elem.get('isHorizontal', '')
            is_horizontal = is_horiz_attr.lower() == 'true' if is_horiz_attr else None
            bounds = None
            label_bounds = None
            for child in elem:
                child_tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                if child_tag == 'Bounds':
                    bounds = child
                elif child_tag == 'BPMNLabel':
                    # Look for dc:Bounds inside BPMNLabel
                    for lbl_child in child:
                        lbl_tag = lbl_child.tag.split('}')[-1] if '}' in lbl_child.tag else lbl_child.tag
                        if lbl_tag == 'Bounds':
                            label_bounds = lbl_child
                            break
            if bpmn_element and bounds is not None:
                shape_data = {
                    'x': float(bounds.get('x', 0)),
                    'y': float(bounds.get('y', 0)),
                    'w': float(bounds.get('width', 100)),
                    'h': float(bounds.get('height', 80)),
                }
                if is_horizontal is not None:
                    shape_data['is_horizontal'] = is_horizontal
                # Extract BPMNLabel position (absolute coords in BPMN space)
                if label_bounds is not None:
                    shape_data['label_x'] = float(label_bounds.get('x', 0))
                    shape_data['label_y'] = float(label_bounds.get('y', 0))
                    shape_data['label_w'] = float(label_bounds.get('width', 80))
                    shape_data['label_h'] = float(label_bounds.get('height', 27))
                # Extract BPMN color attributes (bioc:fill, bioc:stroke)
                # These appear as namespaced attributes on BPMNShape elements
                for attr_name, attr_val in elem.attrib.items():
                    local_attr = attr_name.split('}')[-1] if '}' in attr_name else attr_name
                    if local_attr == 'fill' and 'bioc' in attr_name:
                        shape_data['fill_color'] = attr_val
                    elif local_attr == 'stroke' and 'bioc' in attr_name:
                        shape_data['stroke_color'] = attr_val
                    elif local_attr == 'background-color' and 'color' in attr_name:
                        shape_data.setdefault('fill_color', attr_val)
                    elif local_attr == 'border-color' and 'color' in attr_name:
                        shape_data.setdefault('stroke_color', attr_val)
                shapes[bpmn_element] = shape_data

        elif local_tag == 'BPMNEdge':
            bpmn_element = elem.get('bpmnElement')
            waypoints = []
            label_bounds = None
            for child in elem:
                child_tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                if child_tag == 'waypoint':
                    waypoints.append({
                        'x': float(child.get('x', 0)),
                        'y': float(child.get('y', 0)),
                    })
                elif child_tag == 'BPMNLabel':
                    for lbl_child in child:
                        lbl_tag = lbl_child.tag.split('}')[-1] if '}' in lbl_child.tag else lbl_child.tag
                        if lbl_tag == 'Bounds':
                            label_bounds = {
                                'x': float(lbl_child.get('x', 0)),
                                'y': float(lbl_child.get('y', 0)),
                                'w': float(lbl_child.get('width', 40)),
                                'h': float(lbl_child.get('height', 14)),
                            }
                            break
            if bpmn_element and waypoints:
                edges[bpmn_element] = {'waypoints': waypoints}
                if label_bounds:
                    edges[bpmn_element]['label'] = label_bounds

    return elements, flows, shapes, edges, participant_lanes


def get_element_category(elem_type):
    """Return shape category for a BPMN element type."""
    if elem_type in EVENT_START:
        return 'start_event'
    elif elem_type in EVENT_END:
        return 'end_event'
    elif elem_type in EVENT_INTERMEDIATE:
        return 'intermediate_event'
    elif elem_type in GATEWAY_TYPES:
        return 'gateway'
    elif elem_type in ('participant',):
        return 'participant'
    elif elem_type in ('lane',):
        return 'lane'
    elif elem_type == 'textAnnotation':
        return 'annotation'
    elif elem_type in TASK_TYPES:
        return 'task'
    return 'task'


# ── VSDX Generator ───────────────────────────────────────────────────────────
# Generate .vsdx directly by building the Open XML Package (ZIP of XML files)

def _r(val):
    """Round a float to 4 decimal places for clean XML output."""
    return round(val, 4)


def compute_bounds(shapes, edges):
    """Compute bounding box of all BPMN coordinates in pixels.
    Returns (min_x, min_y, max_x, max_y)."""
    if not shapes and not edges:
        return 0, 0, 1056, 816  # default 11x8.5 inches

    all_x = []
    all_y = []
    for s in shapes.values():
        all_x.extend([s['x'], s['x'] + s['w']])
        all_y.extend([s['y'], s['y'] + s['h']])
    for edge_data in edges.values():
        for wp in edge_data['waypoints']:
            all_x.append(wp['x'])
            all_y.append(wp['y'])

    return min(all_x), min(all_y), max(all_x), max(all_y)


def compute_page_size(min_x, min_y, max_x, max_y):
    """Compute page size in inches from BPMN coordinate bounds.
    The offset (min_x, min_y) is applied so all coords become non-negative."""
    margin = 50  # pixels margin on each side
    total_w = (max_x - min_x) + 2 * margin
    total_h = (max_y - min_y) + 2 * margin

    page_w = total_w / PPI
    page_h = total_h / PPI
    # Minimum page size
    page_w = max(page_w, 11.0)
    page_h = max(page_h, 8.5)
    return _r(page_w), _r(page_h)


def bpmn_to_visio_coords(bpmn_x, bpmn_y, bpmn_w, bpmn_h, page_h, offset_x, offset_y):
    """Convert BPMN coordinates (top-left origin, pixels) to Visio (bottom-left origin, inches).
    offset_x/offset_y shift all coordinates so the minimum becomes the margin.
    Returns center point (PinX, PinY) and size (Width, Height)."""
    w = bpmn_w / PPI
    h = bpmn_h / PPI
    pin_x = (bpmn_x - offset_x + bpmn_w / 2) / PPI
    pin_y = page_h - (bpmn_y - offset_y + bpmn_h / 2) / PPI
    return pin_x, pin_y, w, h


def wp_to_visio(x, y, page_h, offset_x, offset_y):
    """Convert a single waypoint to Visio coordinates."""
    return (x - offset_x) / PPI, page_h - (y - offset_y) / PPI


def _marker_geometry_xml(elem_type, w_in, h_in):
    """Return additional Geometry section(s) for BPMN markers inside shapes.

    Gateway markers: X (exclusive), + (parallel), O (inclusive)
    Event markers: envelope (message), clock (timer), signal, etc.
    Returns geometry section XML string at IX=1 (shape body is IX=0)."""
    hw = _r(w_in / 2)
    hh = _r(h_in / 2)
    # Marker size relative to shape
    ms = min(w_in, h_in) * 0.3  # marker scale

    if elem_type == 'exclusiveGateway':
        # X marker inside diamond
        cx, cy = hw, hh
        d = _r(ms * 0.7)
        return f'''<Section N="Geometry" IX="1">
<Cell N="NoFill" V="1"/>
<Cell N="NoLine" V="0"/>
<Row T="MoveTo" IX="1"><Cell N="X" V="{_r(cx - d)}"/><Cell N="Y" V="{_r(cy - d)}"/></Row>
<Row T="LineTo" IX="2"><Cell N="X" V="{_r(cx + d)}"/><Cell N="Y" V="{_r(cy + d)}"/></Row>
<Row T="MoveTo" IX="3"><Cell N="X" V="{_r(cx + d)}"/><Cell N="Y" V="{_r(cy - d)}"/></Row>
<Row T="LineTo" IX="4"><Cell N="X" V="{_r(cx - d)}"/><Cell N="Y" V="{_r(cy + d)}"/></Row>
</Section>'''

    elif elem_type == 'parallelGateway':
        # + marker inside diamond
        cx, cy = hw, hh
        d = _r(ms * 0.8)
        return f'''<Section N="Geometry" IX="1">
<Cell N="NoFill" V="1"/>
<Cell N="NoLine" V="0"/>
<Row T="MoveTo" IX="1"><Cell N="X" V="{_r(cx)}"/><Cell N="Y" V="{_r(cy - d)}"/></Row>
<Row T="LineTo" IX="2"><Cell N="X" V="{_r(cx)}"/><Cell N="Y" V="{_r(cy + d)}"/></Row>
<Row T="MoveTo" IX="3"><Cell N="X" V="{_r(cx - d)}"/><Cell N="Y" V="{_r(cy)}"/></Row>
<Row T="LineTo" IX="4"><Cell N="X" V="{_r(cx + d)}"/><Cell N="Y" V="{_r(cy)}"/></Row>
</Section>'''

    elif elem_type == 'inclusiveGateway':
        # O marker (circle) inside diamond — approximate with octagon
        cx, cy = hw, hh
        r = ms * 0.6
        rows = ''
        n_pts = 12
        for i in range(n_pts + 1):
            angle = 2 * math.pi * (i % n_pts) / n_pts
            px = _r(cx + r * math.cos(angle))
            py = _r(cy + r * math.sin(angle))
            tag = 'MoveTo' if i == 0 else 'LineTo'
            rows += f'<Row T="{tag}" IX="{i + 1}"><Cell N="X" V="{px}"/><Cell N="Y" V="{py}"/></Row>\n'
        return f'''<Section N="Geometry" IX="1">
<Cell N="NoFill" V="1"/>
<Cell N="NoLine" V="0"/>
{rows}</Section>'''

    elif elem_type == 'eventBasedGateway':
        # Pentagon inside diamond
        cx, cy = hw, hh
        r = ms * 0.6
        rows = ''
        for i in range(6):
            angle = 2 * math.pi * (i % 5) / 5 - math.pi / 2
            px = _r(cx + r * math.cos(angle))
            py = _r(cy + r * math.sin(angle))
            tag = 'MoveTo' if i == 0 else 'LineTo'
            rows += f'<Row T="{tag}" IX="{i + 1}"><Cell N="X" V="{px}"/><Cell N="Y" V="{py}"/></Row>\n'
        return f'''<Section N="Geometry" IX="1">
<Cell N="NoFill" V="1"/>
<Cell N="NoLine" V="0"/>
{rows}</Section>'''

    return ''


def _event_marker_geometry_xml(event_def, w_in, h_in):
    """Return Geometry section for event definition markers (envelope, timer, etc.)."""
    hw = _r(w_in / 2)
    hh = _r(h_in / 2)
    ms = min(w_in, h_in) * 0.25

    if event_def == 'messageEventDefinition':
        # Envelope marker: rectangle + V-shape for flap
        cx, cy = float(hw), float(hh)
        ew = ms * 1.2  # envelope width
        eh = ms * 0.8  # envelope height
        l, r_x = _r(cx - ew), _r(cx + ew)
        b, t = _r(cy - eh), _r(cy + eh)
        mid_x = hw
        mid_y = _r(cy + eh * 0.3)
        return f'''<Section N="Geometry" IX="1">
<Cell N="NoFill" V="1"/>
<Cell N="NoLine" V="0"/>
<Row T="MoveTo" IX="1"><Cell N="X" V="{l}"/><Cell N="Y" V="{b}"/></Row>
<Row T="LineTo" IX="2"><Cell N="X" V="{r_x}"/><Cell N="Y" V="{b}"/></Row>
<Row T="LineTo" IX="3"><Cell N="X" V="{r_x}"/><Cell N="Y" V="{t}"/></Row>
<Row T="LineTo" IX="4"><Cell N="X" V="{l}"/><Cell N="Y" V="{t}"/></Row>
<Row T="LineTo" IX="5"><Cell N="X" V="{l}"/><Cell N="Y" V="{b}"/></Row>
</Section>
<Section N="Geometry" IX="2">
<Cell N="NoFill" V="1"/>
<Cell N="NoLine" V="0"/>
<Row T="MoveTo" IX="1"><Cell N="X" V="{l}"/><Cell N="Y" V="{t}"/></Row>
<Row T="LineTo" IX="2"><Cell N="X" V="{mid_x}"/><Cell N="Y" V="{mid_y}"/></Row>
<Row T="LineTo" IX="3"><Cell N="X" V="{r_x}"/><Cell N="Y" V="{t}"/></Row>
</Section>'''

    elif event_def == 'timerEventDefinition':
        # Clock marker: circle with hands
        cx, cy = float(hw), float(hh)
        r = ms * 0.8
        rows = ''
        n_pts = 12
        for i in range(n_pts + 1):
            angle = 2 * math.pi * (i % n_pts) / n_pts
            px = _r(cx + r * math.cos(angle))
            py = _r(cy + r * math.sin(angle))
            tag = 'MoveTo' if i == 0 else 'LineTo'
            rows += f'<Row T="{tag}" IX="{i + 1}"><Cell N="X" V="{px}"/><Cell N="Y" V="{py}"/></Row>\n'
        # Clock hands
        hand1_x = _r(cx + r * 0.5 * math.cos(math.pi / 3))
        hand1_y = _r(cy + r * 0.5 * math.sin(math.pi / 3))
        hand2_x = _r(cx)
        hand2_y = _r(cy + r * 0.7)
        return f'''<Section N="Geometry" IX="1">
<Cell N="NoFill" V="1"/>
<Cell N="NoLine" V="0"/>
{rows}</Section>
<Section N="Geometry" IX="2">
<Cell N="NoFill" V="1"/>
<Cell N="NoLine" V="0"/>
<Row T="MoveTo" IX="1"><Cell N="X" V="{hw}"/><Cell N="Y" V="{hh}"/></Row>
<Row T="LineTo" IX="2"><Cell N="X" V="{hand1_x}"/><Cell N="Y" V="{hand1_y}"/></Row>
<Row T="MoveTo" IX="3"><Cell N="X" V="{hw}"/><Cell N="Y" V="{hh}"/></Row>
<Row T="LineTo" IX="4"><Cell N="X" V="{hand2_x}"/><Cell N="Y" V="{hand2_y}"/></Row>
</Section>'''

    elif event_def == 'signalEventDefinition':
        # Triangle marker
        cx, cy = float(hw), float(hh)
        s = ms * 0.8
        return f'''<Section N="Geometry" IX="1">
<Cell N="NoFill" V="1"/>
<Cell N="NoLine" V="0"/>
<Row T="MoveTo" IX="1"><Cell N="X" V="{hw}"/><Cell N="Y" V="{_r(cy + s)}"/></Row>
<Row T="LineTo" IX="2"><Cell N="X" V="{_r(cx - s)}"/><Cell N="Y" V="{_r(cy - s * 0.6)}"/></Row>
<Row T="LineTo" IX="3"><Cell N="X" V="{_r(cx + s)}"/><Cell N="Y" V="{_r(cy - s * 0.6)}"/></Row>
<Row T="LineTo" IX="4"><Cell N="X" V="{hw}"/><Cell N="Y" V="{_r(cy + s)}"/></Row>
</Section>'''

    return ''


def _subprocess_marker_geometry_xml(w_in, h_in):
    """Return Geometry sections for a callActivity/subProcess [+] marker.

    Draws a small square with a plus sign centered at the bottom edge of the shape,
    following the BPMN convention for collapsed sub-processes and call activities."""
    cx = _r(w_in / 2)
    # Marker box size
    box = 0.12  # side length in inches
    hbox = box / 2
    # Position: centered horizontally, sitting on bottom edge with small margin
    margin_y = 0.04
    by = margin_y + hbox  # center Y of the marker box

    # Cross line size (slightly smaller than box)
    cross = hbox * 0.65

    # Geometry IX=1: square border
    sq = f'''<Section N="Geometry" IX="1">
<Cell N="NoFill" V="1"/>
<Cell N="NoLine" V="0"/>
<Row T="MoveTo" IX="1"><Cell N="X" V="{_r(cx - hbox)}"/><Cell N="Y" V="{_r(by - hbox)}"/></Row>
<Row T="LineTo" IX="2"><Cell N="X" V="{_r(cx + hbox)}"/><Cell N="Y" V="{_r(by - hbox)}"/></Row>
<Row T="LineTo" IX="3"><Cell N="X" V="{_r(cx + hbox)}"/><Cell N="Y" V="{_r(by + hbox)}"/></Row>
<Row T="LineTo" IX="4"><Cell N="X" V="{_r(cx - hbox)}"/><Cell N="Y" V="{_r(by + hbox)}"/></Row>
<Row T="LineTo" IX="5"><Cell N="X" V="{_r(cx - hbox)}"/><Cell N="Y" V="{_r(by - hbox)}"/></Row>
</Section>'''

    # Geometry IX=2: plus sign (+)
    plus = f'''<Section N="Geometry" IX="2">
<Cell N="NoFill" V="1"/>
<Cell N="NoLine" V="0"/>
<Row T="MoveTo" IX="1"><Cell N="X" V="{cx}"/><Cell N="Y" V="{_r(by - cross)}"/></Row>
<Row T="LineTo" IX="2"><Cell N="X" V="{cx}"/><Cell N="Y" V="{_r(by + cross)}"/></Row>
<Row T="MoveTo" IX="3"><Cell N="X" V="{_r(cx - cross)}"/><Cell N="Y" V="{_r(by)}"/></Row>
<Row T="LineTo" IX="4"><Cell N="X" V="{_r(cx + cross)}"/><Cell N="Y" V="{_r(by)}"/></Row>
</Section>'''

    return sq + '\n' + plus


def _shape_geometry_xml(category, w_in, h_in, header_width_in=0):
    """Return Visio Geometry Section XML for a shape category."""
    hw = _r(w_in / 2)
    hh = _r(h_in / 2)
    w_in = _r(w_in)
    h_in = _r(h_in)

    if category in ('start_event', 'end_event', 'intermediate_event'):
        # Ellipse (circle)
        return f'''<Section N="Geometry" IX="0">
<Cell N="NoFill" V="0"/>
<Cell N="NoLine" V="0"/>
<Row T="Ellipse" IX="1">
<Cell N="X" V="{hw}" F="Width*0.5"/>
<Cell N="Y" V="{hh}" F="Height*0.5"/>
<Cell N="A" V="{w_in}" F="Width*1"/>
<Cell N="B" V="{hh}" F="Height*0.5"/>
<Cell N="C" V="{hw}" F="Width*0.5"/>
<Cell N="D" V="{h_in}" F="Height*1"/>
</Row>
</Section>'''

    elif category == 'annotation':
        # Open bracket shape (left border only, like BPMN text annotation)
        return f'''<Section N="Geometry" IX="0">
<Cell N="NoFill" V="1"/>
<Cell N="NoLine" V="0"/>
<Row T="MoveTo" IX="1">
<Cell N="X" V="0.15"/>
<Cell N="Y" V="0"/>
</Row>
<Row T="LineTo" IX="2">
<Cell N="X" V="0"/>
<Cell N="Y" V="0"/>
</Row>
<Row T="LineTo" IX="3">
<Cell N="X" V="0"/>
<Cell N="Y" V="{h_in}"/>
</Row>
<Row T="LineTo" IX="4">
<Cell N="X" V="0.15"/>
<Cell N="Y" V="{h_in}"/>
</Row>
</Section>'''

    elif category in ('participant', 'lane'):
        # Rectangle for pools/lanes, with header separator if header_width_in > 0
        base_geom = f'''<Section N="Geometry" IX="0">
<Cell N="NoFill" V="0"/>
<Cell N="NoLine" V="0"/>
<Row T="MoveTo" IX="1">
<Cell N="X" V="0"/>
<Cell N="Y" V="0"/>
</Row>
<Row T="LineTo" IX="2">
<Cell N="X" V="{w_in}"/>
<Cell N="Y" V="0"/>
</Row>
<Row T="LineTo" IX="3">
<Cell N="X" V="{w_in}"/>
<Cell N="Y" V="{h_in}"/>
</Row>
<Row T="LineTo" IX="4">
<Cell N="X" V="0"/>
<Cell N="Y" V="{h_in}"/>
</Row>
<Row T="LineTo" IX="5">
<Cell N="X" V="0"/>
<Cell N="Y" V="0"/>
</Row>
</Section>'''
        if header_width_in > 0:
            hw_r = _r(header_width_in)
            base_geom += f'''
<Section N="Geometry" IX="1">
<Cell N="NoFill" V="1"/>
<Cell N="NoLine" V="0"/>
<Row T="MoveTo" IX="1">
<Cell N="X" V="{hw_r}"/>
<Cell N="Y" V="0"/>
</Row>
<Row T="LineTo" IX="2">
<Cell N="X" V="{hw_r}"/>
<Cell N="Y" V="{h_in}"/>
</Row>
</Section>'''
        return base_geom

    elif category == 'gateway':
        # Diamond
        return f'''<Section N="Geometry" IX="0">
<Cell N="NoFill" V="0"/>
<Cell N="NoLine" V="0"/>
<Row T="MoveTo" IX="1">
<Cell N="X" V="{hw}" F="Width*0.5"/>
<Cell N="Y" V="0"/>
</Row>
<Row T="LineTo" IX="2">
<Cell N="X" V="{w_in}" F="Width*1"/>
<Cell N="Y" V="{hh}" F="Height*0.5"/>
</Row>
<Row T="LineTo" IX="3">
<Cell N="X" V="{hw}" F="Width*0.5"/>
<Cell N="Y" V="{h_in}" F="Height*1"/>
</Row>
<Row T="LineTo" IX="4">
<Cell N="X" V="0"/>
<Cell N="Y" V="{hh}" F="Height*0.5"/>
</Row>
<Row T="LineTo" IX="5">
<Cell N="X" V="{hw}" F="Width*0.5"/>
<Cell N="Y" V="0"/>
</Row>
</Section>'''

    else:
        # Rectangle for tasks (Rounding cell is added at shape level for rounded corners)
        return f'''<Section N="Geometry" IX="0">
<Cell N="NoFill" V="0"/>
<Cell N="NoLine" V="0"/>
<Row T="MoveTo" IX="1">
<Cell N="X" V="0"/>
<Cell N="Y" V="0"/>
</Row>
<Row T="LineTo" IX="2">
<Cell N="X" V="{w_in}"/>
<Cell N="Y" V="0"/>
</Row>
<Row T="LineTo" IX="3">
<Cell N="X" V="{w_in}"/>
<Cell N="Y" V="{h_in}"/>
</Row>
<Row T="LineTo" IX="4">
<Cell N="X" V="0"/>
<Cell N="Y" V="{h_in}"/>
</Row>
<Row T="LineTo" IX="5">
<Cell N="X" V="0"/>
<Cell N="Y" V="0"/>
</Row>
</Section>'''


def _fill_xml(category, fill_color=None):
    """Return fill color cells based on element category or per-shape BPMN color."""
    if fill_color:
        return f'<Cell N="FillForegnd" V="{fill_color}"/><Cell N="FillForegndTrans" V="0"/>'
    # Default BPMN colors: all shapes are white when no bioc:fill is specified, matching bpmn.io
    if category == 'annotation':
        return '<Cell N="FillForegnd" V="#FFFFFF"/><Cell N="FillForegndTrans" V="1"/>'
    elif category in ('participant', 'lane'):
        return '<Cell N="FillForegnd" V="#FFFFFF"/><Cell N="FillForegndTrans" V="0"/>'
    else:  # tasks, events, gateways — white, matching bpmn.io defaults
        return '<Cell N="FillForegnd" V="#FFFFFF"/><Cell N="FillForegndTrans" V="0"/>'


def _line_xml(category, stroke_color=None, elem_type=''):
    """Return line style cells."""
    if category == 'end_event':
        weight = '0.04'  # thick border for end events (BPMN convention)
    elif elem_type in ('callActivity', 'subProcess'):
        weight = '0.04'  # thick border for callActivity/subProcess (BPMN convention)
    elif category in ('participant', 'lane'):
        weight = '0.01'
    elif category == 'annotation':
        weight = '0.01'
    elif category == 'start_event':
        weight = '0.02'
    elif category == 'intermediate_event':
        weight = '0.02'
    else:
        weight = '0.02'
    # BPMN defaults: black borders for shapes, gray for pools/lanes/annotations
    color = stroke_color or ('#999999' if category in ('participant', 'lane', 'annotation') else '#000000')
    return f'<Cell N="LineWeight" V="{weight}"/><Cell N="LineColor" V="{color}"/><Cell N="LinePattern" V="1"/>'


def _escape_xml(text):
    """Escape text for XML content."""
    if not text:
        return ''
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    text = text.replace('"', '&quot;')
    text = text.replace("'", '&apos;')
    return text


def _text_xml(name):
    """Return Text element. Newlines from BPMN (&#10;) are already converted
    to \\n by the XML parser, so they pass through naturally."""
    if not name:
        return ''
    return f'<Text>{_escape_xml(name)}</Text>'


def _char_section(category, font_size_pt=8):
    """Return Character section for text formatting."""
    # Visio Size cell uses inches: pt / 72 = inches
    size_in = _r(font_size_pt / 72)
    return f'''<Section N="Character" IX="0">
<Row IX="0">
<Cell N="Font" V="0"/>
<Cell N="Size" V="{size_in}"/>
<Cell N="Color" V="#333333"/>
</Row>
</Section>'''


def _para_section(halign=1):
    """Return Paragraph section for text alignment. 0=left, 1=center, 2=right."""
    return f'''<Section N="Paragraph" IX="0">
<Row IX="0">
<Cell N="HorzAlign" V="{halign}"/>
</Row>
</Section>'''


def _text_block_xml(category, w, h, label_offset=None, header_width_in=0, is_horizontal=True):
    """Return TextBlock cells for text positioning.
    All shapes get explicit TxtWidth/TxtHeight to ensure correct text layout.

    For pools/lanes:
      - If header_width_in > 0 and is_horizontal: vertical text in left header band
      - If header_width_in > 0 and not is_horizontal: horizontal text in top header band
      - If header_width_in == 0: centered text (no lanes, collapsed pool)

    For gateways/events:
      - Uses actual BPMN label position from label_offset when available

    label_offset: dict with 'dx', 'dy' (inches), 'lw', 'lh' (inches).
    header_width_in: computed header band width in inches (from BPMN coordinates).
    is_horizontal: whether the pool/lane is horizontal (header on left).
    """
    if category in ('participant', 'lane'):
        if header_width_in > 0 and is_horizontal:
            # Horizontal pool/lane: vertical text in left header band
            # TxtAngle = π/2 (90° CCW) for bottom-to-top reading
            # When rotated 90°, TxtWidth becomes visual height and TxtHeight
            # becomes visual width. So we set TxtWidth=h (reading length along
            # the lane) and TxtHeight=band_w (cross-direction for single line).
            band_w = header_width_in
            return (f'<Cell N="TxtAngle" V="1.5708"/>\n'
                    f'<Cell N="TxtPinX" V="{_r(band_w / 2)}"/>\n'
                    f'<Cell N="TxtPinY" V="{_r(h / 2)}"/>\n'
                    f'<Cell N="TxtWidth" V="{_r(h)}"/>\n'
                    f'<Cell N="TxtHeight" V="{_r(band_w)}"/>\n'
                    f'<Cell N="TxtLocPinX" V="{_r(h / 2)}"/>\n'
                    f'<Cell N="TxtLocPinY" V="{_r(band_w / 2)}"/>')
        elif header_width_in > 0 and not is_horizontal:
            # Vertical pool/lane: horizontal text in top header band
            band_h = header_width_in
            return (f'<Cell N="TxtAngle" V="0"/>\n'
                    f'<Cell N="TxtPinX" V="{_r(w / 2)}"/>\n'
                    f'<Cell N="TxtPinY" V="{_r(h - band_h / 2)}"/>\n'
                    f'<Cell N="TxtWidth" V="{_r(w)}"/>\n'
                    f'<Cell N="TxtHeight" V="{_r(band_h)}"/>\n'
                    f'<Cell N="TxtLocPinX" V="{_r(w / 2)}"/>\n'
                    f'<Cell N="TxtLocPinY" V="{_r(band_h / 2)}"/>')
        else:
            # No lanes / collapsed pool: centered text
            return (f'<Cell N="TxtAngle" V="0"/>\n'
                    f'<Cell N="TxtPinX" V="{_r(w / 2)}"/>\n'
                    f'<Cell N="TxtPinY" V="{_r(h / 2)}"/>\n'
                    f'<Cell N="TxtWidth" V="{_r(w)}"/>\n'
                    f'<Cell N="TxtHeight" V="{_r(h)}"/>\n'
                    f'<Cell N="TxtLocPinX" V="{_r(w / 2)}"/>\n'
                    f'<Cell N="TxtLocPinY" V="{_r(h / 2)}"/>')
    elif category in ('gateway', 'start_event', 'end_event', 'intermediate_event'):
        # Use actual BPMN label position if available
        if label_offset:
            dx = label_offset['dx']   # inches, positive = right of shape center
            dy = label_offset['dy']   # inches, positive = below shape center (BPMN Y)
            txt_w = max(label_offset['lw'], 0.8)
            txt_h = max(label_offset['lh'], 0.25)
            # In Visio local coords: origin at bottom-left of shape
            # Shape center in local coords = (w/2, h/2)
            # BPMN Y down = Visio Y up, so negate dy
            txt_pin_x = w / 2 + dx
            txt_pin_y = h / 2 - dy
            return (f'<Cell N="TxtAngle" V="0"/>\n'
                    f'<Cell N="TxtPinX" V="{_r(txt_pin_x)}"/>\n'
                    f'<Cell N="TxtPinY" V="{_r(txt_pin_y)}"/>\n'
                    f'<Cell N="TxtWidth" V="{_r(txt_w)}"/>\n'
                    f'<Cell N="TxtHeight" V="{_r(txt_h)}"/>\n'
                    f'<Cell N="TxtLocPinX" V="{_r(txt_w / 2)}"/>\n'
                    f'<Cell N="TxtLocPinY" V="{_r(txt_h / 2)}"/>')
        else:
            # Fallback: position below shape
            txt_w = max(w * 2.5, 1.2)
            txt_h = 0.35
            return (f'<Cell N="TxtAngle" V="0"/>\n'
                    f'<Cell N="TxtPinX" V="{_r(w / 2)}"/>\n'
                    f'<Cell N="TxtPinY" V="{_r(-txt_h / 2 - 0.04)}"/>\n'
                    f'<Cell N="TxtWidth" V="{_r(txt_w)}"/>\n'
                    f'<Cell N="TxtHeight" V="{_r(txt_h)}"/>\n'
                    f'<Cell N="TxtLocPinX" V="{_r(txt_w / 2)}"/>\n'
                    f'<Cell N="TxtLocPinY" V="{_r(txt_h / 2)}"/>')
    else:
        # Horizontal text centered in shape — must set all properties explicitly
        return (f'<Cell N="TxtAngle" V="0"/>\n'
                f'<Cell N="TxtPinX" V="{_r(w / 2)}" F="Width*0.5"/>\n'
                f'<Cell N="TxtPinY" V="{_r(h / 2)}" F="Height*0.5"/>\n'
                f'<Cell N="TxtWidth" V="{_r(w)}" F="Width*1"/>\n'
                f'<Cell N="TxtHeight" V="{_r(h)}" F="Height*1"/>\n'
                f'<Cell N="TxtLocPinX" V="{_r(w / 2)}" F="TxtWidth*0.5"/>\n'
                f'<Cell N="TxtLocPinY" V="{_r(h / 2)}" F="TxtHeight*0.5"/>')


def build_shape_xml(shape_id, category, pin_x, pin_y, w, h, name,
                    fill_color=None, stroke_color=None, label_offset=None,
                    header_width_in=0, is_horizontal=True,
                    elem_type='', event_def=''):
    """Build complete Visio Shape XML element.

    label_offset: dict with 'dx', 'dy' (inches), 'lw', 'lh' (inches)
                  for positioning text relative to shape center.
    header_width_in: computed header band width in inches (pools/lanes only).
    is_horizontal: whether pool/lane is horizontal (header on left side).
    elem_type: original BPMN element type (e.g., 'exclusiveGateway').
    event_def: event definition type (e.g., 'messageEventDefinition').
    """
    pin_x, pin_y, w, h = _r(pin_x), _r(pin_y), _r(w), _r(h)
    loc_pin_x = _r(w / 2)
    loc_pin_y = _r(h / 2)
    geom = _shape_geometry_xml(category, w, h, header_width_in=header_width_in if category in ('participant', 'lane') else 0)
    # Add BPMN markers (X for exclusive gw, + for parallel gw, envelope for message events, etc.)
    marker_geom = ''
    if category == 'gateway' and elem_type:
        marker_geom = _marker_geometry_xml(elem_type, w, h)
    elif category in ('start_event', 'end_event', 'intermediate_event') and event_def:
        marker_geom = _event_marker_geometry_xml(event_def, w, h)
    elif category == 'task' and elem_type in ('callActivity', 'subProcess'):
        marker_geom = _subprocess_marker_geometry_xml(w, h)
    fill = _fill_xml(category, fill_color)
    line = _line_xml(category, stroke_color, elem_type=elem_type)
    text = _text_xml(name)
    text_block = _text_block_xml(category, w, h, label_offset=label_offset,
                                 header_width_in=header_width_in, is_horizontal=is_horizontal)
    if category == 'annotation':
        font_size = 7
    elif category in ('start_event', 'end_event', 'intermediate_event'):
        font_size = 6
    elif category == 'gateway':
        font_size = 6
    elif category in ('participant', 'lane'):
        # Scale font to fit the header band width (avoid wrapping)
        # header_width_in is in inches; max font ~8pt for 0.3" band
        if header_width_in > 0:
            font_size = min(8, max(6, int(header_width_in * 24)))
        else:
            font_size = 9
    else:
        font_size = 8
    char = _char_section(category, font_size)
    # Left-align annotations, center everything else
    para = _para_section(halign=0) if category == 'annotation' else _para_section()

    # Add Rounding cell for task shapes to get rounded corners
    rounding_cell = ''
    if category == 'task':
        rounding = _r(min(0.1, w * 0.1, h * 0.1))
        rounding_cell = f'<Cell N="Rounding" V="{rounding}"/>'

    return f'''<Shape ID="{shape_id}" NameU="Shape.{shape_id}" Type="Shape">
<Cell N="PinX" V="{pin_x}"/>
<Cell N="PinY" V="{pin_y}"/>
<Cell N="Width" V="{w}"/>
<Cell N="Height" V="{h}"/>
<Cell N="LocPinX" V="{loc_pin_x}" F="Width*0.5"/>
<Cell N="LocPinY" V="{loc_pin_y}" F="Height*0.5"/>
<Cell N="Angle" V="0"/>
<Cell N="FlipX" V="0"/>
<Cell N="FlipY" V="0"/>
<Cell N="ResizeMode" V="0"/>
{text_block}
{rounding_cell}
{fill}
{line}
{char}
{para}
{geom}
{marker_geom}
{text}
</Shape>'''


def build_label_shape_xml(shape_id, pin_x, pin_y, lbl_w, lbl_h, text):
    """Build a separate invisible text-only shape for labels outside their parent shape.

    This is the standard approach used by Camunda, bpmn.io, and Bizagi BPMN exporters
    to position labels outside event/gateway shapes. Visio Desktop clips TxtPinX/TxtPinY
    to the parent shape's geometry bounds, so a separate shape is the only reliable way
    to place text outside a circle or diamond.

    pin_x, pin_y: Visio page coordinates for the label center.
    lbl_w, lbl_h: label dimensions in inches.
    text: the label text string.
    """
    if not text:
        return ''
    lbl_w = _r(max(lbl_w, 0.8))
    lbl_h = _r(max(lbl_h, 0.25))
    pin_x = _r(pin_x)
    pin_y = _r(pin_y)
    label_size = _r(6 / 72)  # 6pt
    escaped = _escape_xml(text)
    return f'''<Shape ID="{shape_id}" NameU="Label.{shape_id}" Type="Shape">
<Cell N="PinX" V="{pin_x}"/>
<Cell N="PinY" V="{pin_y}"/>
<Cell N="Width" V="{lbl_w}"/>
<Cell N="Height" V="{lbl_h}"/>
<Cell N="LocPinX" V="{_r(lbl_w / 2)}"/>
<Cell N="LocPinY" V="{_r(lbl_h / 2)}"/>
<Cell N="Angle" V="0"/>
<Cell N="FlipX" V="0"/>
<Cell N="FlipY" V="0"/>
<Cell N="ResizeMode" V="0"/>
<Cell N="TxtAngle" V="0"/>
<Cell N="TxtPinX" V="{_r(lbl_w / 2)}"/>
<Cell N="TxtPinY" V="{_r(lbl_h / 2)}"/>
<Cell N="TxtWidth" V="{lbl_w}"/>
<Cell N="TxtHeight" V="{lbl_h}"/>
<Cell N="TxtLocPinX" V="{_r(lbl_w / 2)}"/>
<Cell N="TxtLocPinY" V="{_r(lbl_h / 2)}"/>
<Cell N="FillForegnd" V="#FFFFFF"/>
<Cell N="FillForegndTrans" V="1"/>
<Cell N="FillPattern" V="0"/>
<Cell N="LinePattern" V="0"/>
<Section N="Geometry" IX="0">
<Cell N="NoFill" V="1"/>
<Cell N="NoLine" V="1"/>
<Row T="MoveTo" IX="1"><Cell N="X" V="0"/><Cell N="Y" V="0"/></Row>
<Row T="LineTo" IX="2"><Cell N="X" V="{lbl_w}"/><Cell N="Y" V="0"/></Row>
<Row T="LineTo" IX="3"><Cell N="X" V="{lbl_w}"/><Cell N="Y" V="{lbl_h}"/></Row>
<Row T="LineTo" IX="4"><Cell N="X" V="0"/><Cell N="Y" V="{lbl_h}"/></Row>
<Row T="LineTo" IX="5"><Cell N="X" V="0"/><Cell N="Y" V="0"/></Row>
</Section>
<Section N="Character" IX="0">
<Row IX="0">
<Cell N="Font" V="0"/>
<Cell N="Size" V="{label_size}"/>
<Cell N="Color" V="#333333"/>
</Row>
</Section>
<Section N="Paragraph" IX="0">
<Row IX="0">
<Cell N="HorzAlign" V="1"/>
</Row>
</Section>
<Text>{escaped}</Text>
</Shape>'''


def _rounded_line_geometry(pts, bb_min_x, bb_min_y, radius=0.15):
    """Generate Visio Geometry rows for a polyline with rounded corners.

    At each interior vertex, the sharp corner is replaced by an ArcTo row:
      - Cut back along each adjacent segment by `radius` (or half the segment
        length if the segment is shorter than 2*radius).
      - Insert a LineTo to the cut-back point, then an ArcTo that curves to
        the cut-back point on the next segment.

    The ArcTo 'A' cell (bow/sagitta) controls the arc bulge. For a 90° turn
    with radius r the sagitta is r*(1 - cos(θ/2)) where θ is the turn angle.
    We compute it from the geometry so it works for any angle.

    Returns a string of <Row> elements (MoveTo, LineTo, ArcTo).
    """
    if len(pts) < 2:
        return ''

    rows = ''
    ix = 1  # Visio row index (1-based)

    def _local(px, py):
        return _r(px - bb_min_x), _r(py - bb_min_y)

    if len(pts) == 2:
        # Straight line — no corners to round
        lx, ly = _local(*pts[0])
        rows += f'<Row T="MoveTo" IX="{ix}"><Cell N="X" V="{lx}"/><Cell N="Y" V="{ly}"/></Row>\n'
        ix += 1
        lx, ly = _local(*pts[1])
        rows += f'<Row T="LineTo" IX="{ix}"><Cell N="X" V="{lx}"/><Cell N="Y" V="{ly}"/></Row>\n'
        return rows

    # For 3+ points, round each interior corner
    for i in range(len(pts)):
        if i == 0:
            # Start point — just MoveTo
            lx, ly = _local(*pts[0])
            rows += f'<Row T="MoveTo" IX="{ix}"><Cell N="X" V="{lx}"/><Cell N="Y" V="{ly}"/></Row>\n'
            ix += 1
        elif i == len(pts) - 1:
            # End point — just LineTo
            lx, ly = _local(*pts[i])
            rows += f'<Row T="LineTo" IX="{ix}"><Cell N="X" V="{lx}"/><Cell N="Y" V="{ly}"/></Row>\n'
            ix += 1
        else:
            # Interior vertex — round this corner
            p_prev = pts[i - 1]
            p_curr = pts[i]
            p_next = pts[i + 1]

            # Vectors from current point to prev and next
            dx1 = p_prev[0] - p_curr[0]
            dy1 = p_prev[1] - p_curr[1]
            dx2 = p_next[0] - p_curr[0]
            dy2 = p_next[1] - p_curr[1]

            len1 = math.sqrt(dx1 * dx1 + dy1 * dy1)
            len2 = math.sqrt(dx2 * dx2 + dy2 * dy2)

            if len1 < 1e-6 or len2 < 1e-6:
                # Degenerate — just LineTo
                lx, ly = _local(*p_curr)
                rows += f'<Row T="LineTo" IX="{ix}"><Cell N="X" V="{lx}"/><Cell N="Y" V="{ly}"/></Row>\n'
                ix += 1
                continue

            # Clamp radius so we don't exceed half the segment length
            r = min(radius, len1 * 0.45, len2 * 0.45)

            # Unit vectors
            u1x, u1y = dx1 / len1, dy1 / len1
            u2x, u2y = dx2 / len2, dy2 / len2

            # Cut-back points (on the segments, `r` away from the corner)
            cb1 = (p_curr[0] + u1x * r, p_curr[1] + u1y * r)
            cb2 = (p_curr[0] + u2x * r, p_curr[1] + u2y * r)

            # LineTo the cut-back point on the incoming segment
            lx, ly = _local(*cb1)
            rows += f'<Row T="LineTo" IX="{ix}"><Cell N="X" V="{lx}"/><Cell N="Y" V="{ly}"/></Row>\n'
            ix += 1

            # Compute the arc sagitta (bow). For Visio ArcTo, the 'A' cell is
            # the signed distance from the midpoint of the chord to the arc.
            # Positive = left of chord direction, negative = right.
            # The sagitta magnitude = r * (1 - cos(θ/2)) where θ is the
            # exterior angle (turn angle) at the corner.
            dot = u1x * u2x + u1y * u2y  # cos(π - θ) where θ is the turn angle
            # θ = angle between the two outgoing directions
            # The angle between the incoming direction (from prev) reversed and
            # outgoing direction (to next):
            #   incoming_dir = (-u1x, -u1y), outgoing_dir = (u2x, u2y)
            #   cos(turn_angle) = dot(-incoming, outgoing) = -(u1·u2) = -dot
            cos_half = math.sqrt((1 + (-dot)) / 2)  # cos(turn/2) — but we need sin
            # Actually: sagitta = r * (1 - cos(half_turn))
            # half_turn = turn_angle / 2
            # cos(turn_angle) = -dot  =>  turn_angle = acos(-dot)
            turn_angle = math.acos(max(-1, min(1, -dot)))
            if turn_angle < 1e-4:
                # Nearly straight — no arc needed
                lx, ly = _local(*cb2)
                rows += f'<Row T="LineTo" IX="{ix}"><Cell N="X" V="{lx}"/><Cell N="Y" V="{ly}"/></Row>\n'
                ix += 1
                continue

            sagitta = r * (1 - math.cos(turn_angle / 2))

            # Determine sign: cross product tells us which side
            cross = (-u1x) * u2y - (-u1y) * u2x  # incoming_dir × outgoing_dir
            if cross < 0:
                sagitta = -sagitta

            lx, ly = _local(*cb2)
            rows += f'<Row T="ArcTo" IX="{ix}"><Cell N="X" V="{lx}"/><Cell N="Y" V="{ly}"/><Cell N="A" V="{_r(sagitta)}"/></Row>\n'
            ix += 1

    return rows


def _build_line_shape(shape_id, name_prefix, waypoints, page_h, offset_x, offset_y,
                      line_color='#555555', line_weight='0.02', line_pattern='1',
                      end_arrow='5', begin_arrow='0', label='', label_color='#333333',
                      label_pos=None):
    """Build a 2D line shape from waypoints using bounding-box positioning.

    Uses a 2D shape instead of 1D (BeginX/EndX) to avoid Visio's rotated
    local coordinate system issues with multi-segment connectors."""
    if len(waypoints) < 2:
        return ''

    # Convert all waypoints to Visio page coordinates
    pts = [wp_to_visio(wp['x'], wp['y'], page_h, offset_x, offset_y) for wp in waypoints]

    # Compute bounding box from waypoints
    all_x = [p[0] for p in pts]
    all_y = [p[1] for p in pts]
    bb_min_x, bb_max_x = min(all_x), max(all_x)
    bb_min_y, bb_max_y = min(all_y), max(all_y)

    # Expand bounding box to include label area (if label falls outside waypoint bounds)
    # Without this, label local coordinates can exceed shape Width/Height and Visio clips them
    if label and label_pos:
        lbl_cx = label_pos['x'] + label_pos['w'] / 2
        lbl_cy = label_pos['y'] + label_pos['h'] / 2
        lbl_vx, lbl_vy = wp_to_visio(lbl_cx, lbl_cy, page_h, offset_x, offset_y)
        lbl_hw = label_pos['w'] / PPI / 2  # half-width in inches
        lbl_hh = label_pos['h'] / PPI / 2  # half-height in inches
        bb_min_x = min(bb_min_x, lbl_vx - lbl_hw)
        bb_max_x = max(bb_max_x, lbl_vx + lbl_hw)
        bb_min_y = min(bb_min_y, lbl_vy - lbl_hh)
        bb_max_y = max(bb_max_y, lbl_vy + lbl_hh)

    # Ensure non-zero dimensions (degenerate lines)
    w = max(bb_max_x - bb_min_x, 0.01)
    h = max(bb_max_y - bb_min_y, 0.01)
    pin_x = _r((bb_min_x + bb_max_x) / 2)
    pin_y = _r((bb_min_y + bb_max_y) / 2)

    # Geometry in local coords with rounded corners
    geom_rows = _rounded_line_geometry(pts, bb_min_x, bb_min_y,
                                        radius=float(CONNECTOR_ROUNDING))

    # Arrowhead geometry: small triangle at the end of the last segment
    arrow_geom = ''
    if end_arrow != '0' and len(pts) >= 2:
        arrow_geom = _arrow_geometry(pts[-2], pts[-1], bb_min_x, bb_min_y, arrow_len=ARROW_LENGTH)

    # Optional label
    text_xml = ''
    char_xml = ''
    if label:
        text_xml = _text_xml(label)
        label_size = _r(CONNECTOR_LABEL_SIZE / 72)
        char_xml = (f'<Section N="Character" IX="0"><Row IX="0">'
                    f'<Cell N="Font" V="0"/><Cell N="Size" V="{label_size}"/>'
                    f'<Cell N="Color" V="{label_color}"/></Row></Section>')

    # Text block positioning
    if label and label_pos:
        # Use BPMN label position (absolute coordinates)
        # Convert label center from BPMN to Visio page coords
        lbl_cx = label_pos['x'] + label_pos['w'] / 2
        lbl_cy = label_pos['y'] + label_pos['h'] / 2
        lbl_vx, lbl_vy = wp_to_visio(lbl_cx, lbl_cy, page_h, offset_x, offset_y)
        # Convert to local coords relative to shape's bounding box origin (bottom-left)
        local_lbl_x = lbl_vx - bb_min_x
        local_lbl_y = lbl_vy - bb_min_y
        txt_w = max(label_pos['w'] / PPI, 0.4)
        txt_h = max(label_pos['h'] / PPI, 0.2)
        txt_block = (f'<Cell N="TxtAngle" V="0"/>\n'
                     f'<Cell N="TxtPinX" V="{_r(local_lbl_x)}"/>\n'
                     f'<Cell N="TxtPinY" V="{_r(local_lbl_y)}"/>\n'
                     f'<Cell N="TxtWidth" V="{_r(txt_w)}"/>\n'
                     f'<Cell N="TxtHeight" V="{_r(txt_h)}"/>\n'
                     f'<Cell N="TxtLocPinX" V="{_r(txt_w / 2)}"/>\n'
                     f'<Cell N="TxtLocPinY" V="{_r(txt_h / 2)}"/>')
    else:
        # Fallback: center on connector bounding box
        txt_w = max(w, 0.6)
        txt_h = max(h, 0.3)
        txt_block = (f'<Cell N="TxtAngle" V="0"/>\n'
                     f'<Cell N="TxtPinX" V="{_r(w / 2)}"/>\n'
                     f'<Cell N="TxtPinY" V="{_r(h / 2)}"/>\n'
                     f'<Cell N="TxtWidth" V="{_r(txt_w)}"/>\n'
                     f'<Cell N="TxtHeight" V="{_r(txt_h)}"/>\n'
                     f'<Cell N="TxtLocPinX" V="{_r(txt_w / 2)}"/>\n'
                     f'<Cell N="TxtLocPinY" V="{_r(txt_h / 2)}"/>')

    return f'''<Shape ID="{shape_id}" NameU="{name_prefix}.{shape_id}" Type="Shape">
<Cell N="PinX" V="{pin_x}"/>
<Cell N="PinY" V="{pin_y}"/>
<Cell N="Width" V="{_r(w)}"/>
<Cell N="Height" V="{_r(h)}"/>
<Cell N="LocPinX" V="{_r(w / 2)}"/>
<Cell N="LocPinY" V="{_r(h / 2)}"/>
{txt_block}
<Cell N="LineWeight" V="{line_weight}"/>
<Cell N="LineColor" V="{line_color}"/>
<Cell N="LinePattern" V="{line_pattern}"/>
<Cell N="FillForegnd" V="{line_color}"/>
<Cell N="FillForegndTrans" V="0"/>
<Cell N="FillPattern" V="1"/>
<Section N="Geometry" IX="0">
<Cell N="NoFill" V="1"/>
<Cell N="NoLine" V="0"/>
{geom_rows}</Section>
{arrow_geom}
{char_xml}
{text_xml}
</Shape>'''


def _arrow_geometry(from_pt, to_pt, bb_min_x, bb_min_y, arrow_len=0.08):
    """Build a filled triangle arrowhead at the end of a line segment.
    Returns a second Geometry section (IX=1) with the arrowhead."""
    dx = to_pt[0] - from_pt[0]
    dy = to_pt[1] - from_pt[1]
    length = math.sqrt(dx * dx + dy * dy)
    if length < 1e-6:
        return ''

    # Unit vector along the line
    ux, uy = dx / length, dy / length
    # Perpendicular
    px, py = -uy, ux

    # Arrowhead tip is at to_pt, base is arrow_len back
    tip_x, tip_y = to_pt
    base_x, base_y = tip_x - ux * arrow_len, tip_y - uy * arrow_len
    half_w = arrow_len * ARROW_WIDTH_RATIO

    # Triangle vertices
    p1 = (tip_x, tip_y)
    p2 = (base_x + px * half_w, base_y + py * half_w)
    p3 = (base_x - px * half_w, base_y - py * half_w)

    # Convert to local coords
    pts = [p1, p2, p3, p1]
    rows = ''
    for i, (ax, ay) in enumerate(pts):
        lx = _r(ax - bb_min_x)
        ly = _r(ay - bb_min_y)
        tag = 'MoveTo' if i == 0 else 'LineTo'
        rows += f'<Row T="{tag}" IX="{i + 1}"><Cell N="X" V="{lx}"/><Cell N="Y" V="{ly}"/></Row>\n'

    return f'''<Section N="Geometry" IX="1">
<Cell N="NoFill" V="0"/>
<Cell N="NoLine" V="0"/>
{rows}</Section>'''


def build_connector_xml(shape_id, waypoints, page_h, offset_x, offset_y, label='', label_pos=None):
    """Build a connector (sequence flow) from waypoints."""
    return _build_line_shape(shape_id, 'Connector', waypoints, page_h, offset_x, offset_y,
                             line_color=CONNECTOR_COLOR, line_weight=CONNECTOR_WEIGHT,
                             line_pattern='1',  # 1=solid (0=no line!)
                             end_arrow='1', label=label, label_color=CONNECTOR_LABEL_COLOR,
                             label_pos=label_pos)


def build_message_flow_xml(shape_id, waypoints, page_h, offset_x, offset_y, label='', label_pos=None):
    """Build a dashed connector for message flows."""
    return _build_line_shape(shape_id, 'MsgFlow', waypoints, page_h, offset_x, offset_y,
                             line_color=MSG_FLOW_COLOR, line_weight=CONNECTOR_WEIGHT,
                             line_pattern='2',  # 2=dashed
                             end_arrow='1', begin_arrow='0',
                             label=label, label_color=MSG_FLOW_LABEL_COLOR,
                             label_pos=label_pos)


def build_association_xml(shape_id, waypoints, page_h, offset_x, offset_y):
    """Build a dotted connector for associations (annotation links)."""
    return _build_line_shape(shape_id, 'Assoc', waypoints, page_h, offset_x, offset_y,
                             line_color='#999999', line_weight='0.01',
                             line_pattern='3',  # 3=dash-dot
                             end_arrow='0', begin_arrow='0')


def build_vsdx(elements, flows, shapes, edges, output_path, process_name='',
               participant_lanes=None):
    """Build a complete .vsdx file from parsed BPMN data."""
    if participant_lanes is None:
        participant_lanes = {}

    min_x, min_y, max_x, max_y = compute_bounds(shapes, edges)
    page_w, page_h = compute_page_size(min_x, min_y, max_x, max_y)
    # Offset shifts coords so the minimum becomes margin (50px)
    margin = 50
    offset_x = min_x - margin
    offset_y = min_y - margin

    # Compute header widths from actual BPMN coordinates (data-driven)
    # For each participant with lanes, header_width = min(lane_x) - participant_x
    DEFAULT_HEADER_PX = 30
    header_widths = {}  # bpmn_id -> header_width_in_pixels
    # Track lanes that should be hidden (single unnamed lane = structural only, invisible in bpmn.io)
    hidden_lanes = set()
    for part_id, lane_ids in participant_lanes.items():
        if part_id in shapes and lane_ids:
            part_x = shapes[part_id]['x']
            lane_xs = [shapes[lid]['x'] for lid in lane_ids if lid in shapes]
            if lane_xs:
                header_px = min(lane_xs) - part_x  # e.g., 160 - 130 = 30px
                header_px = max(header_px, DEFAULT_HEADER_PX)  # guard against zero/negative
                header_widths[part_id] = header_px
                # Check if this is a mono-lane pool (single unnamed lane)
                # In bpmn.io, a single lane with no name is invisible — only the pool header shows
                named_lanes = [lid for lid in lane_ids if elements.get(lid, {}).get('name', '')]
                if len(lane_ids) == 1 and not named_lanes:
                    # Single unnamed lane: hide it, pool header is sufficient
                    hidden_lanes.add(lane_ids[0])
                else:
                    # Multi-lane or named lanes: each lane gets its own header
                    for lid in lane_ids:
                        header_widths[lid] = header_px

    # Fallback: participants whose lanes are ALL hidden (mono-lane unnamed) still need
    # a header band if they are horizontal pools.
    for part_id, lane_ids in participant_lanes.items():
        if part_id not in header_widths and part_id in shapes:
            # This participant had lanes but they were all hidden (mono-lane unnamed)
            if shapes[part_id].get('is_horizontal', True):
                header_widths[part_id] = DEFAULT_HEADER_PX

    # Lane-less participants (e.g. external "Customer" pools with no <laneSet>)
    # also need a header band for vertical text in Visio — they are not in
    # participant_lanes at all, so handle them separately.
    for elem_id, elem_info in elements.items():
        if (elem_info['type'] == 'participant'
                and elem_id not in header_widths
                and elem_id in shapes
                and shapes[elem_id].get('is_horizontal', True)):
            header_widths[elem_id] = DEFAULT_HEADER_PX

    # Build lookup: which lane belongs to which participant
    lane_to_participant = {}
    for part_id, lane_ids in participant_lanes.items():
        for lid in lane_ids:
            lane_to_participant[lid] = part_id

    # Assign Visio shape IDs
    shape_id_map = {}  # bpmn_id -> visio_shape_id
    next_id = 1

    # Render in z-order: participants (bottom) → lanes → annotations → shapes (top)
    pool_parts = []   # participants
    lane_parts = []   # lanes
    annot_parts = []  # text annotations
    fg_parts = []     # tasks, events, gateways
    for bpmn_id, elem_info in elements.items():
        if bpmn_id not in shapes:
            continue
        # Skip hidden lanes (single unnamed lane in a mono-lane pool)
        if bpmn_id in hidden_lanes:
            continue
        s = shapes[bpmn_id]
        category = get_element_category(elem_info['type'])
        pin_x, pin_y, w, h = bpmn_to_visio_coords(s['x'], s['y'], s['w'], s['h'], page_h, offset_x, offset_y)

        # Compute label offset from BPMN label bounds (for events/gateways)
        label_offset = None
        if 'label_x' in s:
            # Shape center in BPMN pixels
            shape_cx = s['x'] + s['w'] / 2
            shape_cy = s['y'] + s['h'] / 2
            # Label center in BPMN pixels
            label_cx = s['label_x'] + s['label_w'] / 2
            label_cy = s['label_y'] + s['label_h'] / 2
            # Offset in inches (BPMN Y-down)
            label_offset = {
                'dx': (label_cx - shape_cx) / PPI,
                'dy': (label_cy - shape_cy) / PPI,
                'lw': s['label_w'] / PPI,
                'lh': s['label_h'] / PPI,
            }

        # Compute header width for pools/lanes (data-driven from BPMN coordinates)
        header_width_in = header_widths.get(bpmn_id, 0) / PPI
        is_horizontal = s.get('is_horizontal', True)

        shape_id_map[bpmn_id] = next_id

        # For events/gateways with names, use a separate label shape instead of
        # in-shape text blocks. Visio Desktop clips TxtPinX/TxtPinY to the shape's
        # geometry bounds, so text positioned outside circles/diamonds gets clipped.
        use_separate_label = (category in ('start_event', 'end_event',
                              'intermediate_event', 'gateway') and elem_info['name'])

        shape_name = '' if use_separate_label else elem_info['name']
        shape_xml = build_shape_xml(next_id, category, pin_x, pin_y, w, h, shape_name,
                                    fill_color=s.get('fill_color'), stroke_color=s.get('stroke_color'),
                                    label_offset=label_offset,
                                    header_width_in=header_width_in, is_horizontal=is_horizontal,
                                    elem_type=elem_info['type'],
                                    event_def=elem_info.get('event_def', ''))
        if category == 'participant':
            pool_parts.append(shape_xml)
        elif category == 'lane':
            lane_parts.append(shape_xml)
        elif category == 'annotation':
            annot_parts.append(shape_xml)
        else:
            fg_parts.append(shape_xml)
        next_id += 1

        # Create separate label shape for events/gateways
        if use_separate_label:
            if 'label_x' in s:
                # Use BPMN label bounds (absolute coordinates)
                lbl_pin_x, lbl_pin_y, lbl_w, lbl_h = bpmn_to_visio_coords(
                    s['label_x'], s['label_y'], s['label_w'], s['label_h'],
                    page_h, offset_x, offset_y)
            else:
                # Fallback: position label below the shape
                lbl_w = max(w * 2.5, 1.2)
                lbl_h = 0.35
                lbl_pin_x = pin_x
                lbl_pin_y = pin_y - h / 2 - lbl_h / 2 - 0.04
            label_xml = build_label_shape_xml(next_id, lbl_pin_x, lbl_pin_y,
                                               lbl_w, lbl_h, elem_info['name'])
            if label_xml:
                fg_parts.append(label_xml)
                next_id += 1

    shape_xml_parts = pool_parts + lane_parts + fg_parts + annot_parts

    # Build connector XML for each flow
    for flow in flows:
        if flow['id'] in edges:
            edge_data = edges[flow['id']]
            waypoints = edge_data['waypoints']
            label_pos = edge_data.get('label')  # BPMN label bounds or None
            flow_type = flow.get('type', 'sequenceFlow')
            if flow_type == 'messageFlow':
                xml = build_message_flow_xml(next_id, waypoints, page_h, offset_x, offset_y, flow.get('name', ''), label_pos=label_pos)
            elif flow_type == 'association':
                xml = build_association_xml(next_id, waypoints, page_h, offset_x, offset_y)
            else:
                xml = build_connector_xml(next_id, waypoints, page_h, offset_x, offset_y, flow.get('name', ''), label_pos=label_pos)
            if xml:
                shape_xml_parts.append(xml)
                next_id += 1

    all_shapes_xml = '\n'.join(shape_xml_parts)

    # Page title
    page_name = process_name or 'BPMN Diagram'
    page_name_escaped = _escape_xml(page_name)

    # ── Build VSDX package files ──

    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/visio/document.xml" ContentType="application/vnd.ms-visio.drawing.main+xml"/>
<Override PartName="/visio/pages/pages.xml" ContentType="application/vnd.ms-visio.pages+xml"/>
<Override PartName="/visio/pages/page1.xml" ContentType="application/vnd.ms-visio.page+xml"/>
<Override PartName="/visio/windows.xml" ContentType="application/vnd.ms-visio.windows+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>'''

    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/document" Target="visio/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''

    document_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<VisioDocument xmlns="http://schemas.microsoft.com/office/visio/2012/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<DocumentProperties>
<Creator>BPMN to VSDX Converter</Creator>
</DocumentProperties>
<DocumentSettings/>
<Colors/>
<FaceNames>
<FaceName ID="0" Name="Calibri" UnicodeRanges="-1 -1 0 0" CharSets="536871423 0" Panos="2 15 5 2 2 2 4 3 2 4"/>
</FaceNames>
<StyleSheets>
<StyleSheet ID="0" NameU="Normal" Name="Normal">
<Cell N="LineWeight" V="0.01"/>
<Cell N="LineColor" V="#333333"/>
<Cell N="FillForegnd" V="#FFFFFF"/>
<Cell N="CharFont" V="0"/>
<Cell N="TxtHeight" V="0.1111"/>
</StyleSheet>
</StyleSheets>
</VisioDocument>'''

    document_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/pages" Target="pages/pages.xml"/>
<Relationship Id="rId2" Type="http://schemas.microsoft.com/visio/2010/relationships/windows" Target="windows.xml"/>
</Relationships>'''

    pages_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Pages xmlns="http://schemas.microsoft.com/office/visio/2012/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<Page ID="0" NameU="{page_name_escaped}" Name="{page_name_escaped}">
<PageSheet>
<Cell N="PageWidth" V="{page_w}"/>
<Cell N="PageHeight" V="{page_h}"/>
<Cell N="DrawingScale" V="1"/>
<Cell N="PageScale" V="1"/>
</PageSheet>
<Rel r:id="rId1"/>
</Page>
</Pages>'''

    pages_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/page" Target="page1.xml"/>
</Relationships>'''

    page1_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<Shapes>
{all_shapes_xml}
</Shapes>
</PageContents>'''

    windows_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Windows xmlns="http://schemas.microsoft.com/office/visio/2012/main">
<Window ID="0" WindowType="Drawing" WindowState="1073741824" WindowLeft="0" WindowTop="0" WindowWidth="1024" WindowHeight="768">
<StencilGroup/>
<StencilGroupPos/>
<ShowRulers>1</ShowRulers>
<ShowGrid>1</ShowGrid>
<ShowPageBreaks>0</ShowPageBreaks>
<ShowGuides>1</ShowGuides>
<ShowConnectionPoints>1</ShowConnectionPoints>
<GlueSettings>9</GlueSettings>
<SnapSettings>65847</SnapSettings>
<SnapExtensions>34</SnapExtensions>
<DynamicGridEnabled>1</DynamicGridEnabled>
<TabSplitterPos>0.5</TabSplitterPos>
</Window>
</Windows>'''

    app_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">
<Application>BPMN to VSDX Converter</Application>
</Properties>'''

    # Write ZIP package
    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml', content_types)
        zf.writestr('_rels/.rels', rels)
        zf.writestr('visio/document.xml', document_xml)
        zf.writestr('visio/_rels/document.xml.rels', document_rels)
        zf.writestr('visio/pages/pages.xml', pages_xml)
        zf.writestr('visio/pages/_rels/pages.xml.rels', pages_rels)
        zf.writestr('visio/pages/page1.xml', page1_xml)
        zf.writestr('visio/windows.xml', windows_xml)
        zf.writestr('docProps/app.xml', app_xml)

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(buf.getvalue())


# ── CLI ───────────────────────────────────────────────────────────────────────

def get_process_name_from_filename(filename):
    """Extract process name from BPMN filename."""
    name = filename.replace("BPMN diagram - ", "")
    name = re.sub(r"\s*-\s*V\s*\d+\.\d+", "", name)
    name = name.replace(".bpmn", "")
    return name.strip()


def convert_bpmn_to_vsdx(bpmn_path, output_dir=None):
    """Public API: Convert a BPMN file to VSDX. Returns output path or None."""
    return convert_file(bpmn_path, output_dir)


def convert_file(bpmn_path, output_dir=None):
    """Convert a single BPMN file to VSDX."""
    bpmn_path = Path(bpmn_path)
    if not bpmn_path.exists():
        print(f"Error: File not found: {bpmn_path}")
        return False

    process_name = get_process_name_from_filename(bpmn_path.name)

    if output_dir:
        out_dir = Path(output_dir)
    else:
        out_dir = bpmn_path.parent

    output_path = out_dir / (bpmn_path.stem + '.vsdx')

    print(f"Converting: {bpmn_path.name}")
    try:
        elements, flows, shapes, edges, participant_lanes = parse_bpmn(str(bpmn_path))
        print(f"  Found {len(elements)} elements, {len(flows)} flows, {len(shapes)} shapes")

        if not elements:
            print(f"  Warning: No BPMN elements found - skipping")
            return False

        build_vsdx(elements, flows, shapes, edges, str(output_path), process_name,
                   participant_lanes=participant_lanes)
        print(f"  Output: {output_path}")
        return True
    except Exception as e:
        print(f"  Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(description='Convert BPMN 2.0 XML files to Visio .vsdx format')
    parser.add_argument('input', nargs='?', help='Input BPMN file path')
    parser.add_argument('--batch', metavar='FOLDER', help='Convert all .bpmn files in folder')
    parser.add_argument('-o', '--output', metavar='DIR', help='Output directory (default: same as input)')
    args = parser.parse_args()

    if not args.input and not args.batch:
        parser.print_help()
        sys.exit(1)

    success_count = 0
    fail_count = 0

    if args.batch:
        batch_dir = Path(args.batch)
        if not batch_dir.is_dir():
            print(f"Error: Directory not found: {batch_dir}")
            sys.exit(1)

        bpmn_files = sorted(batch_dir.glob('**/*.bpmn'))
        if not bpmn_files:
            print(f"No .bpmn files found in {batch_dir}")
            sys.exit(1)

        print(f"Found {len(bpmn_files)} BPMN files\n")
        for bpmn_file in bpmn_files:
            if convert_file(bpmn_file, args.output):
                success_count += 1
            else:
                fail_count += 1
            print()
    elif args.input:
        if convert_file(args.input, args.output):
            success_count += 1
        else:
            fail_count += 1

    print(f"\nDone: {success_count} converted, {fail_count} failed")


if __name__ == '__main__':
    main()
