#!/usr/bin/env node

/*
 * Build the Cybic customer-engagement presentation.
 *
 * The deck is intentionally vector-native: every diagram, card, label, and
 * status chip remains editable in PowerPoint.  The build also emits an HTML
 * proof sheet (when CYBIC_DECK_PREVIEW is set) using the same coordinates so
 * the layout can be reviewed without a local Office installation.
 */

const fs = require("fs");
const path = require("path");

const moduleRoot = process.env.PPTXGENJS_ROOT || "/tmp/cybic-pptx-node/node_modules";
const pptxgen = require(path.join(moduleRoot, "pptxgenjs"));

const ROOT = path.resolve(__dirname, "..");
const OUTPUT = process.argv[2]
  ? path.resolve(process.argv[2])
  : path.join(ROOT, "docs", "demo", "CYBIC-AGENTIC-AI-CUSTOMER-ENGAGEMENT.pptx");
const PREVIEW = process.env.CYBIC_DECK_PREVIEW
  ? path.resolve(process.env.CYBIC_DECK_PREVIEW)
  : null;

const SW = 13.333;
const SH = 7.5;

const C = {
  bg: "16161D",
  panel: "1E1E28",
  ink: "E8E6E0",
  dim: "9A97A3",
  amber: "E8A33D",
  mint: "7CC7A5",
  blue: "6FB3E8",
  red: "E86F6F",
  line: "34323E",
  status: "100F14",
  amberDark: "3D3320",
  mintDark: "20382F",
  blueDark: "20303D",
  redDark: "3D2020",
  white: "FFFFFF",
};

const F = {
  body: "Arial",
  display: "Arial",
  mono: "Courier New",
};

const pptx = new pptxgen();
pptx.defineLayout({ name: "CYBIC_WIDE", width: SW, height: SH });
pptx.layout = "CYBIC_WIDE";
pptx.author = "Cybic";
pptx.company = "Cybic";
pptx.subject = "Current voice-intelligence capability, selective proposal alignment, and expansion path";
pptx.title = "Cybic Voice Intelligence to Agentic AI Customer Engagement";
pptx.lang = "en-US";
pptx.theme = {
  headFontFace: F.display,
  bodyFontFace: F.body,
  lang: "en-US",
};
pptx.defineSlideMaster({
  title: "CYBIC_MASTER",
  background: { color: C.bg },
  objects: [],
  slideNumber: { x: 12.15, y: 7.09, w: 0.42, h: 0.18, color: C.dim, fontFace: F.mono, fontSize: 8, align: "right", margin: 0 },
});

const previewSlides = [];
const boundsErrors = [];

function esc(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function cssColor(hex, transparency = 0) {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${1 - transparency / 100})`;
}

function marginCss(margin) {
  const m = Array.isArray(margin) ? margin : [margin ?? 0, margin ?? 0, margin ?? 0, margin ?? 0];
  const normalized = m.length === 4 ? m : [m[0] || 0, m[0] || 0, m[0] || 0, m[0] || 0];
  return `${normalized[0]}in ${normalized[1]}in ${normalized[2]}in ${normalized[3]}in`;
}

function assertBounds(kind, opts) {
  if ([opts.x, opts.y, opts.w, opts.h].some((v) => typeof v !== "number")) return;
  const tolerance = 0.005;
  if (opts.x < -tolerance || opts.y < -tolerance || opts.x + opts.w > SW + tolerance || opts.y + opts.h > SH + tolerance) {
    boundsErrors.push(`${kind}: x=${opts.x}, y=${opts.y}, w=${opts.w}, h=${opts.h}`);
  }
}

function htmlRuns(runs) {
  if (typeof runs === "string") return esc(runs).replaceAll("\n", "<br>");
  return runs.map((run) => {
    const o = run.options || {};
    const styles = [];
    if (o.bold) styles.push("font-weight:700");
    if (o.italic) styles.push("font-style:italic");
    if (o.color) styles.push(`color:#${o.color}`);
    if (o.fontSize) styles.push(`font-size:${o.fontSize}pt`);
    return `<span style="${styles.join(";")}">${esc(run.text).replaceAll("\n", "<br>")}</span>`;
  }).join("");
}

class Canvas {
  constructor(slide, index) {
    this.slide = slide;
    this.index = index;
    this.html = [];
  }

  rect(x, y, w, h, options = {}) {
    const shape = options.radius ? pptx.ShapeType.roundRect : pptx.ShapeType.rect;
    const fillColor = options.fill || C.panel;
    const fillTransparency = options.transparency || 0;
    const lineColor = options.line || fillColor;
    const lineWidth = options.lineWidth ?? 0;
    const pptOpts = {
      x, y, w, h,
      fill: { color: fillColor, transparency: fillTransparency },
      line: { color: lineColor, transparency: options.lineTransparency ?? 0, width: lineWidth },
      radius: options.radius,
      objectName: options.name,
    };
    assertBounds("rect", pptOpts);
    this.slide.addShape(shape, pptOpts);
    this.html.push(`<div style="position:absolute;left:${x}in;top:${y}in;width:${w}in;height:${h}in;background:${cssColor(fillColor, fillTransparency)};border:${lineWidth}pt solid ${cssColor(lineColor, options.lineTransparency || 0)};border-radius:${options.radius ? 0.08 : 0}in;box-sizing:border-box"></div>`);
  }

  ellipse(x, y, w, h, options = {}) {
    const fillColor = options.fill || C.panel;
    const lineColor = options.line || fillColor;
    const lineWidth = options.lineWidth ?? 0;
    const pptOpts = {
      x, y, w, h,
      fill: { color: fillColor, transparency: options.transparency || 0 },
      line: { color: lineColor, width: lineWidth, transparency: options.lineTransparency || 0 },
    };
    assertBounds("ellipse", pptOpts);
    this.slide.addShape(pptx.ShapeType.ellipse, pptOpts);
    this.html.push(`<div style="position:absolute;left:${x}in;top:${y}in;width:${w}in;height:${h}in;background:${cssColor(fillColor, options.transparency || 0)};border:${lineWidth}pt solid #${lineColor};border-radius:50%;box-sizing:border-box"></div>`);
  }

  line(x, y, w, h, options = {}) {
    const color = options.color || C.line;
    const width = options.width || 1;
    const pptOpts = {
      x, y, w, h,
      line: { color, width, dash: options.dash || "solid", beginArrowType: options.beginArrowType, endArrowType: options.endArrowType },
    };
    assertBounds("line", { x: Math.min(x, x + w), y: Math.min(y, y + h), w: Math.abs(w), h: Math.abs(h) });
    this.slide.addShape(pptx.ShapeType.line, pptOpts);
    const length = Math.sqrt(w * w + h * h);
    const angle = Math.atan2(h, w) * 180 / Math.PI;
    this.html.push(`<div style="position:absolute;left:${x}in;top:${y}in;width:${length}in;border-top:${width}pt ${options.dash === "dash" ? "dashed" : "solid"} #${color};transform:rotate(${angle}deg);transform-origin:0 0;box-sizing:border-box"></div>`);
  }

  text(text, x, y, w, h, options = {}) {
    const opts = {
      x, y, w, h,
      fontFace: options.fontFace || F.body,
      fontSize: options.fontSize || 18,
      color: options.color || C.ink,
      bold: options.bold || false,
      italic: options.italic || false,
      align: options.align || "left",
      valign: options.valign || "top",
      margin: options.margin ?? 0,
      breakLine: false,
      fit: options.fit || "shrink",
      charSpacing: options.charSpacing,
      lineSpacingMultiple: options.lineSpacingMultiple,
      paraSpaceAfterPt: options.paraSpaceAfterPt,
      isTextBox: true,
      hyperlink: options.hyperlink,
      objectName: options.name,
    };
    assertBounds("text", opts);
    this.slide.addText(text, opts);
    const justify = opts.valign === "mid" || opts.valign === "middle" ? "center" : opts.valign === "bottom" ? "flex-end" : "flex-start";
    this.html.push(`<div style="position:absolute;left:${x}in;top:${y}in;width:${w}in;height:${h}in;box-sizing:border-box;padding:${marginCss(opts.margin)};display:flex;align-items:${justify};text-align:${opts.align};font-family:${opts.fontFace},Arial,sans-serif;font-size:${opts.fontSize}pt;line-height:1.12;color:#${opts.color};font-weight:${opts.bold ? 700 : 400};font-style:${opts.italic ? "italic" : "normal"};letter-spacing:${opts.charSpacing ? `${opts.charSpacing / 100}pt` : "normal"};overflow:hidden;white-space:pre-wrap">${htmlRuns(text)}</div>`);
  }

  shape(kind, x, y, w, h, options = {}) {
    const fillColor = options.fill || C.panel;
    const lineColor = options.line || fillColor;
    const lineWidth = options.lineWidth ?? 0;
    const shapeType = pptx.ShapeType[kind] || kind;
    const pptOpts = {
      x, y, w, h,
      fill: { color: fillColor, transparency: options.transparency || 0 },
      line: { color: lineColor, width: lineWidth, transparency: options.lineTransparency || 0 },
      rotate: options.rotate || 0,
    };
    assertBounds(`shape:${kind}`, pptOpts);
    this.slide.addShape(shapeType, pptOpts);
    const clip = kind === "chevron" ? "clip-path:polygon(0 0,80% 0,100% 50%,80% 100%,0 100%,20% 50%);" : kind === "rightArrow" ? "clip-path:polygon(0 30%,75% 30%,75% 0,100% 50%,75% 100%,75% 70%,0 70%);" : "";
    this.html.push(`<div style="position:absolute;left:${x}in;top:${y}in;width:${w}in;height:${h}in;background:#${fillColor};border:${lineWidth}pt solid #${lineColor};${clip}box-sizing:border-box"></div>`);
  }
}

function addFooter(c, label = "CYBIC  •  CONFIDENTIAL") {
  c.line(0.72, 7.03, 11.88, 0, { color: C.line, width: 0.7 });
  c.text(label, 0.75, 7.08, 3.2, 0.16, { fontFace: F.mono, fontSize: 7.5, color: C.dim, charSpacing: 1.2 });
}

function addHeader(c, eyebrow, title, subtitle = null, color = C.amber) {
  c.text(eyebrow.toUpperCase(), 0.78, 0.42, 5.6, 0.24, { fontFace: F.mono, fontSize: 9, color: C.dim, bold: true, charSpacing: 2.1 });
  c.text("CYBIC", 11.4, 0.38, 1.15, 0.25, { fontFace: F.mono, fontSize: 10, color: C.ink, bold: true, align: "right", charSpacing: 2.4 });
  c.text(title, 0.75, 0.78, 11.75, subtitle ? 0.62 : 0.7, { fontFace: F.display, fontSize: 28, color, bold: true, margin: 0 });
  if (subtitle) c.text(subtitle, 0.78, 1.42, 11.6, 0.45, { fontSize: 13.5, color: C.dim, margin: 0 });
}

function pill(c, text, x, y, w, options = {}) {
  const color = options.color || C.amber;
  const fill = options.fill || C.panel;
  c.rect(x, y, w, options.h || 0.34, { fill, line: color, lineWidth: options.lineWidth ?? 0.8, radius: true });
  c.text(text.toUpperCase(), x + 0.05, y + 0.04, w - 0.1, (options.h || 0.34) - 0.07, { fontFace: F.mono, fontSize: options.fontSize || 8.5, color, bold: true, align: "center", valign: "mid", charSpacing: 1 });
}

function bullet(c, runs, x, y, w, options = {}) {
  const color = options.color || C.amber;
  c.ellipse(x, y + 0.12, 0.075, 0.075, { fill: color, line: color });
  c.text(runs, x + 0.18, y, w - 0.18, options.h || 0.52, {
    fontSize: options.fontSize || 15,
    color: options.textColor || C.ink,
    margin: 0,
    fit: "shrink",
  });
}

function card(c, x, y, w, h, title, body, options = {}) {
  const color = options.color || C.amber;
  c.rect(x, y, w, h, { fill: options.fill || C.panel, line: color, lineWidth: options.lineWidth ?? 1, radius: true });
  if (options.tag) pill(c, options.tag, x + 0.22, y + 0.18, Math.min(w - 0.44, options.tagWidth || 1.35), { color, fill: options.tagFill || C.panel, fontSize: 7.5, h: 0.28 });
  const titleY = options.tag ? y + 0.58 : y + 0.24;
  c.text(title, x + 0.24, titleY, w - 0.48, options.titleH || 0.42, { fontSize: options.titleSize || 17, color, bold: true, margin: 0 });
  c.text(body, x + 0.24, titleY + (options.bodyOffset || 0.5), w - 0.48, h - (titleY - y) - (options.bodyOffset || 0.5) - 0.22, { fontSize: options.bodySize || 12.5, color: options.bodyColor || C.ink, margin: 0, fit: "shrink" });
}

function statusBar(c, text, y = 6.45) {
  c.rect(0.78, y, 11.78, 0.38, { fill: C.status, line: C.line, lineWidth: 0.8, radius: true });
  c.text(text, 0.98, y + 0.08, 11.36, 0.18, { fontFace: F.mono, fontSize: 8.4, color: C.dim, margin: 0, fit: "shrink" });
}

function newSlide(eyebrow, title, subtitle = null, color = C.amber) {
  const slide = pptx.addSlide({ masterName: "CYBIC_MASTER" });
  slide.background = { color: C.bg };
  const c = new Canvas(slide, pptx._slides.length);
  addHeader(c, eyebrow, title, subtitle, color);
  addFooter(c);
  previewSlides.push(c);
  return c;
}

function addNotes(c, text) {
  c.slide.addNotes(text.trim());
}

// -----------------------------------------------------------------------------
// 1 — Cover
// -----------------------------------------------------------------------------
{
  const slide = pptx.addSlide({ masterName: "CYBIC_MASTER" });
  slide.background = { color: C.bg };
  const c = new Canvas(slide, 1);
  previewSlides.push(c);

  c.rect(0, 0, 0.16, SH, { fill: C.amber, line: C.amber });
  c.text("WORKING PROTOTYPE  +  PROPOSED ROADMAP", 0.85, 0.58, 5.8, 0.25, { fontFace: F.mono, fontSize: 9, color: C.dim, bold: true, charSpacing: 1.8 });
  c.text("From Voice Intelligence\nto One Continuous\nCustomer Journey", 0.82, 1.18, 7.35, 2.35, { fontSize: 35, color: C.amber, bold: true, margin: 0, fit: "shrink" });
  c.text("Cybic current capability, selective proposal alignment,\nand a credible expansion path", 0.86, 3.82, 6.55, 0.7, { fontSize: 17, color: C.ink, margin: 0 });
  pill(c, "Current capability", 0.86, 4.86, 1.85, { color: C.amber, fill: C.amberDark });
  pill(c, "Reusable patterns", 2.86, 4.86, 1.9, { color: C.mint, fill: C.mintDark });
  pill(c, "Proposed platform", 4.92, 4.86, 1.95, { color: C.blue, fill: C.blueDark });

  c.rect(8.1, 0.72, 4.35, 5.8, { fill: C.panel, line: C.line, lineWidth: 1, radius: true });
  c.text("ONE CUSTOMER TIMELINE", 8.45, 1.03, 3.65, 0.25, { fontFace: F.mono, fontSize: 9, color: C.blue, bold: true, align: "center", charSpacing: 1.4 });
  const nodes = [
    ["WEB", C.blue], ["VOICE", C.amber], ["SMS", C.blue], ["CRM", C.mint], ["AGENT", C.mint],
  ];
  nodes.forEach(([label, color], i) => {
    const y = 1.54 + i * 0.86;
    if (i < nodes.length - 1) c.line(9.15, y + 0.44, 0, 0.42, { color: C.line, width: 2 });
    c.ellipse(8.86, y, 0.58, 0.58, { fill: color === C.amber ? C.amberDark : color === C.mint ? C.mintDark : C.blueDark, line: color, lineWidth: 1.2 });
    c.text(String(i + 1).padStart(2, "0"), 8.95, y + 0.15, 0.4, 0.17, { fontFace: F.mono, fontSize: 8, color, bold: true, align: "center", valign: "mid" });
    c.text(label, 9.7, y + 0.1, 1.05, 0.3, { fontFace: F.mono, fontSize: 10, color: C.ink, bold: true, charSpacing: 1.1 });
    c.text(i === 0 ? "intent captured" : i === 1 ? "context restored" : i === 2 ? "confirmation sent" : i === 3 ? "record updated" : "handoff informed", 10.7, y + 0.11, 1.4, 0.26, { fontSize: 9.5, color: C.dim, align: "right" });
  });
  c.rect(8.56, 5.88, 3.45, 0.38, { fill: C.status, line: C.blue, lineWidth: 0.8, radius: true });
  c.text("context: preserved  •  customer: resolved", 8.74, 5.98, 3.08, 0.15, { fontFace: F.mono, fontSize: 7.4, color: C.blue, align: "center" });

  c.text("CYBIC", 0.86, 6.58, 1.25, 0.25, { fontFace: F.mono, fontSize: 11, color: C.ink, bold: true, charSpacing: 2.6 });
  c.text("Prepared August 2026", 2.15, 6.62, 2.2, 0.18, { fontFace: F.mono, fontSize: 8, color: C.dim });
  addFooter(c);
  addNotes(c, `
Opening thesis

Cybic has a working, pre-existing voice-intelligence foundation. The unified customer-engagement platform is the next and substantially larger build. The purpose of this presentation is to show where the current work genuinely reduces delivery risk, where the patterns are reusable, and where new platform engineering is required.

Position this as a working prototype plus a proposed roadmap. Do not call the current demo an omnichannel platform or autonomous call bot.
  `);
}

// -----------------------------------------------------------------------------
// 2 — Problem
// -----------------------------------------------------------------------------
{
  const c = newSlide("Problem statement", "The real problem is fragmented context");

  c.text("TODAY", 0.82, 1.72, 1.1, 0.24, { fontFace: F.mono, fontSize: 9, color: C.red, bold: true, charSpacing: 1.5 });
  const fragments = [
    ["WEB CHAT", "Roof damage\nContact details", 0.82, 2.12],
    ["PHONE", "“Please repeat\nthat information.”", 3.22, 2.12],
    ["CRM", "Partial lead\nrecord", 0.82, 4.15],
    ["SCHEDULING", "Manual lookup\n+ follow-up", 3.22, 4.15],
  ];
  fragments.forEach(([title, body, x, y]) => {
    c.rect(x, y, 2.05, 1.25, { fill: C.panel, line: C.red, lineWidth: 0.8, radius: true });
    c.text(title, x + 0.18, y + 0.17, 1.7, 0.22, { fontFace: F.mono, fontSize: 8, color: C.red, bold: true, charSpacing: 1.1 });
    c.text(body, x + 0.18, y + 0.5, 1.7, 0.56, { fontSize: 13, color: C.ink });
  });
  c.line(2.88, 2.75, 0, 1.65, { color: C.red, width: 1.2, dash: "dash" });
  c.line(1.86, 3.72, 2.36, 0, { color: C.red, width: 1.2, dash: "dash" });
  pill(c, "context lost", 2.12, 3.52, 1.52, { color: C.red, fill: C.redDark });

  c.text("What fragmentation causes", 6.15, 1.76, 5.75, 0.38, { fontSize: 20, color: C.ink, bold: true });
  const bullets = [
    ["Customers repeat themselves", "when they change channels, devices, or employees."],
    ["Agents search across systems", "while customers wait and conversations lose momentum."],
    ["Qualification and follow-up break", "across disconnected CRM, calendar, and messaging tools."],
    ["Inconsistent actions lose opportunities", "because context and policy do not travel with the customer."],
  ];
  bullets.forEach(([head, body], i) => bullet(c, [
    { text: `${head} — `, options: { bold: true, color: C.mint } },
    { text: body, options: { color: C.ink } },
  ], 6.18, 2.36 + i * 0.83, 5.85, { h: 0.58, fontSize: 15 }));

  c.rect(6.15, 5.9, 5.88, 0.62, { fill: C.blueDark, line: C.blue, lineWidth: 1, radius: true });
  c.text("The goal: one informed organization — not another standalone bot.", 6.46, 5.99, 5.25, 0.4, { fontSize: 13, color: C.blue, bold: true, align: "center", valign: "mid" });

  addNotes(c, `
Problem statement

The requirement is not merely a voice bot, chatbot, or scheduling tool. The business problem is that customers encounter disconnected channels, systems, and employees. Information collected in one interaction is unavailable in the next; qualification is repeated after transfers; and scheduling, follow-up, and CRM changes require manual work.

The target is an identity-resolved customer-engagement platform that can operate as a 24/7 virtual sales and service representative while involving a person whenever customer preference, policy, confidence, or failure demands it.
  `);
}

// -----------------------------------------------------------------------------
// 3 — North star
// -----------------------------------------------------------------------------
{
  const c = newSlide("Core design principle", "One identity-resolved conversation", "Every channel contributes to the same durable customer journey.", C.blue);

  const channels = ["WEB", "VOICE", "SMS", "EMAIL", "WHATSAPP", "MESSENGER", "FUTURE API"];
  channels.forEach((label, i) => {
    const x = 0.8 + i * 1.72;
    pill(c, label, x, 1.9, 1.48, { color: i === 1 ? C.amber : C.blue, fill: i === 1 ? C.amberDark : C.blueDark, fontSize: 7.2 });
    c.line(x + 0.74, 2.25, 0, 0.47, { color: i === 1 ? C.amber : C.blue, width: 1 });
  });

  c.rect(1.08, 2.74, 11.15, 0.82, { fill: C.panel, line: C.blue, lineWidth: 1.4, radius: true });
  c.text("IDENTITY RESOLUTION  +  UNIFIED CUSTOMER & CONVERSATION HUB", 1.34, 2.97, 10.64, 0.25, { fontFace: F.mono, fontSize: 13, color: C.blue, bold: true, align: "center", charSpacing: 1.1 });
  c.text("verified mobile  •  email  •  CRM ID  •  authenticated login  •  signed session", 1.46, 3.3, 10.38, 0.16, { fontFace: F.mono, fontSize: 7.6, color: C.dim, align: "center" });
  c.line(6.66, 3.57, 0, 0.43, { color: C.blue, width: 1.4 });

  c.text("CONTEXT PACKAGE BEFORE EVERY INTERACTION", 1.12, 4.04, 11.05, 0.25, { fontFace: F.mono, fontSize: 9, color: C.mint, bold: true, align: "center", charSpacing: 1.4 });
  const packet = ["History + summary", "Customer profile", "Qualification status", "Appointments", "Open opportunities", "Recommended action"];
  packet.forEach((label, i) => {
    const x = 0.98 + (i % 3) * 4.06;
    const y = 4.46 + Math.floor(i / 3) * 0.65;
    c.rect(x, y, 3.65, 0.46, { fill: C.mintDark, line: C.mint, lineWidth: 0.7, radius: true });
    c.text(label, x + 0.16, y + 0.12, 3.33, 0.18, { fontSize: 11, color: C.ink, bold: true, align: "center" });
  });

  c.rect(1.82, 5.98, 9.67, 0.48, { fill: C.status, line: C.line, lineWidth: 0.8, radius: true });
  c.text("AI and employees begin informed  •  customers continue without repetition", 2.12, 6.12, 9.08, 0.18, { fontSize: 13, color: C.mint, bold: true, align: "center" });

  c.text("Caller ID can suggest a match; sensitive actions require verification. Identity merges must be auditable and reversible.", 1.05, 6.62, 11.2, 0.19, { fontSize: 9, color: C.dim, italic: true, align: "center" });

  addNotes(c, `
North star

The unified customer and conversation record is the architectural center of the product, not a later integration. Every interaction is normalized into one timeline and linked, when safely possible, to a canonical customer profile.

Identity rules must include confidence, verification, consent, provenance, collision handling, manual review, and merge/unmerge controls. Caller ID is a useful candidate signal but must not authenticate sensitive changes.

Before every AI or employee interaction, assemble the authorized context packet shown on the slide, including prior purchases where relevant and source/freshness timestamps.

Google Business Messages, named in the original proposal, was discontinued on July 31, 2024. Select a current Google or third-party entry point during discovery. Official notice: https://developers.google.com/business-communications/business-messages/resources/release-notes/update-on-gbm?hl=en
  `);
}

// -----------------------------------------------------------------------------
// 4 — Current capability
// -----------------------------------------------------------------------------
{
  const c = newSlide("Demonstrated", "What Cybic has already built", "A working single-seat desktop agent-assist prototype — not an autonomous call platform.");
  pill(c, "Current capability", 10.48, 1.43, 1.83, { color: C.amber, fill: C.amberDark });

  const stages = [
    ["01", "TWO-SIDED\nAUDIO"],
    ["02", "LOCAL\nSTT"],
    ["03", "REQUEST\nGATE"],
    ["04", "SESSION\nCONTEXT"],
    ["05", "ANSWER\nCARD"],
    ["06", "AUDIT\n+ LOG"],
  ];
  stages.forEach(([num, label], i) => {
    const x = 0.78 + i * 2.04;
    c.rect(x, 2.04, 1.64, 0.98, { fill: C.panel, line: C.amber, lineWidth: i === 4 ? 1.5 : 0.8, radius: true });
    c.text(num, x + 0.15, 2.16, 0.35, 0.17, { fontFace: F.mono, fontSize: 7.5, color: C.dim, bold: true });
    c.text(label, x + 0.15, 2.42, 1.34, 0.39, { fontFace: F.mono, fontSize: 9.2, color: C.ink, bold: true, align: "center", valign: "mid" });
    if (i < stages.length - 1) c.shape("chevron", x + 1.72, 2.39, 0.22, 0.24, { fill: C.amber, line: C.amber });
  });

  c.rect(0.78, 3.5, 5.42, 2.35, { fill: C.panel, line: C.line, lineWidth: 1, radius: true });
  c.text("LIVE TRANSCRIPT", 1.02, 3.72, 1.8, 0.2, { fontFace: F.mono, fontSize: 8, color: C.dim, bold: true, charSpacing: 1 });
  c.text("CUSTOMER", 1.02, 4.12, 0.95, 0.2, { fontFace: F.mono, fontSize: 8, color: C.amber, bold: true });
  c.text("Does the premium plan include international roaming?", 2.0, 4.08, 3.82, 0.46, { fontSize: 13, color: C.ink });
  c.text("AGENT", 1.02, 4.78, 0.95, 0.2, { fontFace: F.mono, fontSize: 8, color: C.dim, bold: true });
  c.text("Let me check that for you.", 2.0, 4.74, 3.82, 0.32, { fontSize: 13, color: C.dim });
  c.rect(1.0, 5.35, 4.98, 0.28, { fill: C.status, line: C.line, lineWidth: 0.5, radius: true });
  c.text("mic:on  sys:on  whisper:cuda  gate:balanced", 1.2, 5.43, 4.6, 0.12, { fontFace: F.mono, fontSize: 6.9, color: C.mint });

  c.rect(6.5, 3.5, 5.82, 2.35, { fill: C.panel, line: C.amber, lineWidth: 1.2, radius: true });
  c.text("Q  Does the premium plan include international roaming?", 6.78, 3.77, 5.28, 0.36, { fontSize: 12.5, color: C.amber, bold: true });
  c.text("A  Yes — Premium includes roaming in 48 countries at no extra charge; data is capped at 5 GB per trip.", 6.78, 4.27, 5.22, 0.72, { fontSize: 14.5, color: C.ink, bold: true });
  bullet(c, "48 countries; no add-on needed", 6.83, 5.13, 2.75, { h: 0.28, fontSize: 10.5, color: C.mint });
  bullet(c, "5 GB roaming cap per trip", 9.42, 5.13, 2.5, { h: 0.28, fontSize: 10.5, color: C.mint });

  c.rect(0.78, 6.16, 11.54, 0.45, { fill: C.amberDark, line: C.amber, lineWidth: 0.8, radius: true });
  c.text("Built now: audio capture • local transcription • information-request detection • short-session context • cards • audit • replay • optional Linux TTS", 1.03, 6.29, 11.02, 0.18, { fontSize: 10.5, color: C.amber, bold: true, align: "center", fit: "shrink" });

  addNotes(c, `
Current capability

The live repository captures both the agent microphone and customer/system audio from desktop softphones or WebRTC dialers without a telephony integration. It performs local speech transcription, detects information-seeking utterances, assembles a short current-session context window, generates concise answer cards, audits answers, recovers missed questions, and writes replayable local JSONL session logs.

Switchable Markdown profiles provide Topic, Background, and Vocabulary context. They are not an enterprise knowledge base or guided workflow builder.

An optional local Linux voice mode uses Kokoro with an espeak fallback. It plays through the workstation speakers; it does not inject speech into a phone media stream and is not full-duplex autonomous Voice AI.

Source: current Ambient repository and docs/demo/CALLCENTER-DEMO.md, reviewed August 2026.
  `);
}

// -----------------------------------------------------------------------------
// 5 — Prototype evidence
// -----------------------------------------------------------------------------
{
  const c = newSlide("Demonstrated", "Measured on the prototype", "Internal benchmarks establish engineering evidence — not production SLAs.");

  c.text("QUESTION ENDS", 0.85, 2.06, 1.32, 0.2, { fontFace: F.mono, fontSize: 8, color: C.dim, bold: true, charSpacing: 1 });
  c.line(1.04, 2.62, 10.9, 0, { color: C.line, width: 3 });
  const marks = [
    [1.28, "0.2–0.3 s", "LOCAL STT", C.amber],
    [3.55, "0.5–0.6 s", "LOCAL GATE", C.amber],
    [6.28, "3.5–4.5 s", "READABLE CARD", C.mint],
    [9.05, "~8 s behind", "AUDIT, IF NEEDED", C.mint],
    [11.48, "≤25 s", "MISSED-Q RECOVERY", C.blue],
  ];
  marks.forEach(([x, value, label, color]) => {
    c.ellipse(x, 2.46, 0.32, 0.32, { fill: C.bg, line: color, lineWidth: 2 });
    c.text(value, x - 0.48, 2.0, 1.28, 0.25, { fontFace: F.mono, fontSize: 10, color, bold: true, align: "center" });
    c.text(label, x - 0.62, 2.96, 1.58, 0.35, { fontFace: F.mono, fontSize: 7.5, color: C.ink, bold: true, align: "center", charSpacing: 0.8 });
  });

  c.rect(0.82, 3.75, 3.55, 1.88, { fill: C.panel, line: C.mint, lineWidth: 1, radius: true });
  c.text("26 / 26", 1.08, 4.06, 3.02, 0.52, { fontFace: F.mono, fontSize: 26, color: C.mint, bold: true, align: "center" });
  c.text("labelled balanced gate set", 1.05, 4.72, 3.08, 0.25, { fontSize: 12, color: C.ink, align: "center" });
  c.text("0 false positives in that set", 1.05, 5.1, 3.08, 0.2, { fontSize: 10, color: C.dim, align: "center" });

  c.rect(4.66, 3.75, 3.55, 1.88, { fill: C.panel, line: C.amber, lineWidth: 1, radius: true });
  c.text("LOCAL FIRST", 4.92, 4.16, 3.02, 0.35, { fontFace: F.mono, fontSize: 17, color: C.amber, bold: true, align: "center", charSpacing: 1 });
  c.text("audio • STT • first gate", 4.92, 4.75, 3.02, 0.24, { fontSize: 12, color: C.ink, align: "center" });
  c.text("accepted text + bounded context may leave device", 4.92, 5.1, 3.02, 0.28, { fontSize: 9.5, color: C.dim, align: "center" });

  c.rect(8.5, 3.75, 3.55, 1.88, { fill: C.panel, line: C.blue, lineWidth: 1, radius: true });
  c.text("REPLAYABLE", 8.76, 4.16, 3.02, 0.35, { fontFace: F.mono, fontSize: 17, color: C.blue, bold: true, align: "center", charSpacing: 1 });
  c.text("decisions • status • latency", 8.76, 4.75, 3.02, 0.24, { fontSize: 12, color: C.ink, align: "center" });
  c.text("local JSONL — not enterprise history", 8.76, 5.1, 3.02, 0.24, { fontSize: 9.5, color: C.dim, align: "center" });

  c.text("Prototype figures are configuration- and hardware-specific; repeat with production providers, real domain data, and agreed acceptance thresholds.", 1.02, 6.22, 11.25, 0.36, { fontSize: 10.5, color: C.dim, italic: true, align: "center" });

  addNotes(c, `
Prototype evidence

The original call-center demo documents warm GPU transcription at approximately 0.2–0.3 seconds per utterance, local LLM gating at approximately 0.5–0.6 seconds, and a readable answer card approximately 3.5–4.5 seconds after the question ends. Audit correction, when required, runs behind the card; the missed-question sweep runs every 25 seconds.

The labelled balanced-mode gate evaluation set documented in the demo had 26 of 26 correct decisions and no false positives. This is a small internal set and must not be presented as a production accuracy claim.

Do not quote the stale automated-test count from the older HTML deck. Current engineering verification should be re-run before an external technical diligence session.
  `);
}

// -----------------------------------------------------------------------------
// 6 — Selective alignment
// -----------------------------------------------------------------------------
{
  const c = newSlide("Alignment", "Selective alignment — credible because it is incomplete", "The proposal is substantially larger than the demonstrated product.", C.ink);

  card(c, 0.78, 1.92, 3.25, 4.45, "Direct overlap", "Two-sided audio capture\n\nLocal transcription\n\nInformation-request detection\n\nShort-session context\n\nContextual answer cards\n\nEvaluation signals\n\nInteraction logs", { color: C.amber, fill: C.amberDark, tag: "Demonstrated", tagWidth: 1.45, bodySize: 13.2 });
  card(c, 4.22, 1.92, 3.25, 4.45, "Reusable patterns", "Privacy-aware local processing\n\nConfigurable context profiles\n\nOptional local TTS\n\nAnswer audit and recovery\n\nSession replay\n\nResilient real-time pipelines", { color: C.mint, fill: C.mintDark, tag: "Reusable", tagWidth: 1.25, bodySize: 13.2 });
  card(c, 7.66, 1.92, 4.88, 4.45, "Substantial net-new work", "Telephony + call lifecycle  •  persistent identity  •  omnichannel history\n\nLead state + qualification  •  managed knowledge/RAG  •  intelligent scheduling\n\nCRM actions  •  live transfer  •  campaigns + automation\n\nAdmin + analytics  •  APIs/webhooks  •  multitenancy + enterprise scale", { color: C.blue, fill: C.blueDark, tag: "Proposed", tagWidth: 1.25, bodySize: 13.1, titleSize: 18 });

  c.text("The credible story: “We have implemented this component; the surrounding platform is proposed work.”", 0.96, 6.58, 11.45, 0.26, { fontSize: 13, color: C.ink, bold: true, align: "center" });

  addNotes(c, `
Selective alignment

Do not try to map every line of the proposal onto the demo. The current repository has direct overlap with a limited slice of the voice and conversation-intelligence layer, adjacent experience in a few operational patterns, and substantial non-overlap.

This is intentional. It shows prior experience without creating the impression that a deck or prototype was assembled to mirror the prospect's language. Most of the requested platform remains a meaningful implementation engagement.

Use the exact status vocabulary consistently: Demonstrated, Reusable pattern, Proposed MVP, and Later phase.
  `);
}

// -----------------------------------------------------------------------------
// 7 — Live demo guardrails
// -----------------------------------------------------------------------------
{
  const c = newSlide("Live demonstration", "What the demo should — and should not — prove", "Stop at working functionality; label the expansion path separately.");

  c.rect(0.78, 1.88, 5.68, 4.65, { fill: C.mintDark, line: C.mint, lineWidth: 1.1, radius: true });
  pill(c, "Show live", 1.04, 2.12, 1.18, { color: C.mint, fill: C.mintDark });
  const show = [
    "Capture agent mic + customer/system audio",
    "Show live local transcription",
    "Ignore narration and small talk",
    "Answer direct and indirect information requests",
    "Resolve a follow-up from current-session context",
    "Switch a local context profile",
    "Audit, recover, and replay the session",
  ];
  show.forEach((text, i) => {
    c.ellipse(1.05, 2.66 + i * 0.48, 0.24, 0.24, { fill: C.mint, line: C.mint });
    c.text(String(i + 1), 1.105, 2.716 + i * 0.48, 0.13, 0.12, { fontFace: F.mono, fontSize: 6.5, color: C.bg, bold: true, align: "center" });
    c.text(text, 1.47, 2.63 + i * 0.48, 4.58, 0.28, { fontSize: 12.2, color: C.ink });
  });

  c.rect(6.78, 1.88, 5.54, 4.65, { fill: C.redDark, line: C.red, lineWidth: 1.1, radius: true });
  pill(c, "Do not imply", 7.04, 2.12, 1.58, { color: C.red, fill: C.redDark });
  const dont = [
    "Inbound call answering or outbound dialing",
    "Full-duplex voice routed through telephony",
    "Persistent customer identity or omnichannel memory",
    "Qualification, booking, CRM writes, or SMS",
    "Warm transfer, campaigns, dashboards, or multitenancy",
  ];
  dont.forEach((text, i) => {
    c.text("×", 7.08, 2.69 + i * 0.66, 0.22, 0.3, { fontSize: 16, color: C.red, bold: true, align: "center", valign: "mid" });
    c.text(text, 7.48, 2.72 + i * 0.66, 4.45, 0.39, { fontSize: 12.5, color: C.ink });
  });
  c.text("Discuss these as roadmap items — never as mocked working screens.", 7.06, 6.03, 4.97, 0.28, { fontSize: 10.5, color: C.red, italic: true, align: "center" });

  statusBar(c, "● listening   mic:on   sys:on   gate:balanced   profile:support-queue   mode:agent-assist", 6.62);

  addNotes(c, `
Recommended live-demo sequence

1. Stage a caller, helpline, and call-center employee desktop.
2. Let the agent speak first and show that nothing appears; the information-request gate is the product.
3. Let the customer ask a clear question and show the concise answer card.
4. Ask an indirect or command-form question.
5. Ask a contextual follow-up.
6. Show profile switching, answer correction or missed-question recovery, and session replay if time allows.

Do not simulate CRM, booking, customer identity, warm transfer, or omnichannel continuity. The demo should establish the voice-intelligence foundation; the architecture and MVP slides explain how it is extended.
  `);
}

// -----------------------------------------------------------------------------
// 8 — MVP journey
// -----------------------------------------------------------------------------
{
  const c = newSlide("Proposed MVP", "The proof point: change channels without starting over", "One controlled journey demonstrates the platform's defining differentiator.", C.blue);
  pill(c, "Proposed MVP", 10.78, 1.43, 1.5, { color: C.blue, fill: C.blueDark });

  const steps = [
    ["01", "WEB CHAT", "Describe roof damage"],
    ["02", "IDENTITY", "Verify mobile number"],
    ["03", "PHONE", "Call five minutes later"],
    ["04", "CONTEXT", "Restore history + summary"],
    ["05", "QUALIFY", "ZIP • urgency • skills"],
    ["06", "SCHEDULE", "Offer best valid slot"],
    ["07", "ACT", "Update CRM + send SMS"],
    ["08", "HANDOFF", "Pass full context packet"],
  ];
  steps.forEach(([num, title, body], i) => {
    const row = i < 4 ? 0 : 1;
    const col = row === 0 ? i : 7 - i;
    const x = 0.82 + col * 3.05;
    const y = row === 0 ? 2.0 : 4.55;
    const highlight = i === 2 || i === 3;
    c.rect(x, y, 2.48, 1.35, { fill: highlight ? C.blueDark : C.panel, line: highlight ? C.blue : C.line, lineWidth: highlight ? 1.4 : 0.8, radius: true });
    c.text(num, x + 0.16, y + 0.16, 0.36, 0.2, { fontFace: F.mono, fontSize: 8, color: highlight ? C.blue : C.dim, bold: true });
    c.text(title, x + 0.52, y + 0.16, 1.72, 0.22, { fontFace: F.mono, fontSize: 8.5, color: highlight ? C.blue : C.amber, bold: true, align: "right", charSpacing: 0.8 });
    c.text(body, x + 0.18, y + 0.59, 2.12, 0.45, { fontSize: 12.5, color: C.ink, bold: true, align: "center", valign: "mid" });
    if (i < 3) c.shape("chevron", x + 2.62, y + 0.56, 0.24, 0.24, { fill: C.blue, line: C.blue });
    if (i > 4) c.shape("chevron", x - 0.38, y + 0.56, 0.24, 0.24, { fill: C.blue, line: C.blue, rotate: 180 });
  });
  c.shape("downArrow", 11.36, 3.56, 0.28, 0.54, { fill: C.blue, line: C.blue });
  c.rect(3.98, 3.64, 5.36, 0.5, { fill: C.status, line: C.blue, lineWidth: 1, radius: true });
  c.text("DEFINING MOMENT: WEB → PHONE, SAME JOURNEY", 4.28, 3.79, 4.76, 0.18, { fontFace: F.mono, fontSize: 10, color: C.blue, bold: true, align: "center", charSpacing: 1 });
  c.text("Acceptance criterion: the customer does not repeat already verified information, and every approved action is recorded once.", 1.03, 6.3, 11.3, 0.34, { fontSize: 11, color: C.dim, italic: true, align: "center" });

  addNotes(c, `
MVP customer journey

The customer discusses roof damage in web chat, provides and verifies a mobile number, then calls five minutes later. The AI or employee receives the earlier history and summary; qualification continues without repetition. Service-area, urgency, and required-skill rules are validated. The platform offers a valid representative and slot, receives confirmation once, updates the CRM, sends transactional SMS, and prepares a warm-transfer packet if escalation occurs.

This journey is proposed, not demonstrated today. It should be the principal MVP acceptance scenario because it tests persistent identity, cross-channel continuity, knowledge, qualification, policy, scheduling, CRM, messaging, handoff, and audit in one business outcome.
  `);
}

// -----------------------------------------------------------------------------
// 9 — MVP boundary
// -----------------------------------------------------------------------------
{
  const c = newSlide("Proposed MVP", "A bounded MVP — with expansion designed in", "Prove continuity and governed action before multiplying channels and connectors.", C.blue);

  c.rect(0.82, 1.92, 7.25, 4.62, { fill: C.blueDark, line: C.blue, lineWidth: 1.3, radius: true });
  pill(c, "Include", 1.08, 2.16, 0.92, { color: C.blue, fill: C.blueDark });
  const include = [
    ["Channels", "Website chat • inbound voice • transactional SMS"],
    ["Journey", "3–5 high-value intents • verified identity • unified timeline"],
    ["Intelligence", "Approved knowledge + citations • structured capture • qualification"],
    ["Business action", "ZIP/service-area rules • one calendar • one CRM"],
    ["People", "Warm handoff with transcript, summary, status, and next action"],
    ["Control", "Basic admin • audit • consent/retention • core funnel + AI metrics"],
  ];
  include.forEach(([head, body], i) => {
    const col = i % 2;
    const row = Math.floor(i / 2);
    const x = 1.08 + col * 3.38;
    const y = 2.74 + row * 1.05;
    c.text(head.toUpperCase(), x, y, 2.95, 0.2, { fontFace: F.mono, fontSize: 8, color: C.blue, bold: true, charSpacing: 1 });
    c.text(body, x, y + 0.3, 2.95, 0.5, { fontSize: 11.5, color: C.ink });
  });

  c.rect(8.35, 1.92, 3.96, 4.62, { fill: C.panel, line: C.line, lineWidth: 1, radius: true });
  pill(c, "Later expansion", 8.62, 2.16, 1.58, { color: C.dim, fill: C.panel });
  const later = [
    "Every CRM and social channel",
    "Broad outbound campaigns",
    "Probabilistic identity merging",
    "Advanced route/travel optimization",
    "Full executive BI",
    "Multi-company self-service",
    "Unrestricted AI autonomy",
  ];
  later.forEach((text, i) => bullet(c, text, 8.66, 2.74 + i * 0.47, 3.25, { h: 0.28, fontSize: 11.2, color: C.dim }));

  c.text("One company at launch; include tenant, brand, location, and business-unit keys in every record and authorization boundary.", 1.04, 6.7, 11.24, 0.22, { fontSize: 9.6, color: C.dim, italic: true, align: "center" });

  addNotes(c, `
MVP boundary

The launch scope should be deliberately narrow while the data model and authorization boundaries remain ready for multiple companies, brands, locations, and business units. Select one CRM, one calendar ecosystem, and the most valuable three to five journeys during discovery.

Knowledge for MVP should support controlled ingestion, parsing, indexing, permission-aware retrieval, citations, versioning, administrator approval, publishing, rollback, and freshness checks. "Continuous learning" must mean approved knowledge and evaluation-driven improvement, not uncontrolled training from customer calls.

Website chat requires anonymous/authenticated session handling, cross-channel resume, and safe document/photo upload if included. File upload requires type/size validation, malware scanning, object storage, access control, and retention rules.
  `);
}

// -----------------------------------------------------------------------------
// 10 — Architecture
// -----------------------------------------------------------------------------
{
  const c = newSlide("Proposed architecture", "Shared context at the center", "AI proposes; deterministic policy governs; systems of record remain authoritative.", C.blue);

  const layers = [
    ["CHANNEL + MEDIA ADAPTERS", "Voice  •  Web  •  SMS  •  Email  •  Messaging  •  Future APIs", C.blue, 1.86],
    ["UNIFIED INTERACTION GATEWAY", "canonical events  •  consent  •  delivery  •  idempotency", C.blue, 2.55],
    ["IDENTITY + CONVERSATION HUB", "customer profile  •  durable timeline  •  journey state", C.mint, 3.24],
    ["CONTEXT PACKAGE + AGENTIC ORCHESTRATOR", "history  •  summary  •  knowledge/RAG  •  qualification  •  next action", C.mint, 3.93],
    ["POLICY + TOOL GATEWAY + DURABLE WORKFLOW", "rules  •  approval  •  retries  •  reconciliation  •  audit", C.amber, 4.62],
    ["SYSTEMS OF RECORD", "CRM  •  calendars  •  dispatch  •  contact center  •  messaging", C.amber, 5.31],
  ];
  layers.forEach(([title, body, color, y], i) => {
    const inset = i < 2 ? 0 : i < 4 ? 0.28 : 0.56;
    c.rect(1.22 + inset, y, 8.92 - inset * 2, 0.52, { fill: color === C.blue ? C.blueDark : color === C.mint ? C.mintDark : C.amberDark, line: color, lineWidth: 0.9, radius: true });
    c.text(title, 1.48 + inset, y + 0.1, 3.45, 0.18, { fontFace: F.mono, fontSize: 8.4, color, bold: true, charSpacing: 0.6 });
    c.text(body, 4.7, y + 0.1, 5.08 - inset, 0.18, { fontSize: 9.6, color: C.ink, align: "right" });
  });

  c.rect(10.4, 1.86, 1.92, 1.84, { fill: C.panel, line: C.blue, lineWidth: 0.8, radius: true });
  c.text("ADMIN\nCONTROL PLANE", 10.64, 2.13, 1.44, 0.43, { fontFace: F.mono, fontSize: 9.5, color: C.blue, bold: true, align: "center" });
  c.text("tenants\nusers + roles\nknowledge\nprompts + rules\nintegrations", 10.64, 2.7, 1.44, 0.77, { fontSize: 10, color: C.ink, align: "center" });

  c.rect(10.4, 3.94, 1.92, 1.89, { fill: C.panel, line: C.mint, lineWidth: 0.8, radius: true });
  c.text("EVENT +\nANALYTICS RAIL", 10.64, 4.2, 1.44, 0.43, { fontFace: F.mono, fontSize: 9.5, color: C.mint, bold: true, align: "center" });
  c.text("audit events\noperational metrics\nAI evaluation\nwarehouse\nreporting", 10.64, 4.78, 1.44, 0.76, { fontSize: 10, color: C.ink, align: "center" });

  c.rect(1.48, 6.08, 10.52, 0.46, { fill: C.status, line: C.line, lineWidth: 0.7, radius: true });
  c.text("Directional stack: React/Next.js  •  Python/FastAPI  •  PostgreSQL/pgvector  •  Redis  •  object storage  •  managed events  •  Temporal-equivalent  •  OpenTelemetry  •  containers + IaC", 1.7, 6.16, 10.08, 0.28, { fontFace: F.mono, fontSize: 6.8, color: C.dim, align: "center", valign: "mid", fit: "shrink" });

  addNotes(c, `
Target architecture

Each channel has its own adapter but publishes the same canonical interaction event. The platform owns conversation history, summaries, consent evidence, AI decisions, and action audit. The CRM remains authoritative for contacts and opportunities; calendar and dispatch systems remain authoritative for availability.

Models never receive unrestricted CRM or database access. Tools must be tenant-scoped, least-privilege, schema-validated, idempotent, retryable, replay-protected, observable, and audited. Sensitive actions require customer confirmation or employee approval according to deterministic policy.

Long-running reminders, campaigns, retries, and follow-ups use a durable workflow engine—not model memory or an in-process queue. Production business actions must never be silently dropped.

The stack shown is directional and provider-adaptable, not a final commitment. Current Python/asyncio, Whisper, gate, answer, TTS, and test assets can inform reuse; the Textual UI, JSONL store, local playback, and command-line model invocation are not the production architecture.
  `);
}

// -----------------------------------------------------------------------------
// 11 — Expansion workstreams
// -----------------------------------------------------------------------------
{
  const c = newSlide("Target platform", "How capability expands beyond the MVP", "Requested brands and channels are integration candidates — not completed connectors.", C.blue);
  const rows = [
    ["CHANNELS", "MVP", "Web • inbound voice • transactional SMS", "TARGET", "Outbound voice • email • WhatsApp • Messenger • mobile • future APIs"],
    ["KNOWLEDGE", "MVP", "Curated FAQs + service content, citations, approval", "TARGET", "Products • pricing • warranties • SOPs • PDFs • scripts • website content"],
    ["SCHEDULING", "MVP", "Availability • duration • ZIP • territory • skills", "TARGET", "Travel/GPS • workload • priority • emergency • explainable optimization"],
    ["CRM + SYSTEMS", "MVP", "One CRM + one calendar ecosystem", "TARGET", "Dynamics • Salesforce • HubSpot • ServiceTitan • AccuLynx • JobNimbus • LeadPerfection • custom APIs"],
    ["AUTOMATION", "MVP", "Lead/appointment actions • confirmation • handoff", "TARGET", "Campaigns • reminders • reviews • re-engagement • manager alerts • tasks"],
    ["SCALE", "MVP", "One company; tenant-aware data and authorization", "TARGET", "Companies • brands • locations • business units • additional agents • high concurrency"],
  ];
  c.text("WORKSTREAM", 0.82, 1.82, 1.35, 0.2, { fontFace: F.mono, fontSize: 8, color: C.dim, bold: true, charSpacing: 1 });
  c.text("PROPOSED MVP", 2.34, 1.82, 3.8, 0.2, { fontFace: F.mono, fontSize: 8, color: C.blue, bold: true, charSpacing: 1 });
  c.text("TARGET PLATFORM", 6.45, 1.82, 5.63, 0.2, { fontFace: F.mono, fontSize: 8, color: C.mint, bold: true, charSpacing: 1 });
  rows.forEach(([label, _m, mvp, _t, target], i) => {
    const y = 2.2 + i * 0.72;
    c.rect(0.78, y, 11.55, 0.57, { fill: i % 2 ? C.status : C.panel, line: C.line, lineWidth: 0.4, radius: true });
    c.text(label, 0.98, y + 0.16, 1.25, 0.17, { fontFace: F.mono, fontSize: 8.2, color: C.amber, bold: true, charSpacing: 0.7 });
    c.text(mvp, 2.34, y + 0.12, 3.75, 0.27, { fontSize: 10.2, color: C.ink, fit: "shrink" });
    c.shape("chevron", 6.17, y + 0.18, 0.2, 0.2, { fill: C.blue, line: C.blue });
    c.text(target, 6.55, y + 0.1, 5.48, 0.32, { fontSize: 9.2, color: C.ink, valign: "mid", fit: "shrink" });
  });
  c.text("Google Business Messages was discontinued 31 Jul 2024; select a current replacement during discovery.", 0.98, 6.62, 11.25, 0.2, { fontSize: 9.5, color: C.dim, italic: true, align: "center", hyperlink: { url: "https://developers.google.com/business-communications/business-messages/resources/release-notes/update-on-gbm?hl=en" } });

  addNotes(c, `
Expansion detail

Scheduling should be a policy and optimization service, not merely a free-slot search. Separate hard constraints from weighted preferences; use temporary slot holds, concurrency controls, idempotent booking, explainable assignment, and explicit customer confirmation. The requested factors include duration, territory, ZIP, driving distance, travel time, GPS where available, representative/technician availability, specialization, skills, workload, priority, emergency status, and approved performance factors.

The connector framework should support lookup, create, update, notes, lead status, follow-up tasks, appointments, recording/transcript references, and workflow triggers. Select the first CRM during discovery; do not attempt every vendor in MVP.

Voice expansion includes inbound/outbound call lifecycle, barge-in, voicemail detection/drop, appointment changes, follow-up SMS, recording/consent, warm transfer metadata, and full context for the receiving representative.

Google Business Messages correction source: https://developers.google.com/business-communications/business-messages/resources/release-notes/update-on-gbm?hl=en
  `);
}

// -----------------------------------------------------------------------------
// 12 — Phased delivery
// -----------------------------------------------------------------------------
{
  const c = newSlide("Delivery plan", "Build the platform in controlled stages", "Directional ranges assume a dedicated team and timely access to systems, policies, and data.", C.blue);

  const phases = [
    ["01", "DISCOVERY", "3–4 weeks", "Journeys • vendors • identity • compliance • KPIs • IP • acceptance"],
    ["02", "PLATFORM SPINE", "6–8 weeks", "Auth • canonical events • conversation store • knowledge • audit • shadow mode"],
    ["03", "END-TO-END MVP", "8–12 weeks", "Web + voice + SMS • continuity • qualification • CRM + calendar • handoff"],
    ["04", "CONTROLLED PILOT", "6–8 weeks", "Limited traffic • security/load tests • monitoring • recovery • reconciliation"],
    ["05", "EXPANSION WAVES", "8–12 weeks / wave", "Outbound • email/social • more connectors • campaigns • optimization • BI"],
  ];
  phases.forEach(([num, title, duration, body], i) => {
    const x = 0.77 + i * 2.48;
    const color = i < 3 ? C.blue : i === 3 ? C.mint : C.dim;
    const fill = i < 3 ? C.blueDark : i === 3 ? C.mintDark : C.panel;
    c.rect(x, 2.1, 2.16, 3.15, { fill, line: color, lineWidth: i < 3 ? 1.2 : 0.8, radius: true });
    c.text(num, x + 0.18, 2.31, 0.38, 0.2, { fontFace: F.mono, fontSize: 8, color, bold: true });
    c.text(title, x + 0.18, 2.78, 1.8, 0.48, { fontFace: F.mono, fontSize: 11, color, bold: true, align: "center", valign: "mid", charSpacing: 0.7 });
    c.text(duration, x + 0.18, 3.5, 1.8, 0.32, { fontSize: i === 4 ? 12.5 : 16, color: C.ink, bold: true, align: "center" });
    c.line(x + 0.32, 4.03, 1.52, 0, { color: C.line, width: 0.8 });
    c.text(body, x + 0.2, 4.23, 1.76, 0.72, { fontSize: 9.7, color: C.dim, align: "center", valign: "mid" });
    if (i < phases.length - 1) c.shape("chevron", x + 2.23, 3.55, 0.16, 0.24, { fill: C.line, line: C.line });
  });

  c.line(0.82, 5.72, 7.08, 0, { color: C.blue, width: 2 });
  c.line(0.82, 5.62, 0, 0.2, { color: C.blue, width: 2 });
  c.line(7.9, 5.62, 0, 0.2, { color: C.blue, width: 2 });
  c.rect(2.62, 5.48, 3.48, 0.48, { fill: C.blueDark, line: C.blue, lineWidth: 1, radius: true });
  c.text("CUSTOMER-FACING MVP: 17–24 WEEKS", 2.84, 5.63, 3.04, 0.18, { fontFace: F.mono, fontSize: 9.4, color: C.blue, bold: true, align: "center", charSpacing: 0.8 });

  c.rect(8.35, 5.48, 3.97, 0.72, { fill: C.status, line: C.line, lineWidth: 0.8, radius: true });
  c.text("Pilot hardening follows.\nThis is a planning range, not a fixed commercial commitment.", 8.64, 5.58, 3.39, 0.5, { fontSize: 9.5, color: C.dim, bold: true, align: "center", valign: "mid" });

  addNotes(c, `
Phasing

Phase 1 — Discovery and solution definition (3–4 weeks): select launch journeys, systems, channels, identity rules, compliance obligations, metrics, source/IP terms, and acceptance tests.

Phase 2 — Platform spine and shadow mode (6–8 weeks): tenant/auth foundation, canonical events, conversation store, verified identity, knowledge ingestion, adapter framework, audit, and AI recommendations without autonomous writes.

Phase 3 — End-to-end MVP (8–12 weeks): web, inbound voice, transactional SMS, cross-channel continuity, qualification, service-area validation, grounded answers, one calendar, one CRM, handoff, basic administration, and reporting.

Phase 4 — Controlled pilot (6–8 weeks): limited locations/traffic, security/load testing, monitoring, recovery, employee approvals, outcome reconciliation, domain evaluation, and measured automation.

Phase 5 — Expansion (8–12 weeks per wave): outbound, email/social, more connectors, campaigns, uploads, advanced scheduling, richer BI, and multi-company self-service.

Dependencies include a dedicated cross-functional team and timely sandbox, policy, and data access. The 17–24 week MVP range is directional, not a quote.
  `);
}

// -----------------------------------------------------------------------------
// 13 — AI and policy
// -----------------------------------------------------------------------------
{
  const c = newSlide("AI model strategy", "AI can reason; policy decides", "Use task-specific models and governed tools rather than one unrestricted model.", C.mint);

  c.rect(0.78, 1.92, 4.0, 4.55, { fill: C.mintDark, line: C.mint, lineWidth: 1.1, radius: true });
  pill(c, "AI intelligence", 1.05, 2.16, 1.52, { color: C.mint, fill: C.mintDark });
  const ai = [
    ["STREAMING STT / TTS", "quality • latency • interruption • privacy • cost"],
    ["SMALL FAST MODELS", "intent • entity extraction • confidence"],
    ["DIALOGUE / REASONING", "complex responses • orchestration • summary"],
    ["RETRIEVAL", "embeddings • reranking • permission-aware citations"],
  ];
  ai.forEach(([head, body], i) => {
    c.text(head, 1.07, 2.78 + i * 0.78, 3.43, 0.19, { fontFace: F.mono, fontSize: 8.3, color: C.mint, bold: true, charSpacing: 0.7 });
    c.text(body, 1.07, 3.04 + i * 0.78, 3.43, 0.36, { fontSize: 10.3, color: C.ink, valign: "mid" });
  });

  c.shape("rightArrow", 5.08, 3.3, 1.08, 1.34, { fill: C.amberDark, line: C.amber, lineWidth: 1 });
  c.text("POLICY", 5.24, 3.68, 0.72, 0.2, { fontFace: F.mono, fontSize: 9, color: C.amber, bold: true, align: "center" });
  c.text("allow\nconfirm\napprove\nblock", 5.29, 3.96, 0.61, 0.48, { fontFace: F.mono, fontSize: 6.8, color: C.ink, align: "center" });

  c.rect(6.48, 1.92, 5.85, 4.55, { fill: C.amberDark, line: C.amber, lineWidth: 1.1, radius: true });
  pill(c, "Governed execution", 6.75, 2.16, 1.92, { color: C.amber, fill: C.amberDark });
  const tools = [
    ["DETERMINISTIC RULES", "service area • eligibility • routing • action thresholds"],
    ["VALIDATED TOOLS", "tenant-scoped • least privilege • schema checked"],
    ["DURABLE WORKFLOWS", "idempotency • retries • timeouts • dead letters"],
    ["AUDIT + RECOVERY", "confirmation • approval • rollback • reconciliation"],
  ];
  tools.forEach(([head, body], i) => {
    c.text(head, 6.77, 2.78 + i * 0.78, 5.25, 0.19, { fontFace: F.mono, fontSize: 8.3, color: C.amber, bold: true, charSpacing: 0.7 });
    c.text(body, 6.77, 3.04 + i * 0.78, 5.25, 0.36, { fontSize: 10.3, color: C.ink, valign: "mid" });
  });

  c.rect(2.1, 6.64, 9.18, 0.25, { fill: C.status, line: C.line, lineWidth: 0.5, radius: true });
  c.text("“Continuous learning” = administrator-approved knowledge + evaluation-led change — not autonomous training from customer calls.", 2.28, 6.7, 8.82, 0.12, { fontSize: 8.2, color: C.dim, italic: true, align: "center", fit: "shrink" });

  addNotes(c, `
Model strategy

Use a provider-neutral model gateway. Select speech providers for call quality, language, latency, barge-in/interruption support, privacy/data-use terms, reliability, and unit cost. Use smaller models for classification and structured extraction; stronger models for complex dialogue and orchestration; embeddings and rerankers for permission-aware grounded knowledge.

Business decisions and writes remain deterministic and tool-mediated. Establish primary/fallback providers through client-domain evaluations covering accuracy, latency, privacy, reliability, and cost. Version every model, prompt, knowledge publication, workflow, and connector so regression tests can block unsafe changes.
  `);
}

// -----------------------------------------------------------------------------
// 14 — Security/reliability
// -----------------------------------------------------------------------------
{
  const c = newSlide("Enterprise operation", "Security, evaluation, and reliability are product features", "Autonomy expands only when evidence and controls support it.", C.mint);

  const quads = [
    [0.8, 1.92, "SECURITY + IDENTITY", "SSO / OIDC • MFA\nRBAC + scoped attributes\nTenant isolation • encryption\nSecrets / KMS • PII controls\nConsent • retention • immutable audit", C.blue, C.blueDark],
    [6.68, 1.92, "EVALUATION + AI SAFETY", "Intent • extraction • grounding\nQualification • scheduling • action correctness\nPrompt-injection defenses\nStricter thresholds for autonomous actions\nVersioned regression gates", C.mint, C.mintDark],
    [0.8, 4.29, "RELIABILITY + OPERATIONS", "Durable queues • retries • dead letters\nQuotas • rate limits • health checks\nLogs • metrics • traces • alerts\nBackups • restore drills • incident response\nAgreed SLO / RTO / RPO", C.amber, C.amberDark],
    [6.68, 4.29, "COMMUNICATION COMPLIANCE", "AI + recording disclosure\nOutreach consent • do-not-call\nCalling windows • opt-out\nTemplate approval • retention by region\nLegal review of jurisdictional rules", C.red, C.redDark],
  ];
  quads.forEach(([x, y, title, body, color, fill]) => {
    c.rect(x, y, 5.82, 1.94, { fill, line: color, lineWidth: 1, radius: true });
    c.text(title, x + 0.26, y + 0.22, 5.28, 0.22, { fontFace: F.mono, fontSize: 9, color, bold: true, charSpacing: 0.7 });
    c.text(body, x + 0.26, y + 0.62, 5.28, 1.05, { fontSize: 11.1, color: C.ink, fit: "shrink" });
  });
  c.rect(2.05, 6.52, 9.24, 0.34, { fill: C.status, line: C.line, lineWidth: 0.6, radius: true });
  c.text("Current boundary: raw audio + STT + first gate local; accepted text + bounded context may reach the configured answer model. Current JSONL logs are plaintext.", 2.25, 6.61, 8.84, 0.15, { fontSize: 8.4, color: C.dim, align: "center", fit: "shrink" });

  addNotes(c, `
Enterprise controls

Security should include tenant, brand, location, customer, journey, and channel identifiers in every record; verified identity for sensitive actions; SSO/OIDC; MFA where appropriate; RBAC and scoped attributes; encryption in transit/at rest; managed secrets; PII handling; data residency; retention and deletion; immutable audit; backups/DR; dependency and vulnerability scanning; incident response; and explicit model/vendor data-use policy.

Evaluation should separately test intent, extraction, grounding, response, qualification, scheduling, routing, handoff, and action correctness. Employee suggestions can tolerate a different threshold than autonomous actions.

Current privacy must be described precisely: audio capture, speech-to-text, and the first request gate are local. Confirmed question text, recent transcript/context, and active profile material can reach the configured answer model. Local JSONL logs do not have enterprise access control, encryption, or retention enforcement.
  `);
}

// -----------------------------------------------------------------------------
// 15 — Admin, analytics, workflows
// -----------------------------------------------------------------------------
{
  const c = newSlide("Target operating model", "Administration, analytics, and automation close the loop", "Instrument the customer journey once; govern and measure every outcome.", C.blue);

  card(c, 0.78, 1.94, 3.72, 4.58, "Admin control plane", "Users + permissions\n\nKnowledge approve / publish / rollback\n\nPrompt + behavior versions\n\nWorkflow builder + rules\n\nIntegration credentials + health\n\nTranscript / recording review\n\nTemplates + tenant settings", { color: C.blue, fill: C.blueDark, tag: "Govern", tagWidth: 0.95, bodySize: 12.1, titleSize: 17 });
  card(c, 4.8, 1.94, 3.72, 4.58, "Executive analytics", "SALES\nLead • qualified • appointment • demo • close • revenue\n\nMARKETING\nSource • CPL • CPA • cost/sale • ROI\n\nCONTACT CENTER\nService level • ASA • AHT • abandon • occupancy • FCR • CSAT • NPS\n\nAI\nResolution • escalation • booking • accuracy • duration • automation • savings", { color: C.mint, fill: C.mintDark, tag: "Measure", tagWidth: 1.02, bodySize: 9.5, titleSize: 17 });
  card(c, 8.82, 1.94, 3.5, 4.58, "Durable automation", "Lead routing\n\nAppointment scheduling\n\nCRM updates + tasks\n\nConfirmations + reminders\n\nEmail / SMS campaigns\n\nReview + re-engagement\n\nInternal notifications\n\nManager alerts + recovery", { color: C.amber, fill: C.amberDark, tag: "Act", tagWidth: 0.75, bodySize: 11.8, titleSize: 17 });

  c.text("Define every metric's calculation, denominator, window, source system, owner, and tenant/location filters before launch.", 1.2, 6.68, 10.92, 0.19, { fontSize: 9.5, color: C.dim, italic: true, align: "center" });

  addNotes(c, `
Administration, analytics, and workflow detail

The current desktop profile picker, TOML configuration, and local session replay are not an administrative portal. The proposed portal needs user/permission management, knowledge approval and rollback, prompt/behavior configuration, workflow building, integration credential and health management, transcript/recording review, templates, automation rules, and reporting.

Business dashboards require a canonical event model and metric dictionary. Define each metric's numerator, denominator, time window, attribution, source, owner, and segmentation. Current local telemetry provides technical decisions and latency only; it does not implement sales, marketing, contact-center, or business-outcome dashboards.

Workflow automation must be durable: retries, idempotency, timeouts, dead-letter handling, manual recovery, and approval gates. Messaging automation also requires consent/preferences, opt-out, quiet hours, template review, deliverability/bounce handling, provider webhooks, and attribution.
  `);
}

// -----------------------------------------------------------------------------
// 16 — Technology, hosting, scale
// -----------------------------------------------------------------------------
{
  const c = newSlide("Technology + hosting", "Provider-adaptable, horizontally scalable, observable", "Prototype assets inform the build; local desktop components are not the target architecture.", C.blue);

  const stack = [
    ["EXPERIENCES", "Customer web chat  •  live-agent workspace  •  admin portal", C.blue, C.blueDark],
    ["SERVICES", "Python/FastAPI  •  identity  •  conversation  •  orchestration  •  integrations", C.mint, C.mintDark],
    ["DATA + WORKFLOW", "PostgreSQL/pgvector  •  Redis  •  object storage  •  managed events  •  Temporal-equivalent", C.amber, C.amberDark],
    ["CLOUD + OPERATIONS", "Containers  •  IaC  •  autoscaling  •  regional media workers  •  OpenTelemetry  •  backup/DR", C.blue, C.blueDark],
  ];
  stack.forEach(([title, body, color, fill], i) => {
    const x = 0.82 + i * 0.22;
    const y = 2.0 + i * 0.95;
    const w = 7.5 - i * 0.44;
    c.rect(x, y, w, 0.72, { fill, line: color, lineWidth: 0.9, radius: true });
    c.text(title, x + 0.22, y + 0.13, 1.62, 0.2, { fontFace: F.mono, fontSize: 8, color, bold: true, charSpacing: 0.7 });
    c.text(body, x + 1.92, y + 0.09, w - 2.16, 0.36, { fontSize: 8.9, color: C.ink, align: "right", valign: "mid", fit: "shrink" });
  });

  c.rect(8.75, 1.96, 3.57, 4.22, { fill: C.panel, line: C.line, lineWidth: 1, radius: true });
  pill(c, "Scale target", 9.02, 2.2, 1.3, { color: C.blue, fill: C.panel });
  const scale = [
    "Companies • brands • locations • business units",
    "Thousands of simultaneous conversations — validate exact target",
    "Stateless horizontal services + regional voice workers",
    "Per-tenant quotas, backpressure, HA, and disaster recovery",
    "Versioned tenant-scoped APIs + signed webhooks",
    "Future channels, mobile apps, and additional AI agents",
  ];
  scale.forEach((text, i) => bullet(c, text, 9.0, 2.76 + i * 0.53, 3.04, { h: 0.38, fontSize: 10.5, color: C.blue }));

  c.rect(1.18, 6.18, 6.85, 0.38, { fill: C.status, line: C.line, lineWidth: 0.6, radius: true });
  c.text("Provider adapters: models • embeddings • STT • TTS • telephony • messaging • email", 1.4, 6.29, 6.41, 0.15, { fontFace: F.mono, fontSize: 7.8, color: C.dim, align: "center" });

  addNotes(c, `
Technology and hosting

Favor managed, horizontally scalable services with explicit service boundaries. Use separate low-latency media workers for voice. Build versioned, tenant-scoped REST APIs and events with OAuth2/OIDC or service accounts, scoped permissions, schema validation, rate limits, idempotency keys, signed and replay-protected webhooks, audit, and observability.

Operational requirements include health/metrics/traces, tenant-aware alerting, on-call response, autoscaling, backups and restore tests, RTO/RPO/SLO agreements, release/rollback, data residency, and capacity/load tests.

The exact cloud, database topology, event bus, workflow engine, and provider set should be selected through discovery against region, compliance, latency, availability, team, and commercial requirements.
  `);
}

// -----------------------------------------------------------------------------
// 17 — Decisions, cost, maintenance, IP
// -----------------------------------------------------------------------------
{
  const c = newSlide("Discovery inputs", "Timeline and cost depend on decisions — not slideware", "Resolve scope, systems, controls, operations, and ownership before committing a price.", C.ink);

  const decisions = [
    ["LAUNCH SCOPE", "Journeys • intents • channels • brands • locations • outbound definition", C.blue],
    ["SYSTEMS OF RECORD", "First CRM • calendar • telephony • messaging • identity • sandbox access", C.mint],
    ["POLICY + COMPLIANCE", "Autonomy approvals • recording • outreach • retention • residency • accessibility", C.amber],
    ["NONFUNCTIONAL TARGETS", "Concurrency • latency • uptime • support • migration • RTO/RPO", C.blue],
    ["COMMERCIAL + IP", "Hosting • SLA • source ownership • Cybic pre-existing IP • bespoke client IP • model/data rights", C.mint],
  ];
  decisions.forEach(([head, body, color], i) => {
    const y = 1.93 + i * 0.79;
    c.rect(0.8, y, 7.42, 0.61, { fill: C.panel, line: color, lineWidth: 0.7, radius: true });
    c.text(head, 1.02, y + 0.17, 1.63, 0.18, { fontFace: F.mono, fontSize: 8, color, bold: true, charSpacing: 0.7 });
    c.text(body, 2.74, y + 0.1, 5.2, 0.36, { fontSize: 9.2, color: C.ink, valign: "mid", fit: "shrink" });
  });

  c.rect(8.52, 1.93, 3.78, 2.06, { fill: C.blueDark, line: C.blue, lineWidth: 1, radius: true });
  c.text("COST MODEL AFTER DISCOVERY", 8.78, 2.18, 3.26, 0.24, { fontFace: F.mono, fontSize: 9, color: C.blue, bold: true, align: "center", charSpacing: 0.7 });
  c.text("ONE-TIME\nproduct • integration • security • data/migration • testing\n\nRECURRING\ntelephony • messages • models/speech • compute/storage • observability • support", 8.84, 2.63, 3.14, 1.06, { fontSize: 9.8, color: C.ink, align: "center", fit: "shrink" });

  c.rect(8.52, 4.22, 3.78, 2.12, { fill: C.mintDark, line: C.mint, lineWidth: 1, radius: true });
  c.text("ONGOING MAINTENANCE", 8.78, 4.47, 3.26, 0.24, { fontFace: F.mono, fontSize: 9, color: C.mint, bold: true, align: "center", charSpacing: 0.7 });
  c.text("Connector/API upkeep\nKnowledge operations\nModel + prompt + eval regression\nSecurity patching + incident response\nObservability/on-call + backup/DR drills\nCost, latency, and capacity tuning", 8.84, 4.9, 3.14, 1.08, { fontSize: 10.2, color: C.ink, align: "center", fit: "shrink" });

  c.text("No responsible point estimate exists until volume, vendors, regions, compliance, migration, tenant count, SLA, and ownership terms are known.", 1.0, 6.55, 11.3, 0.28, { fontSize: 10.2, color: C.dim, italic: true, align: "center" });

  addNotes(c, `
Open decisions and commercial structure

Discovery should define launch scope, authoritative systems, identity matching/verification/merge rules, mandatory scheduling constraints, autonomous versus confirm/approve actions, regulatory obligations, technical SLOs, migration, KPI definitions, and ownership/licensing expectations.

Cost must separate one-time delivery from recurring usage and operations. Build sensitivities for conversation minutes, messages, model usage, recordings, retention, concurrency, locations, integrations, and support level.

Source/IP terms require legal review. A sensible discussion separates customer data and bespoke deliverables from Cybic's pre-existing/general IP and third-party/open-source/model license pass-through. Define repository, build/deploy documentation, credential handoff, model/data-use rights, and whether customer data may be used for training. Do not make an unqualified legal promise in the presentation.
  `);
}

// -----------------------------------------------------------------------------
// 18 — Close
// -----------------------------------------------------------------------------
{
  const c = newSlide("Recommended next step", "Prove the journey, then scale the platform", null, C.amber);

  const steps = [
    ["01", "WORKING VOICE\nINTELLIGENCE", "Demonstrates reusable\nconversation components", C.amber, C.amberDark],
    ["02", "CROSS-CHANNEL\nMVP", "Proves identity, continuity,\nand one governed outcome", C.blue, C.blueDark],
    ["03", "GOVERNED ENGAGEMENT\nPLATFORM", "Scales channels, workflows,\nintegrations, and intelligence", C.mint, C.mintDark],
  ];
  steps.forEach(([num, title, body, color, fill], i) => {
    const x = 0.83 + i * 4.16;
    c.rect(x, 2.05, 3.58, 2.28, { fill, line: color, lineWidth: 1.3, radius: true });
    c.text(num, x + 0.25, 2.3, 0.45, 0.2, { fontFace: F.mono, fontSize: 8, color, bold: true });
    c.text(title, x + 0.35, 2.82, 2.88, 0.62, { fontFace: F.mono, fontSize: 13, color, bold: true, align: "center", valign: "mid", charSpacing: 0.6 });
    c.text(body, x + 0.36, 3.62, 2.86, 0.46, { fontSize: 11, color: C.ink, align: "center" });
    if (i < 2) c.shape("chevron", x + 3.72, 3.0, 0.3, 0.32, { fill: C.line, line: C.line });
  });

  c.rect(1.2, 4.85, 10.9, 0.68, { fill: C.panel, line: C.amber, lineWidth: 1.2, radius: true });
  c.text("Approve a 3–4 week discovery to lock the MVP journey, acceptance criteria, integration choices, security/hosting design, delivery plan, ROM cost, and IP terms.", 1.54, 4.99, 10.22, 0.4, { fontSize: 11.5, color: C.ink, bold: true, align: "center", valign: "mid", fit: "shrink" });

  c.text("The demo proves reusable conversation intelligence.  The MVP proves continuity.  The platform turns conversations into governed business outcomes.", 1.08, 5.98, 11.15, 0.43, { fontSize: 14, color: C.mint, bold: true, align: "center" });
  c.text("Demonstrates Cybic's ability to move from conversational AI toward actionable business workflows rather than simply providing a voice chatbot.", 1.35, 6.48, 10.64, 0.32, { fontSize: 10, color: C.dim, italic: true, align: "center" });

  addNotes(c, `
Closing

The current demo validates reusable elements of speech capture, local transcription, request detection, short-session context, response generation, evaluation, and interaction records. It does not execute the requested business workflows today.

The proposed MVP adds persistent identity and conversation state, channel adapters, qualification, controlled knowledge, scheduling, CRM, SMS, handoff, administration, analytics, security, and operational foundations. The long-term platform extends channels, connectors, automation, optimization, multitenancy, and scale.

Recommended next step: a 3–4 week discovery and solution-definition engagement that produces a confirmed journey and launch scope, integration spike results, domain evaluation set, target architecture, security/hosting plan, delivery backlog, phased estimate, ROM cost, operational model, and source/IP assumptions.
  `);
}

// -----------------------------------------------------------------------------
// Appendix 19 — Capability status
// -----------------------------------------------------------------------------
{
  const c = newSlide("Appendix A", "Proposal capability status — evidence-safe view", "Status reflects the current repository reviewed in August 2026.", C.ink);

  const rows = [
    ["Inbound / outbound voice", "Desktop audio + optional local TTS", "REUSABLE", C.mint],
    ["Business intent understanding", "Information-request gate only", "REUSABLE", C.mint],
    ["Lead capture + qualification", "Unstructured transcript only", "MVP BUILD", C.blue],
    ["Context-aware responses", "Short current-session context", "CURRENT", C.amber],
    ["Managed knowledge / RAG", "Static Markdown context profiles", "MVP BUILD", C.blue],
    ["Appointment scheduling", "No implementation", "MVP BUILD", C.blue],
    ["Live-agent handoff", "Human remains present; no transfer", "MVP BUILD", C.blue],
    ["Transcript + history", "Local unidentified JSONL + replay", "CURRENT", C.amber],
    ["CRM / calendar / APIs", "No business-system connector", "MVP BUILD", C.blue],
    ["Web / SMS / email / social", "No channel adapters", "MVP + LATER", C.blue],
    ["Admin + workflow builder", "Local TUI/config only", "MVP + LATER", C.blue],
    ["Analytics + dashboards", "Technical telemetry only", "MVP + LATER", C.blue],
    ["Multitenancy + scale", "Multiple local processes ≠ scale", "LATER", C.dim],
  ];
  c.text("CAPABILITY", 0.8, 1.78, 3.1, 0.18, { fontFace: F.mono, fontSize: 7.5, color: C.dim, bold: true, charSpacing: 0.8 });
  c.text("CURRENT EVIDENCE", 3.95, 1.78, 5.26, 0.18, { fontFace: F.mono, fontSize: 7.5, color: C.dim, bold: true, charSpacing: 0.8 });
  c.text("STATUS", 10.25, 1.78, 1.75, 0.18, { fontFace: F.mono, fontSize: 7.5, color: C.dim, bold: true, charSpacing: 0.8, align: "center" });
  rows.forEach(([cap, evidence, status, color], i) => {
    const y = 2.08 + i * 0.35;
    c.rect(0.76, y, 11.57, 0.29, { fill: i % 2 ? C.status : C.panel, line: C.line, lineWidth: 0.25, radius: true });
    c.text(cap, 0.92, y + 0.07, 2.92, 0.12, { fontSize: 8.1, color: C.ink, bold: true });
    c.text(evidence, 3.95, y + 0.07, 5.3, 0.12, { fontSize: 8.1, color: C.dim });
    c.rect(10.22, y + 0.035, 1.76, 0.22, { fill: color === C.amber ? C.amberDark : color === C.mint ? C.mintDark : color === C.blue ? C.blueDark : C.panel, line: color, lineWidth: 0.6, radius: true });
    c.text(status, 10.31, y + 0.085, 1.58, 0.1, { fontFace: F.mono, fontSize: 6.5, color, bold: true, align: "center", charSpacing: 0.5 });
  });
  c.text("CURRENT = demonstrated locally  •  REUSABLE = relevant component, not end-to-end capability  •  MVP BUILD / LATER = new work", 0.98, 6.74, 11.3, 0.16, { fontFace: F.mono, fontSize: 7.3, color: C.dim, align: "center" });

  addNotes(c, `
Capability status notes

Inbound/outbound voice: the repository does not answer, originate, transfer, or control calls. Desktop audio capture and workstation TTS are reusable components only.

Intent: the current gate decides whether an utterance seeks information and rewrites it as a query. It does not classify business intent, extract lead fields, identify urgency, or select business actions.

Context: approximately six recent transcript lines and up to eight Q&A pairs during one running process. It is not customer-linked durable history.

Profiles: static Topic, Background, and Vocabulary Markdown. No ingestion, retrieval, permissions, citations, or admin publishing workflow.

Logs: local plaintext JSONL and replay. No customer ID, durable cross-channel store, enterprise access control, or retention enforcement.
  `);
}

// -----------------------------------------------------------------------------
// Appendix 20 — Functional catalog
// -----------------------------------------------------------------------------
{
  const c = newSlide("Appendix B", "Target-platform functional catalog", "A discovery checklist — not a claim of existing implementation.", C.blue);

  const panels = [
    [0.78, 1.87, 3.68, 2.12, "CHANNELS + ENGAGEMENT", "Website chat • inbound/outbound voice • SMS • email • WhatsApp • Messenger • mobile/future APIs\n\nVoice: booking changes • voicemail • SMS follow-up • live transfer\nWeb: qualification • CRM creation • uploads • confirmations", C.blue, C.blueDark],
    [4.82, 1.87, 3.68, 2.12, "CONVERSATION + KNOWLEDGE", "Natural dialogue • intent • objections • financing • need/urgency • ZIP validation\n\nProducts • services • FAQ • pricing • warranty • installation • scripts • SOP • internal docs • PDFs • website content", C.mint, C.mintDark],
    [8.86, 1.87, 3.47, 2.12, "SCHEDULING + ROUTING", "Google / Outlook / M365 / Exchange\n\nAvailability • duration • territory • ZIP • distance/travel • GPS • rep/tech • specialization • skills • workload • priority • emergency • approved performance factors", C.amber, C.amberDark],
    [0.78, 4.28, 3.68, 2.12, "CRM + INTEGRATIONS", "Dynamics • Salesforce • HubSpot • LeadPerfection • ServiceTitan • AccuLynx • JobNimbus • custom APIs\n\nCreate/update lead • notes • status • tasks • appointment • recording/transcript refs • workflow triggers", C.blue, C.blueDark],
    [4.82, 4.28, 3.68, 2.12, "HANDOFF + AUTOMATION", "Transcript • summary • customer • qualification • prior interactions • appointment • next action\n\nRouting • reminders • campaigns • reviews • no-show • re-engagement • internal notifications • manager alerts", C.mint, C.mintDark],
    [8.86, 4.28, 3.47, 2.12, "PLATFORM + SCALE", "Secure APIs/webhooks • auth • logging • admin/RBAC • reporting • templates • workflow builder\n\nCompanies • brands • locations • business units • thousands concurrent • additional agents/channels", C.amber, C.amberDark],
  ];
  panels.forEach(([x, y, w, h, title, body, color, fill]) => {
    c.rect(x, y, w, h, { fill, line: color, lineWidth: 0.9, radius: true });
    c.text(title, x + 0.22, y + 0.2, w - 0.44, 0.22, { fontFace: F.mono, fontSize: 8.5, color, bold: true, charSpacing: 0.6 });
    c.text(body, x + 0.22, y + 0.58, w - 0.44, h - 0.78, { fontSize: 9.5, color: C.ink, fit: "shrink" });
  });
  c.text("Google Business Messages, included in the source proposal, is discontinued; choose a supported replacement during discovery.", 1.05, 6.68, 11.2, 0.2, { fontSize: 9, color: C.dim, italic: true, align: "center" });

  addNotes(c, `
Functional catalog

This appendix preserves the breadth of the proposal while keeping the main narrative focused. It is a requirements/discovery checklist, not an estimate or implementation statement.

Live-agent transfer requires triggers, queue selection, accept/decline/fallback, AI pause/resume, and a complete context packet. The current human-in-the-loop desktop assist mode is not a bot-to-agent handoff.

APIs and webhooks require versioning, tenant scope, OAuth2/OIDC/service accounts, schema validation, rate limits, idempotency, signed and replay-protected delivery, audit, and observability.

Source proposal: docs/demo/AGENTIC-AI-PLATFORM-PROPOSAL.md.
  `);
}

// -----------------------------------------------------------------------------
// Appendix 21 — Sources and caveats
// -----------------------------------------------------------------------------
{
  const c = newSlide("Appendix C", "Sources, terminology, and accuracy boundaries", "Use these references to keep the external presentation evidence-based.", C.ink);

  const sources = [
    ["PRIMARY ALIGNMENT", "docs/demo/CUSTOMER-ENGAGEMENT-SOLUTION.md", "Current vs proposed scope, architecture, MVP, phases, controls, and open decisions"],
    ["SOURCE PROPOSAL", "docs/demo/AGENTIC-AI-PLATFORM-PROPOSAL.md", "Structured, content-complete normalization of the supplied proposal"],
    ["CURRENT DEMO", "docs/demo/CALLCENTER-DEMO.md", "Existing product narrative, pipeline, prototype measurements, and demo sequence"],
    ["TECHNICAL TRUTH", "README.md  •  docs/ARCHITECTURE.md  •  SPEC.md  •  current source", "Implemented behavior, data flow, operating constraints, and production gaps"],
    ["EXTERNAL CORRECTION", "Google Business Messages discontinuation notice", "Official release note; service ended 31 July 2024"],
  ];
  sources.forEach(([tag, file, detail], i) => {
    const y = 1.92 + i * 0.79;
    const color = i === 4 ? C.blue : i < 2 ? C.amber : C.mint;
    c.text(tag, 0.82, y + 0.06, 1.85, 0.18, { fontFace: F.mono, fontSize: 8, color, bold: true, charSpacing: 0.7 });
    c.text(file, 2.82, y, 4.35, 0.27, { fontFace: F.mono, fontSize: 7.8, color: C.ink, bold: true, valign: "mid", hyperlink: i === 4 ? { url: "https://developers.google.com/business-communications/business-messages/resources/release-notes/update-on-gbm?hl=en" } : undefined });
    c.text(detail, 7.38, y + 0.01, 4.76, 0.29, { fontSize: 9.6, color: C.dim, fit: "shrink" });
    c.line(0.82, y + 0.52, 11.42, 0, { color: C.line, width: 0.5 });
  });

  c.rect(0.82, 6.03, 5.53, 0.6, { fill: C.amberDark, line: C.amber, lineWidth: 0.8, radius: true });
  c.text("CURRENT = proven in this repository\nPROPOSED = recommended new platform work", 1.06, 6.17, 5.05, 0.31, { fontFace: F.mono, fontSize: 8.6, color: C.ink, bold: true, align: "center" });
  c.rect(6.74, 6.03, 5.53, 0.6, { fill: C.redDark, line: C.red, lineWidth: 0.8, radius: true });
  c.text("No mock screen, logo, or connector icon should imply\na deployed capability that the repository does not contain.", 6.98, 6.17, 5.05, 0.31, { fontSize: 9.1, color: C.ink, bold: true, align: "center" });

  addNotes(c, `
Accuracy rules

Use the customer-engagement alignment document as the narrative source of truth. The older HTML call-center deck remains the visual-style reference but contains stale wording and an outdated automated-test count.

Do not use private session log contents in the presentation. The fictional roaming scenario is intentionally synthetic.

No official Cybic logo asset exists in the repository; this deck therefore uses a text-only CYBIC wordmark rather than inventing a mark.

External source: Google Business Messages discontinuation announcement at https://developers.google.com/business-communications/business-messages/resources/release-notes/update-on-gbm?hl=en
  `);
}

function buildPreviewHtml() {
  const slidesHtml = previewSlides.map((c, index) => `
    <section class="slide-wrap">
      <div class="slide-label">${String(index + 1).padStart(2, "0")}</div>
      <div class="slide">${c.html.join("\n")}</div>
    </section>`).join("\n");
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Cybic deck proof</title>
<style>
  *{box-sizing:border-box} body{margin:0;background:#0d0d12;color:#fff;font-family:Arial,sans-serif}
  header{position:sticky;top:0;z-index:2;background:#0d0d12;padding:14px 28px;border-bottom:1px solid #34323e;color:#9a97a3}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(640px,1fr));gap:26px;padding:26px}
  .slide-wrap{position:relative;min-width:0}.slide-label{font:12px monospace;color:#9a97a3;margin-bottom:7px}
  .slide{position:relative;width:13.333in;height:7.5in;background:#16161d;overflow:hidden;transform-origin:top left;box-shadow:0 14px 34px rgba(0,0,0,.45)}
  @media(max-width:1100px){.grid{grid-template-columns:1fr}.slide{zoom:.65}}
</style></head><body><header>CYBIC deck proof • ${previewSlides.length} slides • same geometry as PowerPoint</header><main class="grid">${slidesHtml}</main></body></html>`;
}

async function main() {
  if (boundsErrors.length) {
    throw new Error(`Objects outside slide bounds:\n${boundsErrors.join("\n")}`);
  }
  fs.mkdirSync(path.dirname(OUTPUT), { recursive: true });
  await pptx.writeFile({ fileName: OUTPUT, compression: true });
  if (PREVIEW) {
    fs.mkdirSync(path.dirname(PREVIEW), { recursive: true });
    fs.writeFileSync(PREVIEW, buildPreviewHtml(), "utf8");
  }
  console.log(`Wrote ${OUTPUT}`);
  console.log(`Slides: ${previewSlides.length}`);
  if (PREVIEW) console.log(`Preview: ${PREVIEW}`);
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
