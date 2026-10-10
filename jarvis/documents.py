"""CPU-only document exports. Office Open XML uses the standard library only.

No model calls, macros, remote images, or active HTML. PDF uses an isolated,
GPU-disabled Chromium browser. Existing browser profiles are never reused.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import html
from io import BytesIO
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import unicodedata
from xml.sax.saxutils import escape, quoteattr
from zipfile import ZipFile, ZIP_DEFLATED


MAX_TEXT = 500_000
MAX_DOWNLOAD = 32 * 1024 * 1024
FORMATS = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
}
ALIASES = {"excel": "xlsx", "엑셀": "xlsx", "word": "docx", "워드": "docx",
           "ppt": "pptx", "powerpoint": "pptx"}
NAME_RE = re.compile(r"alex-document-\d{8}-\d{6}-[a-f0-9]{16}\.(?:xlsx|docx|pptx|pdf)\Z")
XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"


class DocumentError(ValueError):
    """A document cannot be created without losing or misrepresenting input."""


@dataclass
class Block:
    kind: str
    text: str = ""
    level: int = 0
    rows: list[list[str]] = field(default_factory=list)


def format_name(value: str) -> str:
    name = value.strip().lower()
    name = ALIASES.get(name, name)
    if name not in FORMATS:
        raise DocumentError("형식은 xlsx·pdf·pptx·docx 중에서 선택하세요.")
    return name


def _text(value: str) -> str:
    # Reject XML-invalid controls/surrogates rather than emitting corrupt Office files.
    if any(not (c in "\t\n\r" or 0x20 <= ord(c) <= 0xD7FF
                or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF) for c in value):
        raise DocumentError("문서에 저장할 수 없는 제어 문자가 있습니다.")
    return escape(value)


def plain(text: str) -> str:
    text = re.sub(r"!?\[([^\]\n]*)\]\(([^)\n]+)\)", r"\1 (\2)", text)
    text = re.sub(r"\*\*([^*]+)\*\*|__([^_]+)__", lambda m: m[1] or m[2], text)
    return re.sub(r"`([^`]+)`", r"\1", text)


def _cells(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|") and not value.endswith(r"\|"):
        value = value[:-1]
    return [plain(c.strip().replace(r"\|", "|")) for c in re.split(r"(?<!\\)\|", value)]


def parse_markdown(text: str) -> list[Block]:
    lines, out = text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        fence = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence:
            marker, code = fence[1], []
            i += 1
            while i < len(lines) and not re.match(r"^\s*" + re.escape(marker) + r"\s*$", lines[i]):
                code.append(lines[i]); i += 1
            out.append(Block("code", "\n".join(code)))
            if i < len(lines):
                i += 1
            continue
        # A separator row, not just a pipe in prose, is necessary for a table.
        if i + 1 < len(lines) and "|" in line:
            separator = _cells(lines[i + 1])
            header = _cells(line)
            if separator and len(separator) == len(header) and all(re.fullmatch(r":?-{3,}:?", c) for c in separator):
                rows = [header]; i += 2
                while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                    rows.append(_cells(lines[i])); i += 1
                width = max(len(row) for row in rows)
                out.append(Block("table", rows=[row + [""] * (width - len(row)) for row in rows]))
                continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        bullet = re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+)$", line)
        if heading:
            out.append(Block("heading", plain(heading[2]), len(heading[1])))
        elif bullet:
            out.append(Block("list", plain(line.strip())))
        elif re.fullmatch(r"\s*(?:---+|\*\*\*+|___+)\s*", line):
            out.append(Block("rule"))
        else:
            out.append(Block("paragraph", plain(line)))
        i += 1
    return out


def _rels(items: list[tuple[str, str, str]]) -> str:
    return XML + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(
        f'<Relationship Id={quoteattr(rid)} Type={quoteattr(REL + kind)} Target={quoteattr(target)}/>'
        for rid, kind, target in items) + "</Relationships>"


def _package(parts: dict[str, str], types: dict[str, str]) -> bytes:
    content = XML + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    content += '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    content += '<Default Extension="xml" ContentType="application/xml"/>'
    content += "".join(f'<Override PartName={quoteattr("/" + path)} ContentType={quoteattr(mime)}/>' for path, mime in types.items())
    content += "</Types>"
    raw = BytesIO()
    with ZipFile(raw, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content.encode("utf-8"))
        for name, body in parts.items():
            archive.writestr(name, body.encode("utf-8"))
    return raw.getvalue()


def _wpara(text: str, style: str = "Normal") -> str:
    runs = '<w:br/>'.join(f'<w:t xml:space="preserve">{_text(t)}</w:t>' for t in text.split("\n"))
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r>{runs}</w:r></w:p>'


def _docx(title: str, blocks: list[Block]) -> bytes:
    body = [_wpara(title, "Title")]
    for block in blocks:
        if block.kind == "table":
            width = max(1, 9360 // len(block.rows[0]))
            table = '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders>'
            table += ''.join(f'<w:{side} w:val="single" w:sz="4" w:color="C7D2E0"/>' for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
            table += '</w:tblBorders></w:tblPr><w:tblGrid>' + f'<w:gridCol w:w="{width}"/>' * len(block.rows[0]) + '</w:tblGrid>'
            for index, row in enumerate(block.rows):
                table += '<w:tr><w:trPr><w:cantSplit/>' + ('<w:tblHeader/>' if index == 0 else '') + '</w:trPr>'
                for cell in row:
                    table += f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>' + ('<w:shd w:fill="E8EEF6"/>' if index == 0 else '') + '</w:tcPr>' + _wpara(cell) + '</w:tc>'
                table += '</w:tr>'
            body.append(table + '</w:tbl>')
        elif block.kind != "rule":
            style = "Heading" + str(min(3, block.level)) if block.kind == "heading" else ("Code" if block.kind == "code" else "Normal")
            body.append(_wpara(block.text, style))
    body.append('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr>')
    styles = XML + f'<w:styles xmlns:w="{W}"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="맑은 고딕"/><w:sz w:val="22"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
    for name, size in (("Normal", 22), ("Title", 40), ("Heading1", 32), ("Heading2", 28), ("Heading3", 24), ("Code", 18)):
        styles += f'<w:style w:type="paragraph" w:styleId="{name}"' + (' w:default="1"' if name == "Normal" else '') + f'><w:name w:val="{name}"/>'
        if name != "Normal":
            styles += '<w:basedOn w:val="Normal"/>'
        if name.startswith("Heading") or name == "Title":
            styles += '<w:pPr><w:keepNext/><w:spacing w:before="200" w:after="120"/></w:pPr>'
        styles += f'<w:rPr><w:sz w:val="{size}"/>' + ('<w:b/><w:color w:val="183E64"/>' if name.startswith("Heading") or name == "Title" else '')
        if name == "Code":
            styles += '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:eastAsia="맑은 고딕"/>'
        styles += '</w:rPr></w:style>'
    parts = {"_rels/.rels": _rels([("rId1", "officeDocument", "word/document.xml")]),
             "word/document.xml": XML + f'<w:document xmlns:w="{W}"><w:body>' + ''.join(body) + '</w:body></w:document>',
             "word/styles.xml": styles + '</w:styles>',
             "word/_rels/document.xml.rels": _rels([("rId1", "styles", "styles.xml")])}
    return _package(parts, {"word/document.xml": FORMATS["docx"] + ".main+xml",
                            "word/styles.xml": "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"})


def _col(index: int) -> str:
    result = ""
    while index:
        index, rem = divmod(index - 1, 26); result = chr(65 + rem) + result
    return result


def _xlsx(title: str, blocks: list[Block]) -> bytes:
    sheets: list[tuple[str, list[list[str]]]] = []
    prose = [["제목", title], ["유형", "내용"]]
    for block in blocks:
        if block.kind == "table":
            sheets.append((f"표 {len(sheets) + 1}", block.rows))
        elif block.kind != "rule":
            prose.append([{"heading": "제목", "code": "코드", "list": "목록"}.get(block.kind, "본문"), block.text])
    # Tables are the primary workbook output; do not duplicate them in the text sheet.
    if len(prose) > 2 or not sheets:
        sheets.append(("본문", prose))
    if len(sheets) > 100:
        raise DocumentError("엑셀 표가 100개를 넘습니다. 내용을 나누어 저장하세요.")
    parts = {"_rels/.rels": _rels([("rId1", "officeDocument", "xl/workbook.xml")])}
    types = {"xl/workbook.xml": FORMATS["xlsx"] + ".main+xml",
             "xl/styles.xml": "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"}
    workbook = XML + f'<workbook xmlns="{S}" xmlns:r="{REL[:-1]}"><sheets>'
    relationships = []
    for index, (name, rows) in enumerate(sheets, 1):
        columns = max(len(row) for row in rows)
        if columns > 100 or len(rows) > 20_000:
            raise DocumentError("한 표는 최대 100열·20000행까지 저장할 수 있습니다.")
        workbook += f'<sheet name={quoteattr(name)} sheetId="{index}" r:id="rId{index}"/>'
        path = f"xl/worksheets/sheet{index}.xml"
        relationships.append((f"rId{index}", "worksheet", f"worksheets/sheet{index}.xml"))
        sheet = XML + f'<worksheet xmlns="{S}"><dimension ref="A1:{_col(columns)}{len(rows)}"/><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>'
        sheet += ''.join(f'<col min="{c}" max="{c}" width="{48 if columns <= 2 and c == 2 else 26}" customWidth="1"/>' for c in range(1, columns + 1))
        sheet += '</cols><sheetData>'
        for rownum, row in enumerate(rows, 1):
            wrapped = [len(_wrap(cell, (48 if columns <= 2 and colnum == 2 else 26) - 2)) for colnum, cell in enumerate(row, 1)]
            height = min(409.5, max(22, max(wrapped, default=1) * 15 + 6))
            sheet += f'<row r="{rownum}" ht="{height}" customHeight="1">'
            for colnum, cell in enumerate(row, 1):
                # Inline strings prevent formulas, URLs and leading zero identifiers being executed/coerced.
                if len(cell.encode("utf-16-le")) // 2 > 32767:
                    raise DocumentError("엑셀 한 셀은 32767자까지입니다. 긴 내용을 나누어 주세요.")
                sheet += f'<c r="{_col(colnum)}{rownum}" s="{1 if rownum == 1 else 0}" t="inlineStr"><is><t xml:space="preserve">{_text(cell)}</t></is></c>'
            sheet += '</row>'
        sheet += '</sheetData>'
        if name != "본문" and len(rows) > 1:
            sheet += f'<autoFilter ref="A1:{_col(columns)}{len(rows)}"/>'
        sheet += '<pageMargins left="0.3" right="0.3" top="0.5" bottom="0.5" header="0.2" footer="0.2"/></worksheet>'
        parts[path] = sheet
        types[path] = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
    parts["xl/workbook.xml"] = workbook + '</sheets></workbook>'
    relationships.append(("rStyles", "styles", "styles.xml"))
    parts["xl/_rels/workbook.xml.rels"] = _rels(relationships)
    parts["xl/styles.xml"] = XML + f'<styleSheet xmlns="{S}"><fonts count="2"><font><sz val="11"/><name val="맑은 고딕"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="맑은 고딕"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF183E64"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
    return _package(parts, types)


def _wrap(text: str, width: int) -> list[str]:
    lines, current, used = [], "", 0
    for char in text:
        cost = 2 if unicodedata.east_asian_width(char) in "WF" else 1
        if char == "\n" or used + cost > width:
            lines.append(current); current, used = "", 0
        if char != "\n":
            current += char; used += cost
    lines.append(current)
    return lines


def _slide_text(ident: int, text: str, x: int, y: int, width: int, height: int, size: int, color: str) -> str:
    paragraphs = ''.join('<a:p><a:pPr><a:lnSpc><a:spcPct val="110000"/></a:lnSpc></a:pPr><a:r>'
                         f'<a:rPr lang="ko-KR" sz="{size * 100}" dirty="0"><a:solidFill><a:srgbClr val="{color}"/></a:solidFill><a:latin typeface="Calibri"/><a:ea typeface="맑은 고딕"/></a:rPr><a:t>{_text(line)}</a:t></a:r><a:endParaRPr lang="ko-KR" sz="{size * 100}"/></a:p>' for line in text.split('\n'))
    return f'<p:sp><p:nvSpPr><p:cNvPr id="{ident}" name="Text {ident}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{width}" cy="{height}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr wrap="square" lIns="0" tIns="0" rIns="0" bIns="0"><a:noAutofit/></a:bodyPr><a:lstStyle/>{paragraphs}</p:txBody></p:sp>'


def _pptx(title: str, blocks: list[Block]) -> bytes:
    sections: list[tuple[str, list[str]]] = []
    heading, lines = title, []
    for block in blocks:
        if block.kind == "heading":
            if lines:
                sections.append((heading, lines)); lines = []
            elif heading != title and heading != block.text:
                sections.append((heading, []))
            heading = block.text
        elif block.kind == "table":
            for row in block.rows:
                lines.extend(_wrap(" | ".join(row), 78))
        elif block.kind != "rule":
            lines.extend(_wrap(block.text, 78))
    if lines or not sections:
        sections.append((heading, lines))
    slides = []
    for heading, lines in sections:
        title_lines = _wrap(heading, 46)
        if len(title_lines) > 2:
            lines = _wrap(heading, 78) + lines
            title_lines = _wrap(title, 46)[:2]
        for offset in range(0, max(1, len(lines)), 12):
            slides.append(("\n".join(title_lines), lines[offset:offset + 12]))
    if len(slides) > 200:
        raise DocumentError("슬라이드가 200장을 넘습니다. 내용을 나누어 저장하세요.")
    tree = '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
    namespaces = f'xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{REL[:-1]}"'
    mapping = '<p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/>'
    parts = {"_rels/.rels": _rels([("rId1", "officeDocument", "ppt/presentation.xml")]),
             "ppt/slideMasters/slideMaster1.xml": XML + f'<p:sldMaster {namespaces}><p:cSld><p:spTree>{tree}</p:spTree></p:cSld>{mapping}<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rLayout"/></p:sldLayoutIdLst><p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles></p:sldMaster>',
             "ppt/slideMasters/_rels/slideMaster1.xml.rels": _rels([("rLayout", "slideLayout", "../slideLayouts/slideLayout1.xml"), ("rTheme", "theme", "../theme/theme1.xml")]),
             "ppt/slideLayouts/slideLayout1.xml": XML + f'<p:sldLayout {namespaces} type="blank" preserve="1"><p:cSld name="Blank"><p:spTree>{tree}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>',
             "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _rels([("rMaster", "slideMaster", "../slideMasters/slideMaster1.xml")])}
    types = {"ppt/presentation.xml": FORMATS["pptx"] + ".main+xml",
             "ppt/slideMasters/slideMaster1.xml": "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml",
             "ppt/slideLayouts/slideLayout1.xml": "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml",
             "ppt/theme/theme1.xml": "application/vnd.openxmlformats-officedocument.theme+xml"}
    colors = {"dk1": "172536", "lt1": "FFFFFF", "dk2": "183E64", "lt2": "E8EEF6", "accent1": "245D89", "accent2": "008B8B", "accent3": "E0A62F", "accent4": "82659D", "accent5": "568A50", "accent6": "BB6060", "hlink": "245D89", "folHlink": "82659D"}
    theme = XML + f'<a:theme xmlns:a="{A}" name="ALEX"><a:themeElements><a:clrScheme name="ALEX">'
    theme += ''.join(f'<a:{key}><a:srgbClr val="{value}"/></a:{key}>' for key, value in colors.items()) + '</a:clrScheme><a:fontScheme name="ALEX">'
    theme += ''.join(f'<a:{key}><a:latin typeface="Calibri"/><a:ea typeface="맑은 고딕"/><a:cs typeface="Arial"/></a:{key}>' for key in ("majorFont", "minorFont")) + '</a:fontScheme>'
    fill = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    line = '<a:ln w="12700">' + fill + '<a:prstDash val="solid"/><a:miter lim="800000"/></a:ln>'
    theme += '<a:fmtScheme name="ALEX"><a:fillStyleLst>' + fill * 3 + '</a:fillStyleLst><a:lnStyleLst>' + line * 3 + '</a:lnStyleLst><a:effectStyleLst>' + '<a:effectStyle><a:effectLst/></a:effectStyle>' * 3 + '</a:effectStyleLst><a:bgFillStyleLst>' + fill * 3 + '</a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>'
    parts["ppt/theme/theme1.xml"] = theme
    ids, relationships = [], [("rMaster", "slideMaster", "slideMasters/slideMaster1.xml")]
    for index, (slide_title, body) in enumerate(slides, 1):
        ids.append(f'<p:sldId id="{255 + index}" r:id="rId{index}"/>')
        relationships.append((f"rId{index}", "slide", f"slides/slide{index}.xml"))
        path = f"ppt/slides/slide{index}.xml"
        shapes = _slide_text(2, slide_title, 571500, 330200, 11049000, 1066800, 32, "183E64")
        shapes += _slide_text(3, '\n'.join(body), 571500, 1676400, 11049000, 4648200, 20, "172536")
        shapes += _slide_text(4, str(index), 11239500, 6381750, 381000, 254000, 10, "607080")
        parts[path] = XML + f'<p:sld {namespaces}><p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill><a:effectLst/></p:bgPr></p:bg><p:spTree>{tree}{shapes}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
        parts[f"ppt/slides/_rels/slide{index}.xml.rels"] = _rels([("rLayout", "slideLayout", "../slideLayouts/slideLayout1.xml")])
        types[path] = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
    parts["ppt/presentation.xml"] = XML + f'<p:presentation {namespaces}><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rMaster"/></p:sldMasterIdLst><p:sldIdLst>' + ''.join(ids) + '</p:sldIdLst><p:sldSz cx="12192000" cy="6858000" type="screen16x9"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>'
    parts["ppt/_rels/presentation.xml.rels"] = _rels(relationships)
    return _package(parts, types)


def _html(title: str, blocks: list[Block]) -> str:
    content = [f'<h1>{html.escape(title)}</h1>']
    for block in blocks:
        if block.kind == "table":
            rows = []
            for index, row in enumerate(block.rows):
                tag = "th" if index == 0 else "td"
                rows.append('<tr>' + ''.join(f'<{tag}>{html.escape(c)}</{tag}>' for c in row) + '</tr>')
            content.append('<table><thead>' + rows[0] + '</thead><tbody>' + ''.join(rows[1:]) + '</tbody></table>')
        elif block.kind == "rule":
            content.append('<hr>')
        else:
            tag = "h" + str(min(6, block.level + 1)) if block.kind == "heading" else ("pre" if block.kind == "code" else "p")
            content.append(f'<{tag}>{html.escape(block.text)}</{tag}>')
    return '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;"><title>' + html.escape(title) + '</title><style>@page{size:A4;margin:18mm}body{font:11pt "Malgun Gothic","맑은 고딕",Arial,sans-serif;line-height:1.6;color:#172536}h1{font-size:22pt}h2{font-size:17pt}h3{font-size:14pt}h1,h2,h3,h4{color:#183e64;break-after:avoid}p,td,th{white-space:pre-wrap;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:9pt Consolas,"Malgun Gothic",monospace;padding:8pt;background:#f0f3f7}table{border-collapse:collapse;width:100%;table-layout:fixed;margin:12pt 0;font-size:9pt}th,td{border:1px solid #c7d2e0;padding:6pt;vertical-align:top}th{background:#e8eef6}tr{break-inside:avoid}thead{display:table-header-group}</style></head><body>' + ''.join(content) + '</body></html>'


def find_pdf_browser() -> Path | None:
    candidates = []
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.environ.get(env)
        if root:
            candidates.extend(Path(root) / relative for relative in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"))
    for executable in ("msedge", "microsoft-edge", "chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(executable)
        if found:
            candidates.append(Path(found))
    return next((path for path in candidates if path.is_file()), None)


def _pdf(title: str, blocks: list[Block]) -> bytes:
    browser = find_pdf_browser()
    if browser is None:
        raise DocumentError("PDF 생성에는 Edge 또는 Chrome/Chromium이 필요합니다. DOCX 출력은 바로 사용할 수 있습니다.")
    with tempfile.TemporaryDirectory(prefix="alex-pdf-") as td:
        folder = Path(td).resolve()
        source, output = folder / "document.html", folder / "document.pdf"
        source.write_text(_html(title, blocks), encoding="utf-8")
        command = [str(browser), "--headless", "--disable-gpu", "--disable-software-rasterizer",
                   "--disable-extensions", "--disable-background-networking", "--no-first-run",
                   "--no-default-browser-check", "--no-pdf-header-footer", "--disable-crash-reporter",
                   "--disable-features=MediaRouter", "--user-data-dir=" + str(folder / "profile"),
                   "--print-to-pdf=" + str(output), source.as_uri()]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired as exc:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10)
                else:
                    process.kill()
                process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
                process.wait(timeout=10)
            raise DocumentError("PDF 인쇄가 60초 안에 끝나지 않았습니다. DOCX로 저장하거나 나중에 다시 시도하세요.") from exc
        if process.returncode != 0 or not output.is_file():
            raise DocumentError("브라우저 PDF 인쇄에 실패했습니다. DOCX로 저장하거나 나중에 다시 시도하세요.")
        if output.stat().st_size > MAX_DOWNLOAD:
            raise DocumentError("생성한 PDF가 32MB를 넘습니다. 내용을 나누어 주세요.")
        result = output.read_bytes()
        if not result.startswith(b"%PDF-"):
            raise DocumentError("올바른 PDF가 생성되지 않았습니다.")
        return result


def export_document(root: Path, format: str, text: str, title: str = "ALEX 문서") -> Path:
    name = format_name(format)
    if not isinstance(text, str) or not text.strip():
        raise DocumentError("저장할 내용이 없습니다. 답변을 받은 뒤 내보내거나 내용을 직접 입력하세요.")
    if len(text) > MAX_TEXT or len(title) > 200:
        raise DocumentError("문서는 500000자, 제목은 200자 이하로 입력하세요.")
    _text(text); _text(title)
    blocks = parse_markdown(text)
    if not blocks:
        raise DocumentError("저장할 본문이 없습니다.")
    if name == "pdf":
        data = _pdf(title, blocks)
    else:
        data = {"docx": _docx, "xlsx": _xlsx, "pptx": _pptx}[name](title, blocks)
    if len(data) > MAX_DOWNLOAD:
        raise DocumentError("생성한 문서가 32MB를 넘습니다. 내용을 나누어 주세요.")
    folder = Path(root).resolve() / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink() or folder.resolve() != folder:
        raise DocumentError("내보내기 폴더는 데이터 폴더 안의 실제 디렉터리여야 합니다.")
    destination = folder / ("alex-document-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(8) + "." + name)
    created = False
    try:
        # A unique exclusive file never replaces a previously exported document.
        with destination.open("xb") as stream:
            created = True
            stream.write(data)
    except OSError:
        # Only our newly created partial file can be removed; no recursive deletion.
        if created and destination.is_file():
            destination.unlink()
        raise
    return destination


def download_path(root: Path, name: str) -> Path | None:
    if not NAME_RE.fullmatch(name):
        return None
    base = Path(root).resolve() / "exports"
    path = base / name
    if base.is_symlink() or base.resolve() != base or path.is_symlink() or not path.is_file():
        return None
    if path.resolve().parent != base or path.stat().st_size > MAX_DOWNLOAD:
        return None
    return path


def command_export(storage, session_id: str, command: str) -> str:
    """Both CLI and web share the same command contract; never calls a provider."""
    if command.startswith("/document "):
        header, delimiter, text = command.removeprefix("/document ").partition("|")
        pieces = header.strip().split(maxsplit=1)
        if not delimiter or not pieces:
            raise DocumentError("사용법: /document 형식 제목 | 저장할 내용")
        name = format_name(pieces[0])
        title = pieces[1] if len(pieces) == 2 else "ALEX 문서"
    else:
        pieces = command.split(maxsplit=3)
        if len(pieces) < 2 or len(pieces) > 4:
            raise DocumentError("사용법: /export 형식 [last|all] [제목]")
        name = format_name(pieces[1])
        mode = pieces[2] if len(pieces) >= 3 else "last"
        if mode not in {"last", "all"}:
            raise DocumentError("범위는 last(최신 답변) 또는 all(전체 대화)입니다. 예: /export pdf last 시험지")
        title = pieces[3] if len(pieces) == 4 else (storage.session_title(session_id) or "ALEX 문서")
        if not storage.session_exists(session_id):
            raise DocumentError("내보낼 대화를 찾을 수 없습니다.")
        if mode == "last":
            cursor, text = None, ""
            while not text:
                page = storage.message_page(session_id, before=cursor, limit=200)
                for message in reversed(page["messages"]):
                    if message["role"] == "assistant" and message["content"].strip():
                        text = message["content"]; break
                if text or not page["has_more"]:
                    break
                cursor = page["messages"][0]["id"]
        else:
            cursor, pages, total = None, [], 0
            while True:
                page = storage.message_page(session_id, before=cursor, limit=200)
                chunk = '\n\n'.join(f"## {m['role']} · {m['created_at']}\n\n{m['content']}" for m in page["messages"])
                total += len(chunk) + 2
                if total > MAX_TEXT:
                    raise DocumentError("전체 대화가 500000자를 넘습니다. 최신 답변만 저장하세요.")
                pages.append(chunk)
                if not page["has_more"]:
                    break
                cursor = page["messages"][0]["id"]
            text = '\n\n'.join(reversed(pages))
    path = export_document(storage.db_path.parent, name, text, title)
    return f"{name.upper()} 문서 저장 완료: {path}\n[문서 다운로드](alex-document:{path.name})"
