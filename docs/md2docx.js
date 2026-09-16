// Regenerate docs/操作手冊.docx from docs/操作手冊.md after editing the markdown:
//     npm install docx        (once; if it is installed elsewhere: set NODE_PATH=<that>/node_modules)
//     node docs/md2docx.js docs/操作手冊.md docs/操作手冊.docx
// Handles the markdown subset the manual uses: headings, paragraphs, bullet and
// numbered lists (with nested code blocks), tables, fenced code, blockquotes, links.
// Convert docs/操作手冊.md (the markdown subset it uses) into a Word document.
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell,
  WidthType, ShadingType, BorderStyle, AlignmentType, LevelFormat, ExternalHyperlink,
  Header, Footer, PageNumber,
} = require("docx");

const [,, inPath, outPath] = process.argv;
const md = fs.readFileSync(inPath, "utf8").replace(/\r\n/g, "\n").split("\n");

const FONT = { ascii: "Calibri", hAnsi: "Calibri", eastAsia: "Microsoft JhengHei", cs: "Calibri" };
const MONO = { ascii: "Consolas", hAnsi: "Consolas", eastAsia: "Microsoft JhengHei", cs: "Consolas" };
const PAGE_W = 11906, MARGIN = 1440, USABLE = PAGE_W - 2 * MARGIN; // A4, 1" margins

// ---- inline markdown -> runs ------------------------------------------------
function inline(text, base = {}) {
  const runs = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[([^\]]+)\]\(([^)]+)\))|(https?:\/\/[^\s)]+)/g;
  let last = 0, m;
  const push = (t, opts) => { if (t) runs.push(new TextRun({ text: t, font: FONT, size: 21, ...base, ...opts })); };
  const link = (label, url) => runs.push(new ExternalHyperlink({ link: url, children: [new TextRun({ text: label, font: FONT, size: 21, style: "Hyperlink", ...base })] }));
  while ((m = re.exec(text)) !== null) {
    push(text.slice(last, m.index));
    if (m[1]) push(m[1].slice(1, -1), { font: MONO, size: 19, shading: { type: ShadingType.CLEAR, fill: "F2F2F2" } });
    else if (m[2]) push(m[2].slice(2, -2), { bold: true });
    else if (m[3]) { if (/^https?:\/\//.test(m[5])) link(m[4], m[5]); else push(m[4], { bold: true }); }
    else if (m[6]) link(m[6], m[6]);
    last = re.lastIndex;
  }
  push(text.slice(last));
  return runs;
}
const para = (text, opts = {}) => new Paragraph({ children: inline(text, opts.run || {}), spacing: { after: 120, line: 300 }, ...opts.p });

function codeBlock(lines, indent = 200) {
  const common = Math.min(...lines.filter(l => l.trim()).map(l => l.match(/^\s*/)[0].length), 0) || 0;
  const strip = lines.filter(l => l.trim()).reduce((n, l) => Math.min(n, l.match(/^\s*/)[0].length), 99);
  return lines.map((l, k) => new Paragraph({
    children: [new TextRun({ text: (l.slice(strip === 99 ? 0 : strip)) || " ", font: MONO, size: 17 })],
    shading: { type: ShadingType.CLEAR, fill: "F5F5F5" },
    spacing: { before: k === 0 ? 60 : 0, after: k === lines.length - 1 ? 160 : 0, line: 250 },
    indent: { left: indent },
    keepNext: k !== lines.length - 1,
  }));
}

// ---- block parsing ------------------------------------------------------------
const intro = [], body = [];
let out = intro;           // paragraphs before the first "## " go on the cover
let i = 0, skipToc = false, listInstance = 0;
const isFence = l => l.trim().startsWith("```");

function readFence() {   // md[i] is the opening fence; returns the lines inside, advances i past the closing fence
  i++; const lines = [];
  while (i < md.length && !isFence(md[i])) { lines.push(md[i]); i++; }
  i++; return lines;
}

while (i < md.length) {
  const line = md[i];
  if (isFence(line)) { out.push(...codeBlock(readFence())); continue; }
  if (line.startsWith("# ")) { i++; continue; }
  if (line.startsWith("## 目錄")) { skipToc = true; i++; continue; }
  if (skipToc) { if (line.startsWith("## ")) skipToc = false; else { i++; continue; } }
  if (line.startsWith("## ")) { out = body; body.push(new Paragraph({ text: line.slice(3), heading: HeadingLevel.HEADING_1 })); i++; continue; }
  if (line.startsWith("### ")) { body.push(new Paragraph({ text: line.slice(4), heading: HeadingLevel.HEADING_2 })); i++; continue; }
  if (line.trim() === "---" || line.trim() === "") { i++; continue; }
  if (line.startsWith("> ")) {
    out.push(new Paragraph({ children: inline(line.slice(2).replace(/^⚠️\s*/, "")), spacing: { after: 120, line: 300 }, indent: { left: 400 },
      border: { left: { style: BorderStyle.SINGLE, size: 18, color: "C00000", space: 8 } },
      shading: { type: ShadingType.CLEAR, fill: "FFF4E5" } }));
    i++; continue;
  }
  if (line.startsWith("|")) {
    const rows = [];
    while (i < md.length && md[i].startsWith("|")) { rows.push(md[i]); i++; }
    const cells = rows.filter(r => !/^\|\s*-+/.test(r)).map(r => r.slice(1, -1).split("|").map(c => c.trim()));
    const ncol = cells[0].length;
    const widths = ncol === 2 ? [Math.round(USABLE * 0.34), USABLE - Math.round(USABLE * 0.34)]
                 : ncol === 3 ? [Math.round(USABLE * 0.28), Math.round(USABLE * 0.5), USABLE - Math.round(USABLE * 0.28) - Math.round(USABLE * 0.5)]
                 : Array(ncol).fill(Math.floor(USABLE / ncol));
    out.push(new Table({
      width: { size: USABLE, type: WidthType.DXA }, columnWidths: widths,
      rows: cells.map((r, ri) => new TableRow({
        tableHeader: ri === 0, cantSplit: true,
        children: r.map((c, ci) => new TableCell({
          width: { size: widths[ci], type: WidthType.DXA },
          shading: ri === 0 ? { type: ShadingType.CLEAR, fill: "D9E2F3" } : undefined,
          margins: { top: 60, bottom: 60, left: 100, right: 100 },
          children: [new Paragraph({ children: inline(c, ri === 0 ? { bold: true } : {}), spacing: { after: 0, line: 276 } })],
        })),
      })),
    }));
    out.push(new Paragraph({ text: "", spacing: { after: 120 } }));
    continue;
  }
  let m;
  if ((m = line.match(/^(\s*)- (?:\[ \] )?(.*)$/))) {
    const checkbox = /^\s*- \[ \] /.test(line);
    out.push(new Paragraph({ children: inline((checkbox ? "☐ " : "") + m[2]), numbering: { reference: "bullets", level: Math.min(1, Math.floor(m[1].length / 2)) }, spacing: { after: 60, line: 300 } }));
    i++; continue;
  }
  if ((m = line.match(/^(\d+)\. (.*)$/))) {
    if (m[1] === "1") listInstance++;                   // a new numbered list starts at 1
    let txt = m[2]; i++;
    // continuation lines (indented) and nested fenced code blocks belong to this item,
    // in document order: text / code / text ...
    const segments = [];
    while (i < md.length && /^\s{2,}\S/.test(md[i])) {
      if (isFence(md[i])) { segments.push({ code: codeBlock(readFence(), 720) }); continue; }
      if (segments.length && segments[segments.length - 1].text !== undefined) segments[segments.length - 1].text += " " + md[i].trim();
      else if (segments.length) segments.push({ text: md[i].trim() });
      else txt += " " + md[i].trim();
      i++;
    }
    out.push(new Paragraph({ children: inline(txt), numbering: { reference: "numbers", level: 0, instance: listInstance }, spacing: { after: 80, line: 300 } }));
    for (const seg of segments) {
      if (seg.code) out.push(...seg.code);
      else out.push(new Paragraph({ children: inline(seg.text), indent: { left: 520 }, spacing: { after: 80, line: 300 } }));
    }
    continue;
  }
  let txt = line; i++;   // plain paragraph: join wrapped lines
  while (i < md.length && md[i].trim() !== "" && !/^(#|>|\||- |\d+\. |```|---)/.test(md[i]) && !/^\s+- /.test(md[i])) { txt += " " + md[i].trim(); i++; }
  out.push(para(txt));
}

const toc = [];
for (const l of md) { const m = l.match(/^## (\d+\. .*)$/); if (m) toc.push(m[1]); }

const doc = new Document({
  styles: {
    default: { document: { run: { font: FONT, size: 21 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 30, bold: true, color: "1F3864", font: FONT }, paragraph: { spacing: { before: 360, after: 160 }, outlineLevel: 0, keepNext: true } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 25, bold: true, color: "2F5496", font: FONT }, paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 1, keepNext: true } },
    ],
  },
  numbering: {
    config: [
      { reference: "bullets", levels: [
        { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 520, hanging: 260 } } } },
        { level: 1, format: LevelFormat.BULLET, text: "–", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 1000, hanging: 260 } } } },
      ] },
      { reference: "numbers", levels: [
        { level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 520, hanging: 320 } } } },
      ] },
    ],
  },
  sections: [{
    properties: { page: { size: { width: PAGE_W, height: 16838 }, margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN } } },
    headers: { default: new Header({ children: [new Paragraph({ children: [new TextRun({ text: "HIWIN Support Agent 操作手冊", font: FONT, size: 16, color: "808080" })], alignment: AlignmentType.RIGHT })] }) },
    footers: { default: new Footer({ children: [new Paragraph({ children: [new TextRun({ children: ["第 ", PageNumber.CURRENT, " 頁"], font: FONT, size: 16, color: "808080" })], alignment: AlignmentType.CENTER })] }) },
    children: [
      new Paragraph({ children: [new TextRun({ text: "HIWIN Support Agent", font: FONT, size: 52, bold: true, color: "1F3864" })], spacing: { before: 2000, after: 120 }, alignment: AlignmentType.CENTER }),
      new Paragraph({ children: [new TextRun({ text: "操作手冊", font: FONT, size: 44, bold: true, color: "1F3864" })], spacing: { after: 500 }, alignment: AlignmentType.CENTER }),
      new Paragraph({ children: [new TextRun({ text: "安裝、啟動、使用、資料更新、備份與故障排除", font: FONT, size: 24, color: "404040" })], alignment: AlignmentType.CENTER, spacing: { after: 200 } }),
      new Paragraph({ children: [new TextRun({ text: "ACPIE_Lab", font: FONT, size: 26, bold: true, color: "2F5496" })], alignment: AlignmentType.CENTER, spacing: { after: 700 } }),
      ...intro,
      new Paragraph({ text: "目錄", heading: HeadingLevel.HEADING_1, pageBreakBefore: true }),
      ...toc.map(t => new Paragraph({ children: [new TextRun({ text: t, font: FONT, size: 22 })], spacing: { after: 80 }, indent: { left: 400 } })),
      ...body,
    ],
  }],
});

Packer.toBuffer(doc).then(buf => { fs.writeFileSync(outPath, buf); console.log("wrote", outPath, buf.length, "bytes"); });
