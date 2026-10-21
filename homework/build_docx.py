"""Build an editable Word copy from the approved HTML, using stdlib only."""
from html.parser import HTMLParser
from pathlib import Path
import re
from zipfile import ZipFile, ZIP_DEFLATED
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'
ET.register_namespace('w', W)
ET.register_namespace('r', R)


class Node:
    def __init__(self, tag, attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []


class Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs)
        self.stack[-1].children.append(n)
        if tag not in {'br', 'col', 'meta', 'link', 'img', 'hr'}:
            self.stack.append(n)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def descendants(node, tag):
    for c in node.children:
        if isinstance(c, Node):
            if c.tag == tag:
                yield c
            yield from descendants(c, tag)


def el(parent, tag, **attrs):
    return ET.SubElement(parent, f'{{{W}}}{tag}', {f'{{{W}}}{k}': str(v) for k, v in attrs.items()})


rels = ET.Element('Relationships', xmlns=PKG)
ET.SubElement(rels, 'Relationship', Id='styles', Type=R + '/styles', Target='styles.xml')
ET.SubElement(rels, 'Relationship', Id='footer', Type=R + '/footer', Target='footer1.xml')


def run(parent, text, bold=False, link=False):
    r = el(parent, 'r')
    rp = el(r, 'rPr')
    if bold:
        el(rp, 'b')
    if link:
        el(rp, 'color', val='205741')
        el(rp, 'u', val='single')
    t = el(r, 't')
    t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    t.text = re.sub(r'\s+', ' ', text)


def inline(parent, node, bold=False, link=False):
    for c in node.children:
        if isinstance(c, str):
            run(parent, c, bold, link)
        elif c.tag == 'br':
            el(el(parent, 'r'), 'br')
        elif c.tag == 'a':
            rid = f'link{len(rels)}'
            ET.SubElement(rels, 'Relationship', Id=rid, Type=R + '/hyperlink', Target=c.attrs['href'], TargetMode='External')
            h = el(parent, 'hyperlink', history='1')
            h.set(f'{{{R}}}id', rid)
            inline(h, c, bold, True)
        else:
            inline(parent, c, bold or c.tag == 'strong', link)


def paragraph(parent, node, pagebreak=False, cell=False, header=False):
    p = el(parent, 'p')
    pp = el(p, 'pPr')
    style = {'h1': 'Title', 'h2': 'Heading1', 'h3': 'Heading2'}.get(node.tag, 'Normal')
    if cell:
        style = 'TableHeader' if header else 'TableText'
    elif node.attrs.get('class') in {'meta', 'note', 'sources'}:
        style = 'SmallText'
    el(pp, 'pStyle', val=style)
    if pagebreak:
        el(pp, 'pageBreakBefore')
    if node.attrs.get('class') == 'num':
        el(pp, 'jc', val='right')
    inline(p, node, header)
    return p


def table(parent, node):
    t = el(parent, 'tbl')
    tp = el(t, 'tblPr')
    el(tp, 'tblW', w='10318', type='dxa')
    el(tp, 'tblLayout', type='fixed')
    borders = el(tp, 'tblBorders')
    for side in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        el(borders, side, val='single', sz='4', color='D9D9D9')
    margins = el(tp, 'tblCellMar')
    for side, size in [('top', 65), ('bottom', 65), ('left', 85), ('right', 85)]:
        el(margins, side, w=size, type='dxa')
    cols = list(descendants(node, 'col'))
    widths = [round(float(re.search(r'[\d.]+', c.attrs['style']).group()) * 10318 / 100) for c in cols]
    widths[-1] += 10318 - sum(widths)
    grid = el(t, 'tblGrid')
    for width in widths:
        el(grid, 'gridCol', w=width)
    for row in descendants(node, 'tr'):
        tr = el(t, 'tr')
        rp = el(tr, 'trPr')
        el(rp, 'cantSplit')
        cells = [c for c in row.children if isinstance(c, Node) and c.tag in {'th', 'td'}]
        header = cells[0].tag == 'th'
        if header:
            el(rp, 'tblHeader')
        for i, cell in enumerate(cells):
            tc = el(tr, 'tc')
            cp = el(tc, 'tcPr')
            el(cp, 'tcW', w=widths[i], type='dxa')
            el(cp, 'vAlign', val='center')
            if header:
                el(cp, 'shd', fill='EDF3EF', val='clear')
            paragraph(tc, cell, cell=True, header=header)


def xml(node):
    return ET.tostring(node, encoding='utf-8', xml_declaration=True)


parser = Parser()
parser.feed((ROOT / 'product-decision.html').read_text())
pages = list(descendants(parser.root, 'section'))
assert len(pages) == 5
doc = ET.Element(f'{{{W}}}document')
body = el(doc, 'body')
for i, page in enumerate(pages):
    first = True
    for n in page.children:
        if not isinstance(n, Node) or n.tag == 'footer':
            continue
        if n.tag == 'table':
            table(body, n)
        else:
            paragraph(body, n, pagebreak=i > 0 and first)
        first = False
sect = el(body, 'sectPr')
ref = el(sect, 'footerReference', type='default')
ref.set(f'{{{R}}}id', 'footer')
el(sect, 'pgSz', w=11906, h=16838)
el(sect, 'pgMar', top=794, right=794, bottom=794, left=794, header=350, footer=350, gutter=0)

styles = ET.Element(f'{{{W}}}styles')
defaults = el(styles, 'docDefaults')
rd = el(el(defaults, 'rPrDefault'), 'rPr')
el(rd, 'rFonts', ascii='Arial', hAnsi='Arial', eastAsia='Arial', cs='Arial')
el(rd, 'lang', val='uk-UA')
el(rd, 'sz', val=19)
el(rd, 'color', val='17201C')
for sid, name, size, before, after, bold in [
    ('Normal', 'Normal', 19, 0, 100, False),
    ('Title', 'Title', 46, 0, 180, True),
    ('Heading1', 'heading 1', 30, 90, 100, True),
    ('Heading2', 'heading 2', 21, 130, 70, True),
    ('SmallText', 'Small Text', 17, 0, 90, False),
    ('TableText', 'Table Text', 17, 0, 0, False),
    ('TableHeader', 'Table Header', 16, 0, 0, True),
]:
    s = el(styles, 'style', type='paragraph', styleId=sid)
    if sid == 'Normal':
        s.set(f'{{{W}}}default', '1')
    el(s, 'name', val=name)
    el(s, 'qFormat')
    pp = el(s, 'pPr')
    el(pp, 'spacing', before=before, after=after, line=240 if sid.startswith('Table') else 260, lineRule='auto')
    if sid in {'Title', 'Heading1', 'Heading2'}:
        el(pp, 'keepNext')
        el(pp, 'keepLines')
        if sid != 'Title':
            el(pp, 'outlineLvl', val=0 if sid == 'Heading1' else 1)
    rp = el(s, 'rPr')
    el(rp, 'sz', val=size)
    if bold:
        el(rp, 'b')
    if sid in {'Title', 'Heading1', 'Heading2', 'TableHeader'}:
        el(rp, 'color', val='000000')

footer = ET.Element(f'{{{W}}}ftr')
p = el(footer, 'p')
pp = el(p, 'pPr')
el(pp, 'jc', val='right')
rp = el(pp, 'rPr')
el(rp, 'sz', val=16)
run(p, 'Оля і Марко · ')
for value, label in [('PAGE', None), (None, ' / '), ('NUMPAGES', None)]:
    if label:
        run(p, label)
    else:
        f = el(p, 'fldSimple', instr=f' {value} ')
        run(f, '1' if value == 'PAGE' else '5')

types = ET.Element('Types', xmlns='http://schemas.openxmlformats.org/package/2006/content-types')
ET.SubElement(types, 'Default', Extension='rels', ContentType='application/vnd.openxmlformats-package.relationships+xml')
ET.SubElement(types, 'Default', Extension='xml', ContentType='application/xml')
for part, suffix in [('document', 'document.main'), ('styles', 'styles'), ('footer1', 'footer')]:
    ET.SubElement(types, 'Override', PartName=f'/word/{part}.xml', ContentType=f'application/vnd.openxmlformats-officedocument.wordprocessingml.{suffix}+xml')
rootrels = ET.Element('Relationships', xmlns=PKG)
ET.SubElement(rootrels, 'Relationship', Id='document', Type=R + '/officeDocument', Target='word/document.xml')
with ZipFile(ROOT / 'product-decision.docx', 'w', ZIP_DEFLATED) as out:
    for name, node in [('[Content_Types].xml', types), ('_rels/.rels', rootrels), ('word/document.xml', doc), ('word/styles.xml', styles), ('word/_rels/document.xml.rels', rels), ('word/footer1.xml', footer)]:
        out.writestr(name, xml(node))
print('Created homework/product-decision.docx: editable text, 6 tables, 5 planned pages.')
