"""
Build the interview presentation for the World Bank Documents MCP Server project.

Run:
    python build_deck.py

Outputs (next to this script):
    World-Bank-MCP-Presentation.pptx
    output_screenshots/*.png
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
SHOTS = HERE / "output_screenshots"
CAPTURES = Path(r"C:\Users\prasa\AppData\Local\Temp\opencode\wb-captures")
PPTX = HERE / "World-Bank-MCP-Presentation.pptx"

SHOTS.mkdir(exist_ok=True)

# --------------------------------------------------------------------------
# Theme
# --------------------------------------------------------------------------

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)

PRIMARY = RGBColor(0x0B, 0x4F, 0x8A)      # World Bank blue
PRIMARY_DARK = RGBColor(0x06, 0x33, 0x59)
ACCENT = RGBColor(0x00, 0xA0, 0xDF)       # bright cyan-blue
TEAL = RGBColor(0x0E, 0x8F, 0x7E)
GREEN = RGBColor(0x2E, 0x7D, 0x32)
AMBER = RGBColor(0xB2, 0x6A, 0x00)
DARK = RGBColor(0x1A, 0x1A, 0x2E)
GREY = RGBColor(0x5A, 0x62, 0x6B)
LIGHT = RGBColor(0xF2, 0xF6, 0xFA)
LIGHT2 = RGBColor(0xE7, 0xEF, 0xF7)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
CODE_BG = RGBColor(0x1E, 0x1E, 0x1E)
CODE_FG = RGBColor(0xD4, 0xD4, 0xD4)

BODY_FONT = "Segoe UI"
MONO_FONT = "Consolas"

TERM_FONT = "C:/Windows/Fonts/consola.ttf"
TERM_FONT_B = "C:/Windows/Fonts/consolab.ttf"


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def blank(prs: Presentation):
    return prs.slides.add_slide(prs.slide_layouts[6])


def rect(slide, left, top, width, height, fill=None, line=None, shape=MSO_SHAPE.RECTANGLE,
         line_w=1.0, radius=None):
    shp = slide.shapes.add_shape(shape, left, top, width, height)
    shp.shadow.inherit = False
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(line_w)
    if radius is not None and shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        shp.adjustments[0] = radius
    return shp


def textbox(slide, left, top, width, height, anchor=MSO_ANCHOR.TOP):
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    return tf


def para(tf, first, text, size=16, bold=False, color=DARK, font=BODY_FONT,
         space_before=0, space_after=6, align=PP_ALIGN.LEFT, level=0, italic=False):
    p = tf.paragraphs[0] if first else tf.add_paragraph()
    p.alignment = align
    p.level = level
    p.space_before = Pt(space_before)
    p.space_after = Pt(space_after)
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    run.font.name = font
    return p


def header(slide, title, subtitle=None):
    band_h = Inches(1.02) if subtitle else Inches(0.86)
    band = rect(slide, 0, 0, SLIDE_W, band_h, fill=PRIMARY)
    rect(slide, 0, band_h, SLIDE_W, Inches(0.055), fill=ACCENT)
    tf = band.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(0.55)
    tf.margin_right = Inches(0.55)
    tf.margin_top = Inches(0.10)
    tf.margin_bottom = Inches(0.06)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    para(tf, True, title, size=27, bold=True, color=WHITE, space_after=0)
    if subtitle:
        para(tf, False, subtitle, size=12.5, color=RGBColor(0xCF, 0xE4, 0xF7), space_before=2, space_after=0)


def bullets(slide, items, left, top, width, height, size=15.5, gap=7, color=DARK):
    tf = textbox(slide, left, top, width, height)
    first = True
    for item in items:
        if isinstance(item, tuple):
            level, text = item
        else:
            level, text = 0, item
        marker = "" if level else "\u25aa  "
        prefix = "     " * level
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.space_after = Pt(gap if level == 0 else 3)
        p.space_before = Pt(2 if level == 0 else 0)
        run = p.add_run()
        run.text = prefix + (marker if not level else "\u2013  ") + text
        run.font.size = Pt(size if level == 0 else size - 2.5)
        run.font.color.rgb = color if level == 0 else GREY
        run.font.name = BODY_FONT
        run.font.bold = level == 0 and text.endswith(":")
    return tf


def code_block(slide, code, left, top, width, height, size=12.5, title=None):
    box = rect(slide, left, top, width, height, fill=CODE_BG, line=RGBColor(0x33, 0x3B, 0x45),
               shape=MSO_SHAPE.ROUNDED_RECTANGLE, line_w=1.0, radius=0.04)
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(0.18)
    tf.margin_right = Inches(0.14)
    tf.margin_top = Inches(0.12)
    tf.margin_bottom = Inches(0.10)
    first = True
    if title:
        para(tf, True, title, size=size - 1, bold=True, color=RGBColor(0x6F, 0xD0, 0xF0), font=MONO_FONT,
             space_after=5)
        first = False
    for line in code.split("\n"):
        para(tf, first, line if line else " ", size=size, color=CODE_FG, font=MONO_FONT,
             space_after=1.5)
        first = False
    return box


def add_table(slide, data, left, top, width, row_height=0.38, font_size=12,
              col_widths=None, header_fill=PRIMARY, zebra=True, first_col_bold=False):
    rows = len(data)
    cols = len(data[0])
    gf = slide.shapes.add_table(rows, cols, left, top, width, Inches(row_height * rows))
    tbl = gf.table
    tbl.first_row = True
    tbl.horz_banding = False
    if col_widths:
        total = sum(col_widths)
        for i, w in enumerate(col_widths):
            tbl.columns[i].width = Emu(int(width * w / total))
    for r, row in enumerate(data):
        tbl.rows[r].height = Inches(row_height)
        for c in range(cols):
            val = row[c] if c < len(row) else ""
            cell = tbl.cell(r, c)
            cell.margin_left = Inches(0.09)
            cell.margin_right = Inches(0.07)
            cell.margin_top = Inches(0.025)
            cell.margin_bottom = Inches(0.025)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            run = p.add_run()
            run.text = str(val)
            run.font.size = Pt(font_size)
            run.font.name = BODY_FONT
            if r == 0:
                run.font.bold = True
                run.font.color.rgb = WHITE
                cell.fill.solid()
                cell.fill.fore_color.rgb = header_fill
            else:
                run.font.color.rgb = DARK
                if first_col_bold and c == 0:
                    run.font.bold = True
                    run.font.name = MONO_FONT
                    run.font.size = Pt(font_size - 0.5)
                cell.fill.solid()
                cell.fill.fore_color.rgb = LIGHT if (zebra and r % 2 == 0) else WHITE
    return tbl


def dashed_border(shape, color=ACCENT, width_pt=1.75):
    ln = shape.line._get_or_add_ln()
    ln.set("w", str(int(width_pt * 12700)))
    for tag in ("a:prstDash",):
        for el in ln.findall(qn(tag)):
            ln.remove(el)
    dash = ln.makeelement(qn("a:prstDash"), {"val": "dash"})
    ln.append(dash)


def output_slot(slide, caption, hint, left, top, width, height):
    shp = rect(slide, left, top, width, height, fill=RGBColor(0xF7, 0xFA, 0xFD), line=ACCENT,
               shape=MSO_SHAPE.ROUNDED_RECTANGLE, line_w=1.75, radius=0.03)
    dashed_border(shp)
    tf = shp.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = Inches(0.3)
    tf.margin_right = Inches(0.3)
    para(tf, True, caption, size=17, bold=True, color=PRIMARY, align=PP_ALIGN.CENTER, space_after=8)
    para(tf, False, hint, size=12.5, color=GREY, align=PP_ALIGN.CENTER, space_after=0)
    return shp


def caption(slide, text, left, top, width, size=11.5, color=GREY, align=PP_ALIGN.LEFT, bold=False):
    tf = textbox(slide, left, top, width, Inches(0.3))
    para(tf, True, text, size=size, color=color, align=align, bold=bold, italic=not bold, space_after=0)


def note_bar(slide, text, left, top, width, height=Inches(0.5), fill=LIGHT2, color=PRIMARY_DARK,
             size=12.5, icon="\u2139"):
    shp = rect(slide, left, top, width, height, fill=fill, line=None, shape=MSO_SHAPE.ROUNDED_RECTANGLE,
               radius=0.14)
    tf = shp.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = Inches(0.16)
    tf.margin_right = Inches(0.16)
    para(tf, True, f"{icon}  {text}", size=size, color=color, space_after=0)
    return shp


def picture_fit(slide, path, left, top, max_w, max_h, center=False):
    with Image.open(path) as im:
        iw, ih = im.size
    ratio = min(max_w / iw, max_h / ih)
    w = int(iw * ratio)
    h = int(ih * ratio)
    if center:
        left = int((SLIDE_W - w) / 2)
    return slide.shapes.add_picture(str(path), left, top, width=Emu(w), height=Emu(h))


# --------------------------------------------------------------------------
# Terminal-image renderer
# --------------------------------------------------------------------------

def wrap_lines(text, cols):
    out = []
    for line in text.split("\n"):
        line = line.rstrip()
        if len(line) <= cols:
            out.append(line)
            continue
        indent = len(line) - len(line.lstrip(" "))
        cont = " " * min(indent + 2, 8)
        while len(line) > cols:
            cut = line.rfind(" ", 0, cols)
            if cut <= indent:
                cut = cols
            out.append(line[:cut])
            line = cont + line[cut:].lstrip()
        out.append(line)
    return out


def render_terminal(text, path, title=None, cols=104, font_size=17, max_lines=30,
                    bg=(30, 30, 30), fg=(212, 212, 212), accent=(0x6F, 0xD0, 0xF0)):
    font = ImageFont.truetype(TERM_FONT, font_size)
    fontb = ImageFont.truetype(TERM_FONT_B, font_size)
    lines = wrap_lines(text.rstrip("\n"), cols)
    truncated = False
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
    pad = 24
    line_h = int(font_size * 1.5)
    extra = 1 if title else 0
    if truncated:
        extra += 1
    w = pad * 2 + int(cols * font_size * 0.615)
    h = pad * 2 + line_h * (len(lines) + extra)
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    y = pad
    if title:
        d.text((pad, y), title, font=fontb, fill=(255, 255, 255))
        y += line_h
    for ln in lines:
        d.text((pad, y), ln, font=font, fill=fg)
        y += line_h
    if truncated:
        d.text((pad, y), "... (output continues)", font=font, fill=accent)
    img.save(path)
    return path


def load_capture(name):
    p = CAPTURES / f"{name}.txt"
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").strip()


# --------------------------------------------------------------------------
# Build the deck
# --------------------------------------------------------------------------

def build():
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    # ---------------- 1. Title ----------------
    s = blank(prs)
    rect(s, 0, 0, SLIDE_W, SLIDE_H, fill=PRIMARY)
    rect(s, 0, 0, Inches(0.28), SLIDE_H, fill=ACCENT)
    rect(s, Inches(0.9), Inches(4.62), Inches(2.2), Inches(0.06), fill=ACCENT)
    tf = textbox(s, Inches(0.9), Inches(1.9), Inches(11.5), Inches(2.6))
    para(tf, True, "World Bank Documents", size=44, bold=True, color=WHITE, space_after=0)
    para(tf, False, "MCP Server + LLM Agent", size=44, bold=True, color=ACCENT, space_after=0)
    tf2 = textbox(s, Inches(0.9), Inches(4.9), Inches(11.5), Inches(1.6))
    para(tf2, True,
         "An MCP server exposing the public World Bank Documents & Reports API, "
         "plus an agent that answers natural-language questions grounded in real publications.",
         size=15.5, color=RGBColor(0xD3, 0xE4, 0xF4), space_after=14)
    para(tf2, False, "Technical Interview Task      \u2022      Python 3.12  \u00b7  FastMCP  \u00b7  MCP SDK  \u00b7  Groq",
         size=14, bold=True, color=WHITE, space_after=4)
    para(tf2, False, "Submitted by: ______________________          Date: ______________",
         size=12.5, color=RGBColor(0xA9, 0xC7, 0xE4), space_after=0)

    # ---------------- 2. Agenda ----------------
    s = blank(prs)
    header(s, "Agenda", "What this deck covers")
    col1 = [
        "1.  The task and what was required",
        "2.  What was delivered",
        "3.  System architecture",
        "4.  The four MCP tools",
        "5.  Tool design for an LLM",
        "6.  Pydantic validation & error handling",
        "7.  The client agent loop",
    ]
    col2 = [
        "8.   LLM choice and configuration",
        "9.   Live API quirks and fallbacks",
        "10.  Rate limits and robustness",
        "11.  Output screenshots (live runs)",
        "12.  Three worked examples",
        "13.  Testing",
        "14.  How to run  +  design notes",
    ]
    bullets(s, col1, Inches(0.9), Inches(1.5), Inches(5.7), Inches(5.3), size=16, gap=13)
    bullets(s, col2, Inches(7.0), Inches(1.5), Inches(5.7), Inches(5.3), size=16, gap=13)

    # ---------------- 3. Task ----------------
    s = blank(prs)
    header(s, "1. The Task", "Build an MCP server for the World Bank Documents API and drive it with a free LLM")
    bullets(s, [
        "Required: a local MCP server exposing at least four tools over the World Bank Documents & Reports API",
        (1, "search_documents  \u2013  full-text search with language filter + pagination"),
        (1, "filter_documents  \u2013  country / topic / document type / date range"),
        (1, "get_document  \u2013  full metadata for one document by ID"),
        (1, "get_facets  \u2013  discover distinct values for a field"),
        "Required: a client that starts the server as a stdio subprocess, converts MCP schemas to LLM functions, "
        "executes tool calls, feeds results back, and repeats until a final answer",
        "Required: Pydantic validation, informative errors, pagination, LLM-oriented tool descriptions",
        "Required: a README with setup, LLM choice, and 3 example questions with outputs",
    ], Inches(0.9), Inches(1.42), Inches(7.3), Inches(5.4), size=15.5, gap=8)
    box = rect(s, Inches(8.5), Inches(1.5), Inches(4.2), Inches(4.7), fill=LIGHT, line=LIGHT2,
               shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.05)
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(0.28); tf.margin_right = Inches(0.24)
    tf.margin_top = Inches(0.24); tf.margin_bottom = Inches(0.2)
    para(tf, True, "Explicitly OUT of scope", size=15, bold=True, color=AMBER, space_after=10)
    for t in ["World Bank authentication (the API is public)",
              "A frontend or web UI",
              "Deployment or containerisation",
              "A database, caching layer, or RAG",
              "Production observability"]:
        para(tf, False, "\u2715  " + t, size=13, color=GREY, space_after=9)

    # ---------------- 4. Delivered ----------------
    s = blank(prs)
    header(s, "2. What Was Delivered", "Everything the task asked for, plus tests and a no-key smoke test")
    add_table(s, [
        ["Path", "Purpose"],
        ["server/wb_docs_mcp.py", "FastMCP server with the four required tools (stdio transport)"],
        ["server/world_bank_api.py", "Async HTTP client: retries, normalisation, pagination, sorting, filter fallbacks"],
        ["server/schemas.py", "Pydantic request models, annotated tool parameter types, date parsing, facet aliases"],
        ["client/agent.py", "MCP stdio client, MCP\u2192LLM schema conversion, bounded tool-calling loop, CLI"],
        ["client/groq_client.py", "Provider setup and .env loading (Groq or Google Gemini)"],
        ["scripts/smoke_test.py", "Live smoke test of all four tools \u2014 no LLM key required"],
        ["tests/", "5 test modules: offline unit tests + opt-in live integration tests"],
        ["README.md", "Setup, configuration, LLM choice, worked examples, design notes"],
        ["requirements.txt", "Pinned dependency ranges"],
    ], Inches(0.75), Inches(1.35), Inches(11.85), row_height=0.52, font_size=12.5,
        col_widths=[3.0, 8.85], first_col_bold=True)

    # ---------------- 5. Architecture ----------------
    s = blank(prs)
    header(s, "3. Architecture", "Client launches the server itself; the server is the reusable unit")
    boxes = [
        ("Client \u2014 client/agent.py", "CLI / REPL  \u00b7  MCP stdio session  \u00b7  agent loop  \u00b7  provider calls", PRIMARY),
        ("MCP Server \u2014 server/wb_docs_mcp.py", "FastMCP  \u00b7  4 tools  \u00b7  Pydantic validation  \u00b7  stdio transport", PRIMARY_DARK),
        ("HTTP Layer \u2014 server/world_bank_api.py", "httpx async  \u00b7  retries  \u00b7  normalisation  \u00b7  filter fallbacks", TEAL),
        ("World Bank Documents & Reports API", "https://search.worldbank.org/api/v3/wds   (public, no auth)", RGBColor(0x4A, 0x4A, 0x6A)),
    ]
    top = Inches(1.45)
    box_h = Inches(0.82)
    gap = Inches(0.52)
    for i, (title, sub, col) in enumerate(boxes):
        y = top + i * (box_h + gap)
        b = rect(s, Inches(3.05), y, Inches(7.2), box_h, fill=col, line=None,
                 shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.09)
        tf = b.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf.margin_left = Inches(0.24)
        para(tf, True, title, size=15.5, bold=True, color=WHITE, space_after=1)
        para(tf, False, sub, size=11.5, color=RGBColor(0xD8, 0xE8, 0xF6), space_after=0)
        if i < len(boxes) - 1:
            ar = rect(s, Inches(6.45), y + box_h + Inches(0.05), Inches(0.4), Inches(0.42),
                      fill=ACCENT, shape=MSO_SHAPE.DOWN_ARROW)
    note_bar(s, "The server subprocess deliberately does NOT inherit GROQ_API_KEY \u2014 only the client needs it.",
             Inches(0.75), Inches(6.35), Inches(11.85), Inches(0.5))

    # ---------------- 6. Four tools ----------------
    s = blank(prs)
    header(s, "4. The Four MCP Tools", "Named and described for an LLM, not for a human API wrapper")
    add_table(s, [
        ["Tool", "What it does", "Key API parameters"],
        ["search_documents", "Full-text search with optional language filter, pagination and sorting",
         "qterm, fl, rows, os, lang_exact, srt"],
        ["filter_documents", "Structured filter by country, region, topic, document type and/or date range",
         "count_exact, admreg_exact, topic_exact, docty_exact, strdate, enddate"],
        ["get_document", "Full metadata for a single document by numeric ID or GUID",
         "id / guid, fl"],
        ["get_facets", "All distinct values for a field, for discovery questions",
         "fct, qterm"],
    ], Inches(0.75), Inches(1.4), Inches(11.85), row_height=0.78, font_size=12.5,
        col_widths=[2.5, 5.6, 3.75], first_col_bold=True)
    note_bar(s, "All four tools validate inputs with Pydantic and return pagination metadata "
                "(total, returned, has_more, next_offset) so the agent can page through results.",
             Inches(0.75), Inches(5.75), Inches(11.85), Inches(0.62))

    # ---------------- 7. Tool design for LLM ----------------
    s = blank(prs)
    header(s, "5. Tool Design for an LLM", "Descriptions are part of the interface \u2014 they decide which tool is called")
    code_block(s, '''@mcp.tool
def filter_documents(
    country: CountryParam = None,
    topic: TopicParam = None,
    document_type: DocTypeParam = None,
    start_date: DateParam = None,
    end_date: DateParam = None,
    qterm: QtermParam = None,
    rows: RowsParam = 10,
    offset: OffsetParam = 0,
    sort: SortParam = "relevance",
) -> dict:
    """Filter World Bank documents by structured fields.

    WHEN TO USE: the question names a country, region, topic/sector,
    document type and/or a date range, e.g. "education financing in
    Sub-Saharan Africa between 2019 and 2022". Prefer this over
    search_documents whenever a structured filter is available.

    ...
    """''', Inches(0.75), Inches(1.4), Inches(7.4), Inches(5.35), size=11.5,
        title="server/wb_docs_mcp.py  \u2014  tool signature + docstring")
    bullets(s, [
        "When to use the tool",
        "What every argument means",
        "What the result contains",
        "Units, formats, and defaults",
        "Where to discover valid values (get_facets)",
    ], Inches(8.5), Inches(1.55), Inches(4.2), Inches(3.0), size=14, gap=11)
    note_bar(s, "Narrow schemas + explicit descriptions let a general-purpose "
                "open model pick the right tool reliably.",
             Inches(8.5), Inches(4.6), Inches(4.2), Inches(1.6), fill=RGBColor(0xE8, 0xF5, 0xEC),
             color=GREEN, icon="\u2713")

    # ---------------- 8. Validation ----------------
    s = blank(prs)
    header(s, "6. Pydantic Validation & Error Handling", "Bad input never reaches the World Bank API")
    code_block(s, '''# server/schemas.py  \u2014  annotated parameter types
QtermParam = Annotated[str, StringConstraints(min_length=1, max_length=300)]
RowsParam  = Annotated[int, Field(ge=1, le=50)]
OffsetParam = Annotated[int, Field(ge=0, le=100_000)]
DateParam  = Annotated[Optional[date], BeforeValidator(parse_flexible_date)]''',
               Inches(0.75), Inches(1.4), Inches(6.0), Inches(2.0), size=11.5,
               title="Declarative bounds")
    code_block(s, '''{
  "error": "Invalid arguments for filter_documents",
  "field": "end_date",
  "detail": "start_date (2019-06-01) is after end_date (2019-01-01)"
}''', Inches(7.05), Inches(1.4), Inches(5.55), Inches(2.0), size=11.5,
        title="Informative tool error returned to the model")
    add_table(s, [
        ["Situation", "Behaviour"],
        ["Invalid tool arguments", "Pydantic rejects before any HTTP call; ToolError names the field"],
        ["Malformed JSON from the model", "Parse error fed back as a tool result so the model can retry"],
        ["World Bank 4xx", "Returned immediately as an informative error (status + detail + URL)"],
        ["World Bank 429 / 5xx", "Retried up to 3 times with exponential backoff (0.6s, 1.2s)"],
        ["Timeout / network failure", "Clear \u201ctimed out\u201d / \u201ccould not reach\u201d message, with retry"],
        ["Unknown document ID", "get_document raises an error explaining no document was found"],
        ["Zero results", "Not an error \u2014 the agent broadens the query and says nothing matched"],
    ], Inches(0.75), Inches(3.55), Inches(11.85), row_height=0.40, font_size=12,
        col_widths=[3.5, 8.35])

    # ---------------- 9. Agent loop ----------------
    s = blank(prs)
    header(s, "7. The Client Agent Loop", "client/agent.py \u2014 the nine steps required by the task")
    steps_l = [
        "1.  Start the MCP server as a subprocess (stdio_client, sys.executable -m server.wb_docs_mcp)",
        "2.  Discover tools dynamically with session.list_tools() \u2014 nothing hard-coded",
        "3.  Convert each MCP tool to an LLM function definition",
        "4.  Send the question + tools with tool_choice=\"auto\"",
        "5.  Append the assistant message with its tool_calls array exactly as returned",
    ]
    steps_r = [
        "6.  Execute every requested tool through the MCP session; parse the JSON arguments",
        "7.  Return each result as {role: tool, tool_call_id: \u2026, content: \u2026}",
        "8.  Repeat until the model returns a message with no tool calls",
        "9.  Bound the loop: after N rounds, force a final answer with tools disabled",
    ]
    bullets(s, steps_l, Inches(0.85), Inches(1.5), Inches(5.8), Inches(4.4), size=14.5, gap=13)
    bullets(s, steps_r, Inches(6.95), Inches(1.5), Inches(5.8), Inches(4.4), size=14.5, gap=13)
    note_bar(s, "Oversized tool results are compacted (short abstracts, no facet blocks) and the whole "
                "conversation is kept under a token budget \u2014 see slide 10.",
             Inches(0.85), Inches(6.25), Inches(11.9), Inches(0.62), fill=RGBColor(0xFF, 0xF4, 0xE0),
             color=AMBER, icon="\u26a0")

    # ---------------- 10. Schema conversion ----------------
    s = blank(prs)
    header(s, "7b. MCP \u2192 LLM Schema Conversion", "Schemas are translated at runtime, so the client is provider-agnostic")
    code_block(s, '''def convert_mcp_tools(mcp_tools):
    """MCP tool definitions -> OpenAI-style function definitions."""
    functions = []
    for tool in mcp_tools:
        schema = tool.inputSchema or {"type": "object", "properties": {}}
        schema = _flatten_anyof(schema)          # anyOf:[X, null] -> X
        schema.pop("additionalProperties", None)  # small models prefer simpler schemas
        functions.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": schema,
            },
        })
    return functions''', Inches(0.75), Inches(1.4), Inches(7.5), Inches(3.5), size=12,
        title="client/agent.py")
    bullets(s, [
        "No tool names or schemas hard-coded",
        "Optional fields flattened for reliability",
        "tool_call_id preserved for every result",
        "Tool errors become JSON tool results",
    ], Inches(8.6), Inches(1.6), Inches(4.0), Inches(3.2), size=14, gap=12)
    note_bar(s, "The same loop drives Groq, Gemini, or a local Ollama server \u2014 only the "
                "base URL and key change.",
             Inches(0.75), Inches(5.2), Inches(11.85), Inches(0.6))

    # ---------------- 11. LLM choice ----------------
    s = blank(prs)
    header(s, "8. LLM Choice & Configuration", "Default: Groq openai/gpt-oss-120b \u2014 free, fast, native tool calling")
    bullets(s, [
        "Groq (default) \u2014 free tier, very low latency, native function/tool calling via the OpenAI-compatible API",
        (1, "Model: openai/gpt-oss-120b  (openai/gpt-oss-20b and qwen/qwen3.8-27b also work)"),
        "Google Gemini \u2014 supported through its OpenAI-compatible endpoint",
        (1, "LLM_PROVIDER=gemini  +  GEMINI_API_KEY  +  GEMINI_MODEL=gemini-2.5-flash"),
        "Ollama / any OpenAI-compatible server \u2014 set GROQ_BASE_URL (e.g. http://localhost:11434/v1)",
        "Provider is auto-detected when only GEMINI_API_KEY is present; --model overrides per run",
    ], Inches(0.85), Inches(1.5), Inches(7.3), Inches(4.6), size=15, gap=11)
    add_table(s, [
        ["Variable", "Default"],
        ["GROQ_MODEL", "openai/gpt-oss-120b"],
        ["GEMINI_MODEL", "gemini-2.5-flash"],
        ["AGENT_MAX_ITERATIONS", "6"],
        ["AGENT_TEMPERATURE", "0.1"],
        ["MAX_CONTEXT_TOKENS", "6000"],
        ["MAX_TOOL_RESULT_CHARS", "12000"],
    ], Inches(8.6), Inches(1.5), Inches(4.0), row_height=0.44, font_size=12,
        col_widths=[3.0, 3.0], first_col_bold=True)
    note_bar(s, "Verified:  python -m client.agent --check-llm", Inches(0.85), Inches(6.15),
             Inches(11.85), Inches(0.55), fill=RGBColor(0xE8, 0xF5, 0xEC), color=GREEN, icon="\u2713")

    # ---------------- 12. API quirks ----------------
    s = blank(prs)
    header(s, "9. Live API Quirks & Fallbacks", "The server absorbs the WDS API's rough edges so the LLM does not have to")
    add_table(s, [
        ["Quirk in the live API", "How the server handles it"],
        ["documents is an object, and contains a facets key", "Normaliser drops facets before iterating"],
        ["Modern topic_exact fields often return 0 results",
         "filter_documents retries topic_exact \u2192 teratopic_exact \u2192 sectr_exact, and reports the fallback in notes"],
        ["Region names are not country names",
         "\u201cSub-Saharan Africa\u201d \u2192 admreg_exact = Eastern and Southern Africa^Western and Central Africa"],
        ["Document URLs use GUIDs, not numeric IDs", "get_document looks up id first, then retries with guid"],
        ["The API requires ISO dates (20240101 can cause HTTP 500)",
         "Schema accepts YYYY-MM-DD, slash/dash variants and bare years, then sends ISO dates"],
        ["Sorting needs explicit parameters", "sort=newest|oldest maps to srt=docdt&order=desc|asc"],
    ], Inches(0.75), Inches(1.4), Inches(11.85), row_height=0.62, font_size=12,
        col_widths=[4.6, 7.25])
    note_bar(s, "Every fallback that fires is surfaced to the model in the tool result's notes field, "
                "so the answer can explain what it did.",
             Inches(0.75), Inches(5.9), Inches(11.85), Inches(0.6))

    # ---------------- 13. Rate limits ----------------
    s = blank(prs)
    header(s, "10. Free-Tier Rate Limits & Robustness", "The Groq free tier caps 8,000 tokens/minute \u2014 the client stays inside it")
    bullets(s, [
        "Slim results before sending: short abstracts (320 chars), embedded facet blocks dropped",
        "Keep the whole conversation under MAX_CONTEXT_TOKENS (6000); older tool results are replaced with a one-line note",
        "On 413 (too large) or 429 (rate limit): trim the context, honour Retry-After, retry up to 3 times",
        "Truncate any single tool result to MAX_TOOL_RESULT_CHARS while keeping valid JSON",
        "Unwrap MCP ExceptionGroups so the real provider error is reported instead of a masked teardown error",
        "On finish_reason=length, note the truncation instead of silently returning a partial answer",
    ], Inches(0.85), Inches(1.5), Inches(11.9), Inches(4.4), size=15, gap=13)
    note_bar(s, "Diagnosed live: a 413 \u201crequest too large\u201d from the 8,000 TPM cap was being masked by the MCP "
                "task-group teardown. Fixing the unwrap + trimming made the agent reliable on the free tier.",
             Inches(0.85), Inches(5.9), Inches(11.9), Inches(0.9), fill=RGBColor(0xFF, 0xF4, 0xE0),
             color=AMBER, icon="\u26a0")

    # ---------------- 14-18. Output slides ----------------
    kenya = load_capture("agent_kenya")
    doc_types = load_capture("agent_doc_types")
    pytest_out = load_capture("pytest")
    list_tools = load_capture("list_tools")
    check_llm = load_capture("check_llm")

    def split_trace(text):
        trace, answer = [], []
        for line in text.split("\n"):
            (trace if line.startswith("[tool]") else answer).append(line)
        return "\n".join(trace).strip(), "\n".join(answer).strip()

    kenya_trace, kenya_answer = split_trace(kenya)
    dt_trace, dt_answer = split_trace(doc_types)

    imgs = {}
    if kenya_answer:
        imgs["agent"] = render_terminal(
            kenya_answer, SHOTS / "01_agent_answer.png",
            title="> python -m client.agent \"What World Bank reports exist on climate resilience in Kenya?\"",
            cols=96, max_lines=20)
    if kenya_trace or dt_trace:
        imgs["trace"] = render_terminal(
            (kenya_trace + "\n" + dt_trace).strip(), SHOTS / "02_tool_trace.png",
            title="> python -m client.agent --show-tools \"...\"     (stderr trace)",
            cols=96, max_lines=18)
    if pytest_out:
        imgs["pytest"] = render_terminal(
            pytest_out, SHOTS / "03_pytest.png",
            title="> python -m pytest", cols=72, max_lines=18)
    if dt_answer:
        imgs["facets"] = render_terminal(
            dt_answer, SHOTS / "04_document_types.png",
            title="> python -m client.agent \"What document types does the World Bank publish most frequently?\"",
            cols=96, max_lines=20)
    if list_tools:
        imgs["listtools"] = render_terminal(
            list_tools, SHOTS / "05_list_tools.png",
            title="> python -m client.agent --list-tools", cols=104, max_lines=26)
    if check_llm:
        imgs["checkllm"] = render_terminal(
            check_llm, SHOTS / "06_check_llm.png",
            title="> python -m client.agent --check-llm", cols=80, max_lines=8)

    # ---- Output 1: agent answer
    s = blank(prs)
    header(s, "11. Output \u2014 Agent Answer", "Live run: Groq + the real stdio MCP server + the live World Bank API")
    if "agent" in imgs:
        picture_fit(s, imgs["agent"], Inches(0.75), Inches(1.35), Inches(11.85), Inches(5.3))
        caption(s, "Captured from the live run \u2014 replace with your own screenshot if you prefer.",
                Inches(0.75), Inches(6.68), Inches(11.85))
    else:
        output_slot(s, "Paste screenshot: agent answer", "run:  python -m client.agent \"...\"",
                    Inches(0.75), Inches(1.35), Inches(11.85), Inches(5.3))

    # ---- Output 2: tool trace
    s = blank(prs)
    header(s, "11b. Output \u2014 Tool-Call Trace", "--show-tools prints each tool call and its size to stderr")
    if "trace" in imgs:
        picture_fit(s, imgs["trace"], Inches(0.75), Inches(1.35), Inches(11.85), Inches(5.3))
        caption(s, "Captured from the live run \u2014 replace with your own screenshot if you prefer.",
                Inches(0.75), Inches(6.68), Inches(11.85))
    else:
        output_slot(s, "Paste screenshot: tool-call trace", "run:  python -m client.agent --show-tools \"...\"",
                    Inches(0.75), Inches(1.35), Inches(11.85), Inches(5.3))

    # ---- Output 3: tests
    s = blank(prs)
    header(s, "11c. Output \u2014 Test Suite", "The offline suite mocks HTTP, the MCP session and the LLM")
    if "pytest" in imgs:
        picture_fit(s, imgs["pytest"], 0, Inches(1.45), Inches(8.6), Inches(3.2), center=True)
        caption(s, "Captured from the live run.", Inches(0.75), Inches(4.85), Inches(11.85),
                align=PP_ALIGN.CENTER)
    else:
        output_slot(s, "Paste screenshot: pytest results", "run:  python -m pytest",
                    Inches(2.6), Inches(1.5), Inches(8.2), Inches(3.6))
    bullets(s, [
        "Offline suite \u2014 no network or API key needed",
        "2 live integration tests are opt-in (RUN_WB_INTEGRATION=1)",
        "Covers URL building, normalisation, retries, GUID/topic fallbacks,",
        (1, "Pydantic validation, the agent loop, token trimming and 413/429 retries"),
    ], Inches(2.9), Inches(5.25), Inches(7.6), Inches(1.6), size=13.5, gap=4)

    # ---- Output 4: facets
    s = blank(prs)
    header(s, "11d. Output \u2014 Facet Discovery", "get_facets answers \u201cwhat values exist?\u201d discovery questions")
    if "facets" in imgs:
        picture_fit(s, imgs["facets"], Inches(0.75), Inches(1.35), Inches(11.85), Inches(5.3))
        caption(s, "Captured from the live run \u2014 replace with your own screenshot if you prefer.",
                Inches(0.75), Inches(6.68), Inches(11.85))
    else:
        output_slot(s, "Paste screenshot: facet discovery", "run:  python -m client.agent \"What document types ...?\"",
                    Inches(0.75), Inches(1.35), Inches(11.85), Inches(5.3))

    # ---- Optional: your own screenshots
    s = blank(prs)
    header(s, "11e. Output \u2014 Your Own Screenshots", "Optional: drop in any additional terminal captures")
    output_slot(s, "Paste screenshot 1", "e.g.  python scripts/smoke_test.py   \u00b7   python -m client.agent --interactive",
                Inches(0.75), Inches(1.35), Inches(5.8), Inches(5.3))
    output_slot(s, "Paste screenshot 2", "e.g.  MCP Inspector  \u00b7  npx @modelcontextprotocol/inspector python -m server.wb_docs_mcp",
                Inches(6.8), Inches(1.35), Inches(5.8), Inches(5.3))

    # ---------------- 19-21. Worked examples ----------------
    def example_slide(num, title, question, tool_json, summary_lines, answer_lines):
        s = blank(prs)
        header(s, f"12.{num} Example {num} \u2014 {title}", "Question \u2192 tool call \u2192 live World Bank data \u2192 cited answer")
        tf = textbox(s, Inches(0.8), Inches(1.35), Inches(11.9), Inches(0.6))
        para(tf, True, f"Q:  {question}", size=15, bold=True, color=PRIMARY_DARK, space_after=0)
        code_block(s, tool_json, Inches(0.8), Inches(2.0), Inches(5.8), Inches(1.7), size=11.5,
                   title="Tool call chosen by the model")
        code_block(s, summary_lines, Inches(0.8), Inches(3.85), Inches(5.8), Inches(2.9), size=11.5,
                   title="Tool result (live API)")
        bullets(s, answer_lines, Inches(6.95), Inches(2.05), Inches(5.7), Inches(4.6), size=13, gap=9)

    example_slide(
        1, "Climate Resilience in Kenya",
        "What World Bank reports exist on climate resilience in Kenya?",
        '{\n  "name": "search_documents",\n  "arguments": {\n    "qterm": "climate resilience Kenya",\n    "rows": 5\n  }\n}',
        "total: 962\nreturned: 5\ndocuments:\n  33295056 | 2021-07-01 | Resettlement Plan\n  ...      | 2015-07-29 | Auditing Document\n  ...      | 2021-02-01 | Resettlement Plan\n  ...      | 2020-12-15 | Project Paper\n  ...      | 2015-12-02 | ISR Report",
        [
            "962 matching documents",
            "Kenya \u2013 Coastal Region Water Security and Climate Resilience Project: Resettlement Plan (Vol. 4 of 4), July 2021",
            "Kenya \u2013 Water Security and Climate Resilience Project: audited financial statement, 2015",
            "Disclosable Restructuring Paper \u2013 Kenya Water Security and Climate Resilience Project (P117635), Dec 2020",
            "Each result cited with title, type, date and URL",
        ])

    example_slide(
        2, "Education Financing, Sub-Saharan Africa 2019\u20132022",
        "Find documents about education financing in Sub-Saharan Africa published between 2019 and 2022.",
        '{\n  "name": "filter_documents",\n  "arguments": {\n    "country": "Sub-Saharan Africa",\n    "topic": "Education",\n    "qterm": "financing",\n    "start_date": "2019",\n    "end_date": "2022",\n    "rows": 5\n  }\n}',
        'total: 4148\nfilters:\n  admreg_exact: Eastern and Southern\n                Africa^Western and Central Africa\n  strdate: 2019-01-01\n  enddate: 2022-12-31\n  teratopic_exact: Education\nnotes:\n  - country mapped to a WB region\n  - topic_exact empty -> teratopic_exact',
        [
            "4,148 matching documents",
            "Ethiopia \u2013 Education Results Based Financing Project (ICR, Dec 2019)",
            "Mozambique \u2013 Can Results-Based Financing Ensure Books Reach Students in Time? (2021)",
            "South Africa \u2013 Results-Based Financing and the Book Supply Chain (2019)",
            "The answer explains the region mapping and the topic-field fallback",
        ])

    example_slide(
        3, "Most Frequent Document Types",
        "What document types does the World Bank publish most frequently?",
        '{\n  "name": "get_facets",\n  "arguments": {\n    "field": "document_type",\n    "limit": 5\n  }\n}',
        "total_matching_documents: 623114\ntotal_values: 100\nvalues:\n  Procurement Plan              144,407\n  Implementation Status and\n    Results Report               59,904\n  Auditing Document              30,490\n  Working Paper                  27,069\n  Brief                          24,818",
        [
            "623,114 documents in total",
            "1. Procurement Plan \u2014 144,407",
            "2. Implementation Status and Results Report \u2014 59,904",
            "3. Auditing Document \u2014 30,490",
            "4. Working Paper \u2014 27,069   \u00b7   5. Brief \u2014 24,818",
            "Procurement Plans + ISRs are roughly one third of the collection",
        ])

    # ---------------- 22. Testing ----------------
    s = blank(prs)
    header(s, "13. Testing", "66 passed, 2 skipped offline \u2014 plus opt-in live integration tests")
    add_table(s, [
        ["Test module", "What it covers"],
        ["tests/test_api.py", "URL construction, normalisation, facets, sorting, retries, timeouts, GUID + topic fallbacks"],
        ["tests/test_tools.py", "In-memory FastMCP Client calls, Pydantic validation, pagination metadata, lookup errors"],
        ["tests/test_agent.py", "Schema conversion, anyOf flattening, multi-turn tool loops, tool_call_id correlation, "
                                "truncation, iteration bounds, token trimming, 413/429 retries"],
        ["tests/test_llm_config.py", "Provider auto-detection, explicit selection, model defaults, configuration errors"],
        ["tests/test_integration.py", "Live WDS API + real stdio server startup (opt-in: RUN_WB_INTEGRATION=1)"],
    ], Inches(0.75), Inches(1.35), Inches(11.85), row_height=0.60, font_size=12,
        col_widths=[2.9, 8.95], first_col_bold=True)
    if "checkllm" in imgs:
        picture_fit(s, imgs["checkllm"], Inches(0.75), Inches(5.2), Inches(6.0), Inches(1.5))
    note_bar(s, "An end-to-end run with the real Groq SDK, the real stdio server and the live API "
                "confirmed correct tool_call_id correlation and full message history.",
             Inches(7.2), Inches(5.25), Inches(5.4), Inches(1.5),
             fill=RGBColor(0xE8, 0xF5, 0xEC), color=GREEN, icon="\u2713")

    # ---------------- 23. Structure ----------------
    s = blank(prs)
    header(s, "14. Project Structure", "Server and client are separate; the server is the reusable unit")
    code_block(s, '''world-bank-mcp/
|-- client/
|   |-- agent.py          # stdio session, schema conversion, agent loop, CLI
|   `-- groq_client.py    # .env loading, provider setup, env defaults
|-- server/
|   |-- schemas.py        # Pydantic models + annotated tool parameter types
|   |-- wb_docs_mcp.py    # FastMCP server and the four tools
|   `-- world_bank_api.py # HTTP client, retries, normalisation, fallbacks
|-- scripts/
|   `-- smoke_test.py     # live tool smoke test without an LLM
|-- tests/
|   |-- test_agent.py   test_api.py   test_tools.py
|   |-- test_llm_config.py   test_integration.py
|   `-- conftest.py
|-- .env.example   .gitignore   pytest.ini
|-- requirements.txt
`-- README.md''', Inches(0.75), Inches(1.4), Inches(7.2), Inches(5.35), size=11.5,
        title="world-bank-mcp/")
    bullets(s, [
        "Server knows nothing about LLMs",
        "Client knows nothing about the World Bank API",
        "Any MCP-compatible client can reuse the server",
        "Tests are separate from source",
        "No secrets committed (.env is gitignored)",
    ], Inches(8.35), Inches(1.6), Inches(4.25), Inches(4.6), size=13.5, gap=12)

    # ---------------- 24. How to run ----------------
    s = blank(prs)
    header(s, "15. How to Run", "Python 3.12 + one free API key")
    code_block(s, '''# 1. install
python -m venv .venv
.venv\\Scripts\\Activate.ps1        # Windows PowerShell
pip install -r requirements.txt

# 2. configure  (copy .env.example to .env)
GROQ_API_KEY=your_free_groq_key
GROQ_MODEL=openai/gpt-oss-120b

# 3. verify the provider, then ask a question
python -m client.agent --check-llm
python -m client.agent "What World Bank reports exist on climate resilience in Kenya?"

# other useful commands
python -m client.agent --interactive         # REPL, one question at a time
python -m client.agent --show-tools "..."    # trace tool calls on stderr
python scripts/smoke_test.py                 # test the tools with no LLM key
python -m pytest                             # offline test suite''',
               Inches(0.75), Inches(1.4), Inches(11.85), Inches(5.4), size=12,
               title="PowerShell / bash")

    # ---------------- 25. Design notes ----------------
    s = blank(prs)
    header(s, "16. Design Notes & Limitations", "Honest about what it does and does not do")
    bullets(s, [
        "The MCP server is the unit of reuse \u2014 the client only knows MCP tools",
        "Tool descriptions are treated as part of the interface, not documentation",
        "No caching: every answer reflects the live API (honest, but repeats HTTP calls)",
        "Relevance ranking is the API's; the sort parameter exists for \u201clatest\u201d questions",
        "Facet values are API-capped at 100 distinct values per field",
        "docdt is the disclosure date, not necessarily the completion date of the work",
        "Model variability: gpt-oss-120b is free but can occasionally over-explore; the system prompt, "
        "bounded loop and token budget reduce this",
        "No streaming, persistence or UI \u2014 explicitly out of scope",
    ], Inches(0.85), Inches(1.5), Inches(11.9), Inches(5.2), size=15, gap=11)

    # ---------------- 26. Summary ----------------
    s = blank(prs)
    header(s, "17. Summary", "What was built and why it is worth a look")
    cards = [
        ("4 MCP tools", "search \u00b7 filter \u00b7 get_document \u00b7 facets, all Pydantic-validated with pagination", PRIMARY),
        ("Real agent loop", "Dynamic tool discovery, tool_call_id correlation, bounded iterations", TEAL),
        ("Provider-agnostic", "Groq by default; Gemini and Ollama need only config changes", PRIMARY_DARK),
        ("Production-minded", "Retries, fallbacks, token budgeting, informative errors, 66 passing tests", GREEN),
    ]
    x = Inches(0.8)
    for i, (t, d, col) in enumerate(cards):
        cx = Inches(0.8) + i * Inches(3.06)
        b = rect(s, cx, Inches(1.55), Inches(2.85), Inches(2.5), fill=LIGHT, line=col,
                 shape=MSO_SHAPE.ROUNDED_RECTANGLE, line_w=1.75, radius=0.06)
        tf = b.text_frame
        tf.word_wrap = True
        tf.margin_left = Inches(0.2); tf.margin_right = Inches(0.18)
        tf.margin_top = Inches(0.22)
        para(tf, True, t, size=16, bold=True, color=col, space_after=8)
        para(tf, False, d, size=12.5, color=GREY, space_after=0)
    note_bar(s, "End-to-end: a question goes in, the model picks MCP tools, the server calls the live World Bank API, "
                "and a cited answer comes out.",
             Inches(0.8), Inches(4.35), Inches(11.7), Inches(0.85), fill=RGBColor(0xE8, 0xF5, 0xEC),
             color=GREEN, icon="\u2713")
    tf = textbox(s, Inches(0.8), Inches(5.5), Inches(11.7), Inches(1.2))
    para(tf, True, "Thank you  \u2014  questions welcome.", size=26, bold=True, color=PRIMARY, space_after=6)
    para(tf, False, "Repository contents: server/  \u00b7  client/  \u00b7  tests/  \u00b7  scripts/  \u00b7  requirements.txt  \u00b7  README.md",
         size=13, color=GREY, space_after=0)

    # ---------------- footers ----------------
    total = len(prs.slides)
    for i, slide in enumerate(prs.slides, start=1):
        if i == 1:
            continue
        tf = textbox(slide, Inches(0.55), Inches(7.02), Inches(9.0), Inches(0.32))
        para(tf, True, "World Bank Documents MCP Server  \u00b7  Technical Interview Task",
             size=9.5, color=RGBColor(0x9A, 0xA3, 0xAD), space_after=0)
        tf2 = textbox(slide, Inches(12.2), Inches(7.02), Inches(0.9), Inches(0.32))
        para(tf2, True, f"{i} / {total}", size=9.5, color=RGBColor(0x9A, 0xA3, 0xAD),
             align=PP_ALIGN.RIGHT, space_after=0)

    prs.save(str(PPTX))
    print(f"slides: {total}")
    print(f"saved:  {PPTX}")
    for name, p in sorted(imgs.items()):
        print(f"image:  {p}")


if __name__ == "__main__":
    build()
