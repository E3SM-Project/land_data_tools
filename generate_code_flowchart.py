"""
generate_flowchart.py
Generates a PowerPoint (.pptx) flowchart of the landgen code structure.

Run this script from the upper-level landgen directory (.../tools/landgen/) 
    and it will write landgen_flowchart.pptx in the same directory.
    You can specify a custom output path as an optional argument.

Usage:
    python generate_flowchart.py            # writes landgen_flowchart.pptx
    python generate_flowchart.py <out.pptx> # writes to a custom path

Requires:
    conda install python-pptx
"""

import sys
from pathlib import Path
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.enum.text import PP_ALIGN
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.oxml import parse_xml
from pptx.oxml.ns import qn, nsdecls
from lxml import etree

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
C_ENTRY    = RGBColor(0x1F, 0x49, 0x7D)   # dark blue  – entry point
C_CORE     = RGBColor(0x2E, 0x75, 0xB6)   # mid blue   – core orchestration
C_MODULE   = RGBColor(0x70, 0xAD, 0x47)   # green      – top-level modules
C_SUB      = RGBColor(0xFF, 0xC0, 0x00)   # amber      – sub-steps
C_IO       = RGBColor(0xED, 0x7D, 0x31)   # orange     – I/O utilities
C_DATA     = RGBColor(0x7B, 0x0E, 0xA8)   # purple     – shared data structures
C_PARALLEL = RGBColor(0xC0, 0x50, 0x4D)   # red        – parallel workers
C_NOTIMPL  = RGBColor(0xA5, 0xA5, 0xA5)   # grey       – not yet implemented
C_WHITE    = RGBColor(0xFF, 0xFF, 0xFF)
C_BLACK    = RGBColor(0x00, 0x00, 0x00)
C_ARROW    = RGBColor(0x40, 0x40, 0x40)

SLIDE_W = Inches(20)
SLIDE_H = Inches(14)

# Font sizes – adjust these to make all text larger or smaller
FS_TITLE = Pt(22)    # slide title
FS_LABEL = Pt(11)    # section labels / IO header
FS_BODY  = Pt(10)    # main box text
FS_SMALL = Pt(9.5)   # smaller boxes (substeps, workers, IO)
FS_ARROW = Pt(9)     # arrow labels

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rgb_to_hex(rgb: RGBColor) -> str:
    return f"{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"


def add_box(slide, left, top, width, height,
            text, fill_color, text_color=C_WHITE,
            font_size=FS_BODY, bold=False, shape_type="rect"):
    """Add a rounded-rectangle shape and return it."""

    shape = slide.shapes.add_shape(
        MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE,
        left, top, width, height
    )

    # solid fill via python-pptx API (avoids duplicate prstGeom XML corruption)
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill_color

    # thin white border
    shape.line.color.rgb = C_WHITE
    shape.line.width = Pt(0.75)

    # text
    tf = shape.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = text
    run.font.size = font_size
    run.font.bold = bold
    run.font.color.rgb = text_color

    return shape


def add_arrow(slide, x1, y1, x2, y2, color=C_ARROW, label=None):
    """Draw a straight connector with an arrowhead at (x2,y2).

    Builds the <p:cxnSp> from a complete XML template so the element has
    no <p:style> block (which can trigger PowerPoint repair on blank
    presentations) and no python-pptx connector-API side-effects.
    """
    # Bounding box of the line segment
    left = int(min(x1, x2))
    top  = int(min(y1, y2))
    cx   = int(abs(x2 - x1)) or 1   # OOXML requires cx >= 1
    cy   = int(abs(y2 - y1)) or 1

    # flip attributes encode which diagonal the line runs along
    flip_attrs = ''
    if x2 < x1:
        flip_attrs += ' flipH="1"'
    if y2 < y1:
        flip_attrs += ' flipV="1"'

    hex_color = _rgb_to_hex(color)
    sp_tree   = slide.shapes._spTree
    shape_id  = sp_tree._next_shape_id

    xml = (
        f'<p:cxnSp {nsdecls("a", "p", "r")}>'
        f'<p:nvCxnSpPr>'
        f'<p:cNvPr id="{shape_id}" name="Arrow {shape_id}"/>'
        f'<p:cNvCxnSpPr/>'
        f'<p:nvPr/>'
        f'</p:nvCxnSpPr>'
        f'<p:spPr>'
        f'<a:xfrm{flip_attrs}><a:off x="{left}" y="{top}"/>'
        f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f'<a:prstGeom prst="line"><a:avLst/></a:prstGeom>'
        f'<a:ln w="19050">'
        f'<a:solidFill><a:srgbClr val="{hex_color}"/></a:solidFill>'
        f'<a:tailEnd type="arrow" w="med" len="med"/>'
        f'</a:ln>'
        f'</p:spPr>'
        f'</p:cxnSp>'
    )

    cxnSp = parse_xml(xml)
    sp_tree.append(cxnSp)

    if label:
        mx = (x1 + x2) / 2
        my = (y1 + y2) / 2
        tb = slide.shapes.add_textbox(mx, my - Inches(0.12), Inches(0.9), Inches(0.2))
        tf = tb.text_frame
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = label
        run.font.size = FS_ARROW
        run.font.color.rgb = C_ARROW

    return cxnSp


# ---------------------------------------------------------------------------
# Layout constants  (all in Inches, converted via Inches())
# ---------------------------------------------------------------------------

def build_slide(prs):
    slide_layout = prs.slide_layouts[6]   # blank
    slide = prs.slides.add_slide(slide_layout)

    # ---- title ----
    tb = slide.shapes.add_textbox(Inches(0.2), Inches(0.1),
                                  Inches(19.6), Inches(0.5))
    tf = tb.text_frame
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = "landgen – Code Structure Flowchart"
    run.font.size = FS_TITLE
    run.font.bold = True
    run.font.color.rgb = C_ENTRY

    # ---- legend ----
    legend_items = [
        (C_ENTRY,    "Entry point"),
        (C_CORE,     "Core orchestration"),
        (C_MODULE,   "Top-level module"),
        (C_SUB,      "Sub-step (sequential)"),
        (C_PARALLEL, "Parallel worker"),
        (C_IO,       "I/O utility"),
        (C_DATA,     "Shared data structure"),
        (C_NOTIMPL,  "Not yet implemented"),
    ]
    lx = Inches(0.2)
    ly = Inches(0.7)
    for i, (col, lbl) in enumerate(legend_items):
        add_box(slide, lx + Inches(i * 2.4), ly, Inches(2.2), Inches(0.3),
                lbl, col, font_size=FS_SMALL)

    # =========================================================
    # ROW 1 – Entry point
    # =========================================================
    r1y = Inches(1.2)
    bh = Inches(0.45)   # box height
    bw_sm  = Inches(2.2)
    bw_med = Inches(2.8)
    bw_lg  = Inches(3.6)

    # __main__.py
    main_x = Inches(9.1)
    b_main = add_box(slide, main_x, r1y, bw_med, bh,
                     "__main__.py\npython -m landgen <config.json>",
                     C_ENTRY, font_size=FS_BODY, bold=True)

    # config.json (to the right)
    cfg_x = Inches(13.0)
    b_cfg = add_box(slide, cfg_x, r1y, bw_sm, bh,
                    "config.json\n(start/end year, paths, modules)",
                    C_IO, font_size=FS_BODY)

    add_arrow(slide,
              cfg_x, r1y + bh / 2,
              main_x + bw_med, r1y + bh / 2,
              label="reads")

    # =========================================================
    # ROW 2 – landgen.main()
    # =========================================================
    r2y = r1y + bh + Inches(0.55)
    core_x = Inches(8.6)
    bw_core = Inches(3.8)
    b_core = add_box(slide, core_x, r2y, bw_core, bh,
                     "landgen.py  ·  main(config_path)\n"
                     "Load config · start mp.Manager · create GridData\n"
                     "Loop modules → importlib.import_module(name).run()",
                     C_CORE, font_size=FS_BODY, bold=True)

    # arrow __main__ → main
    add_arrow(slide,
              main_x + bw_med / 2, r1y + bh,
              core_x + bw_core / 2, r2y)

    # =========================================================
    # ROW 3 – Shared data (right side)
    # =========================================================
    r3y = r2y
    sd_x = Inches(13.8)
    bw_sd = Inches(5.8)
    b_sd = add_box(slide, sd_x, r3y, bw_sd, Inches(1.35),
                   "shared_data.py  –  Multiprocessing shared structures\n"
                   "GridData / GridManager  (cell ids, lon/lat, landfrac)\n"
                   "TopoData / TopoManager  (elevation, slope, fmax …)\n"
                   "LtData  / LtManager     (pct_pft, harvest_frac, …)",
                   C_DATA, font_size=Pt(8))

    add_arrow(slide,
              core_x + bw_core, r2y + bh / 2,
              sd_x, r3y + Inches(0.675),
              label="uses")

    # =========================================================
    # ROW 4 – Top-level modules  (5 boxes across)
    # =========================================================
    r4y = r3y + Inches(1.5) + Inches(0.45)
    modules = [
        ("topography.py\nrun()\n[must run first;\nsets landfrac]",  C_MODULE),
        ("land_type.py\nrun()\n[main land-type\norchestrator]",     C_MODULE),
        ("atmosphere.py\nrun()\n[ndep, pdep,\nlightning]",         C_MODULE),
        ("soil.py\nrun()\n[soil texture,\norganic matter]",        C_MODULE),
        ("human.py\nrun()\n[population,\nGDP]",                    C_MODULE),
    ]
    mod_bw   = Inches(3.4)
    mod_gap  = Inches(0.35)
    total_mw = len(modules) * mod_bw + (len(modules) - 1) * mod_gap
    mod_x0   = (SLIDE_W - total_mw) / 2
    mod_bh   = Inches(0.85)

    mod_boxes = []
    for i, (txt, col) in enumerate(modules):
        bx = mod_x0 + i * (mod_bw + mod_gap)
        b = add_box(slide, bx, r4y, mod_bw, mod_bh, txt, col,
                    font_size=FS_BODY)
        mod_boxes.append((bx, b))
        add_arrow(slide,
                  core_x + bw_core / 2, r2y + bh,
                  bx + mod_bw / 2, r4y)

    # =========================================================
    # ROW 5 – land_type internals: _process_single_year  loop
    # =========================================================
    r5y = r4y + mod_bh + Inches(0.55)
    lt_x  = mod_x0 + 1 * (mod_bw + mod_gap)   # aligned under land_type
    bw_lt = Inches(3.4)
    bh_lt = Inches(0.45)
    b_loop = add_box(slide, lt_x, r5y, bw_lt, bh_lt,
                     "Loop years (start_year → end_year)\n"
                     "_process_single_year(year, …)",
                     C_CORE, font_size=FS_BODY)
    add_arrow(slide,
              lt_x + bw_lt / 2, r4y + mod_bh,
              lt_x + bw_lt / 2, r5y)

    # =========================================================
    # ROW 6 – Sub-steps inside _process_single_year
    # =========================================================
    r6y = r5y + bh_lt + Inches(0.45)
    substeps = [
        ("landcover.py\nrun() → landcover_\nprocess() [parallel]",     C_SUB),
        ("crop.py\nrun()\n[not yet impl.]",                             C_NOTIMPL),
        ("urban.py\nrun()\n[not yet impl.]",                            C_NOTIMPL),
        ("lake.py\nrun()\n[not yet impl.]",                             C_NOTIMPL),
        ("ice.py\nrun()\n[not yet impl.]",                              C_NOTIMPL),
        ("harvest.py\nrun() → harvest_\nprocess() [parallel]",         C_SUB),
        ("normalize_cell\nfill_land +\nreconcile_ocean",                C_NOTIMPL),
        ("veg_assoc.py\nrun()\n[not yet impl.]",                        C_NOTIMPL),
        ("consistency.py\nrun()\n[not yet impl.]",                      C_NOTIMPL),
    ]
    ss_bw  = Inches(2.0)
    ss_bh  = Inches(0.75)
    ss_gap = Inches(0.18)
    total_ssw = len(substeps) * ss_bw + (len(substeps) - 1) * ss_gap
    ss_x0  = (SLIDE_W - total_ssw) / 2

    for i, (txt, col) in enumerate(substeps):
        bx = ss_x0 + i * (ss_bw + ss_gap)
        add_box(slide, bx, r6y, ss_bw, ss_bh, txt, col, font_size=FS_SMALL)
        add_arrow(slide,
                  lt_x + bw_lt / 2, r5y + bh_lt,
                  bx + ss_bw / 2, r6y)

    # =========================================================
    # ROW 7 – Parallel worker detail (landcover & harvest)
    # =========================================================
    r7y = r6y + ss_bh + Inches(0.45)

    # landcover worker
    lc_worker_x = ss_x0   # aligned under landcover box
    b_lcw = add_box(slide, lc_worker_x, r7y, Inches(2.0), Inches(0.95),
                    "landcover_process()\n· read MODIS / transitions\n"
                    "· regrid (uraster)\n· split PFTs\n· write → LtData",
                    C_PARALLEL, font_size=FS_SMALL)
    add_arrow(slide,
              ss_x0 + ss_bw / 2, r6y + ss_bh,
              lc_worker_x + Inches(1.0), r7y)

    # harvest worker
    harv_worker_x = ss_x0 + 5 * (ss_bw + ss_gap)
    b_hw = add_box(slide, harv_worker_x, r7y, Inches(2.0), Inches(0.95),
                   "harvest_process()\n· read LUH2 harvest\n"
                   "· read HYDE grazing\n· regrid (uraster)\n"
                   "· write → LtData",
                   C_PARALLEL, font_size=FS_SMALL)
    add_arrow(slide,
              harv_worker_x + ss_bw / 2, r6y + ss_bh,
              harv_worker_x + Inches(1.0), r7y)

    # =========================================================
    # ROW 8 – I/O utilities
    # =========================================================
    r8y = r7y + Inches(0.95) + Inches(0.45)
    io_items = [
        "read_luh2_harvest()\nLUH2 transitions.nc\n→ harvest arrays",
        "read_hyde_grazing()\nHYDE3.5 .nc files\n→ grazing arrays",
        "write_latlon_to_geotiff()\n2D lat-lon slice\n→ GeoTIFF",
        "write_chunk_mesh_\nto_geojson()\nHEALPix cells → GeoJSON",
        "regrid_to_landgen_\ngrid()\nGeoTIFF + GeoJSON\n→ URaster → 1D array",
    ]
    io_bw  = Inches(3.3)
    io_bh  = Inches(0.85)
    io_gap = Inches(0.2)
    total_iow = len(io_items) * io_bw + (len(io_items) - 1) * io_gap
    io_x0  = (SLIDE_W - total_iow) / 2

    for i, txt in enumerate(io_items):
        bx = io_x0 + i * (io_bw + io_gap)
        add_box(slide, bx, r8y, io_bw, io_bh, txt, C_IO, font_size=FS_SMALL)

    # label row
    lbl = slide.shapes.add_textbox(io_x0 - Inches(0.1), r8y - Inches(0.3),
                                   Inches(4), Inches(0.25))
    lf = lbl.text_frame.paragraphs[0]
    lr = lf.add_run()
    lr.text = "landgen_io.py  –  I/O utilities"
    lr.font.size = FS_LABEL
    lr.font.bold = True
    lr.font.color.rgb = C_IO

    # arrows from workers into io
    add_arrow(slide,
              lc_worker_x + Inches(1.0), r7y + Inches(0.95),
              io_x0 + 2 * io_bw + Inches(1.65), r8y)
    add_arrow(slide,
              harv_worker_x + Inches(1.0), r7y + Inches(0.95),
              io_x0 + 2 * io_bw + Inches(1.65), r8y)

    return slide


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 \
        else Path("landgen_flowchart.pptx")

    prs = Presentation()
    prs.slide_width  = SLIDE_W
    prs.slide_height = SLIDE_H

    build_slide(prs)

    prs.save(out_path)
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()

