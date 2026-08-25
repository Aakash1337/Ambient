#!/usr/bin/env node

/* Build a standalone, editable PowerPoint feature-overview slide for Ambient. */

const fs = require("fs");
const path = require("path");

const moduleRoot = process.env.PPTXGENJS_ROOT || "/tmp/cybic-pptx-node/node_modules";
const pptxgen = require(path.join(moduleRoot, "pptxgenjs"));

const ROOT = path.resolve(__dirname, "..");
const OUTPUT = process.argv[2]
  ? path.resolve(process.argv[2])
  : path.join(ROOT, "docs", "demo", "AMBIENT-FEATURE-OVERVIEW.pptx");
const PREVIEW = process.env.AMBIENT_SLIDE_PREVIEW
  ? path.resolve(process.env.AMBIENT_SLIDE_PREVIEW)
  : null;

const SW = 13.333;
const SH = 7.5;
const C = {
  white: "FFFFFF",
  paper: "F7F7FA",
  ink: "36363D",
  dim: "6E6D75",
  blue: "3E6DF5",
  cyan: "5DC6FF",
  purple: "7A42EF",
  magenta: "EF38F2",
  deep: "2416B8",
  navy: "17237A",
  line: "DADAE2",
  paleBlue: "EDF3FF",
  palePurple: "F5EEFF",
  paleCyan: "ECFAFF",
};

const pptx = new pptxgen();
pptx.defineLayout({ name: "AMBIENT_WIDE", width: SW, height: SH });
pptx.layout = "AMBIENT_WIDE";
pptx.author = "Cybic";
pptx.company = "Cybic";
pptx.subject = "Ambient current product capabilities";
pptx.title = "Ambient — Real-Time Voice Intelligence";
pptx.lang = "en-US";
pptx.theme = { headFontFace: "Arial", bodyFontFace: "Arial", lang: "en-US" };

const slide = pptx.addSlide();
slide.background = { color: C.paper };
const html = [];
const boundsErrors = [];

function esc(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function rgba(hex, transparency = 0) {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${1 - transparency / 100})`;
}

function assertBounds(kind, x, y, w, h) {
  const t = 0.005;
  if (x < -t || y < -t || x + w > SW + t || y + h > SH + t) {
    boundsErrors.push(`${kind}: x=${x}, y=${y}, w=${w}, h=${h}`);
  }
}

function rect(x, y, w, h, opts = {}) {
  assertBounds("rect", x, y, w, h);
  const fill = opts.fill || C.white;
  const line = opts.line || fill;
  const lineWidth = opts.lineWidth ?? 0;
  slide.addShape(opts.round ? pptx.ShapeType.roundRect : pptx.ShapeType.rect, {
    x, y, w, h,
    fill: { color: fill, transparency: opts.transparency || 0 },
    line: { color: line, width: lineWidth, transparency: opts.lineTransparency || 0 },
  });
  html.push(`<div style="position:absolute;left:${x}in;top:${y}in;width:${w}in;height:${h}in;background:${rgba(fill, opts.transparency || 0)};border:${lineWidth}pt solid #${line};border-radius:${opts.round ? 0.09 : 0}in;box-sizing:border-box"></div>`);
}

function ellipse(x, y, w, h, opts = {}) {
  assertBounds("ellipse", x, y, w, h);
  const fill = opts.fill || C.white;
  const line = opts.line || fill;
  const lineWidth = opts.lineWidth ?? 0;
  slide.addShape(pptx.ShapeType.ellipse, {
    x, y, w, h,
    fill: { color: fill, transparency: opts.transparency || 0 },
    line: { color: line, width: lineWidth },
  });
  html.push(`<div style="position:absolute;left:${x}in;top:${y}in;width:${w}in;height:${h}in;background:${rgba(fill, opts.transparency || 0)};border:${lineWidth}pt solid #${line};border-radius:50%;box-sizing:border-box"></div>`);
}

function line(x, y, w, h, opts = {}) {
  assertBounds("line", Math.min(x, x + w), Math.min(y, y + h), Math.abs(w), Math.abs(h));
  const color = opts.color || C.line;
  const width = opts.width || 1;
  slide.addShape(pptx.ShapeType.line, { x, y, w, h, line: { color, width } });
  const length = Math.sqrt(w * w + h * h);
  const angle = Math.atan2(h, w) * 180 / Math.PI;
  html.push(`<div style="position:absolute;left:${x}in;top:${y}in;width:${length}in;border-top:${width}pt solid #${color};transform:rotate(${angle}deg);transform-origin:0 0;box-sizing:border-box"></div>`);
}

function text(value, x, y, w, h, opts = {}) {
  assertBounds("text", x, y, w, h);
  const fontSize = opts.fontSize || 16;
  const color = opts.color || C.ink;
  const margin = opts.margin ?? 0;
  slide.addText(value, {
    x, y, w, h,
    fontFace: opts.fontFace || "Arial",
    fontSize,
    color,
    bold: opts.bold || false,
    italic: opts.italic || false,
    align: opts.align || "left",
    valign: opts.valign || "top",
    margin,
    fit: "shrink",
    charSpacing: opts.charSpacing,
    breakLine: false,
    isTextBox: true,
  });
  const alignItems = opts.valign === "mid" ? "center" : opts.valign === "bottom" ? "flex-end" : "flex-start";
  html.push(`<div style="position:absolute;left:${x}in;top:${y}in;width:${w}in;height:${h}in;display:flex;align-items:${alignItems};padding:${margin}in;box-sizing:border-box;overflow:hidden;white-space:pre-wrap;text-align:${opts.align || "left"};font-family:${opts.fontFace || "Arial"},sans-serif;font-size:${fontSize}pt;line-height:1.13;color:#${color};font-weight:${opts.bold ? 700 : 400};font-style:${opts.italic ? "italic" : "normal"};letter-spacing:${opts.charSpacing ? `${opts.charSpacing / 100}pt` : "normal"}">${esc(value).replaceAll("\n", "<br>")}</div>`);
}

function lerpHex(a, b, t) {
  const parse = (hex) => [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const av = parse(a);
  const bv = parse(b);
  return av.map((v, i) => Math.round(v + (bv[i] - v) * t).toString(16).padStart(2, "0")).join("").toUpperCase();
}

function gradientBand(x, y, w, h) {
  const stops = [C.blue, C.magenta, C.deep];
  const bands = 36;
  for (let i = 0; i < bands; i += 1) {
    const progress = i / (bands - 1);
    const segment = progress < 0.5 ? 0 : 1;
    const local = segment === 0 ? progress * 2 : (progress - 0.5) * 2;
    const color = lerpHex(stops[segment], stops[segment + 1], local);
    const bandY = y + (h * i) / bands;
    const bandHeight = Math.min(h / bands + 0.01, y + h - bandY);
    rect(x, bandY, w, bandHeight, { fill: color, line: color });
  }
}

function pill(label, x, y, w, color, fill) {
  rect(x, y, w, 0.32, { fill, line: color, lineWidth: 0.8, round: true });
  text(label.toUpperCase(), x + 0.06, y + 0.07, w - 0.12, 0.16, { fontSize: 7.2, color, bold: true, align: "center", charSpacing: 0.8 });
}

function featureCard(number, titleValue, body, x, y, h, color, fill) {
  rect(x, y, 7.45, h, { fill: C.white, line: C.line, lineWidth: 0.7, round: true });
  rect(x, y, 0.12, h, { fill: color, line: color, round: true });
  ellipse(x + 0.26, y + 0.25, 0.46, 0.46, { fill, line: color, lineWidth: 1.1 });
  text(number, x + 0.345, y + 0.37, 0.29, 0.15, { fontSize: 7.3, color, bold: true, align: "center", valign: "mid" });
  text(titleValue, x + 0.9, y + 0.2, 6.12, 0.26, { fontSize: 13, color, bold: true, charSpacing: 0.4 });
  text(body, x + 0.9, y + 0.56, 6.15, h - 0.7, { fontSize: 10.6, color: C.dim, valign: "mid" });
}

// Left gradient panel inspired by the supplied reference slide.
gradientBand(0, 0, 3.25, 7.5);
rect(0, 5.76, 3.25, 1.74, { fill: C.navy, line: C.navy, transparency: 16 });
text("Passive voice intelligence\nthat hears both sides, filters\nfor genuine information needs,\nand delivers contextual guidance\non screen or by voice.", 0.35, 1.55, 2.55, 2.55, { fontSize: 19, color: C.white, valign: "mid" });

// Microphone and waveform illustration.
rect(0.42, 6.1, 0.56, 0.88, { fill: C.cyan, line: "E89BFF", lineWidth: 2, round: true });
rect(0.54, 6.2, 0.32, 0.56, { fill: C.deep, line: C.deep, round: true });
line(0.42, 6.71, 0, 0.22, { color: C.cyan, width: 2 });
line(0.98, 6.71, 0, 0.22, { color: C.cyan, width: 2 });
line(0.42, 6.93, 0.56, 0, { color: C.cyan, width: 2 });
line(0.7, 6.94, 0, 0.25, { color: C.cyan, width: 2 });
line(0.5, 7.18, 0.4, 0, { color: C.cyan, width: 2 });
const waveHeights = [0.24, 0.42, 0.68, 0.93, 0.58, 0.34, 0.62, 0.86, 0.48, 0.25, 0.55, 0.76, 0.38, 0.2];
waveHeights.forEach((height, i) => {
  const x = 1.18 + i * 0.115;
  const y = 6.76 - height / 2;
  line(x, y, 0, height, { color: i % 2 ? "F58AF7" : C.cyan, width: 2.1 });
});

// Main content area.
text("AMBIENT", 4.03, 0.29, 5.35, 0.52, { fontSize: 30, color: C.ink, bold: true, charSpacing: 1.4 });
text("Real-time, context-aware voice intelligence for live conversations", 4.03, 0.9, 7.9, 0.36, { fontSize: 15.5, color: C.dim });
pill("Current product capabilities", 10.53, 0.38, 2.18, C.purple, C.palePurple);

featureCard(
  "01",
  "HEAR BOTH SIDES — RELIABLY",
  "Captures the microphone and every active system-audio endpoint through PipeWire or WASAPI. WebRTC noise suppression and automatic gain control improve microphone quality; live device meters, automatic endpoint handoff, and silence warnings expose capture problems instead of hiding them.",
  4.02, 1.55, 1.32, C.blue, C.paleBlue,
);

featureCard(
  "02",
  "UNDERSTAND SELECTIVELY — WITH CONTEXT",
  "Local Faster-Whisper transcription feeds fast heuristics plus a local semantic gate for direct questions, indirect information requests, and command-style asks. Fragment merging, per-channel policies, deduplication, echo/rehearsal suppression, short-session history, configurable profiles, and concurrent gating keep answers relevant.",
  4.02, 3.02, 1.48, C.purple, C.palePurple,
);

featureCard(
  "03",
  "ANSWER, SPEAK, VERIFY, AND REPLAY",
  "Streams concise cue cards to the terminal pane or local web console, with selective web lookup for changing facts and parallel answers for multiple questions. Optional local Kokoro/espeak voice adds Normal, Conversational, mute, repeat, and echo-control behavior. Audit, missed-question recovery, JSONL logs, badges, session replay, and a guarded emergency fallback support dependable demos and review.",
  4.02, 4.65, 1.66, C.magenta, C.paleCyan,
);

const chips = [
  ["WINDOWS + LINUX", 4.02, 1.35],
  ["TUI + WEB CONSOLE", 5.52, 1.56],
  ["CONCURRENT Q&A", 7.23, 1.34],
  ["CONTEXT PROFILES", 8.72, 1.42],
  ["VOICE MODES", 10.3, 1.13],
  ["AUDIT + REPLAY", 11.58, 1.15],
];
chips.forEach(([label, x, w], i) => pill(label, x, 6.57, w, i % 2 ? C.purple : C.blue, i % 2 ? C.palePurple : C.paleBlue));

text("Desktop agent-assist system  •  Voice playback is currently Linux-only  •  No telephony call control or media injection", 4.05, 7.05, 8.56, 0.17, { fontSize: 7.7, color: C.dim, italic: true, align: "center" });
text("CYBIC", 12.2, 7.27, 0.65, 0.12, { fontSize: 6.5, color: C.dim, bold: true, align: "right", charSpacing: 1.2 });

slide.addNotes(`
Ambient feature overview — current implementation only

Hear both sides: native PipeWire/WASAPI capture, microphone plus system audio, all-endpoint monitoring, automatic active-source selection, silence health warnings, device picker, WebRTC noise suppression and automatic gain control.

Understand selectively: local Faster-Whisper, heuristic fast path, local Ollama semantic gate, direct/indirect/command asks, per-channel policies, VAD-fragment merging, short-session transcript and Q&A context, profiles for Topic/Background/Vocabulary, dedupe, cross-channel echo suppression, rehearsal suppression, and concurrent gate/answer work.

Answer and voice: streaming cue/interview/terse answer styles, selective web lookup for changing facts, multiple answers in flight, local TUI and opt-in local web console, optional Linux Kokoro TTS with espeak fallback, Normal/Conversational delivery, mute, repeat/reuse of the last answer, cross-instance speaking lease, capture muting and answer-echo suppression.

Quality and operations: answer verification when enabled, missed-question sweep, force-answer, live gate decision reasons, JSONL logs with stage latencies, replay, single-pipeline process lock, guarded mode picker, and pinned emergency fallback.

Important boundary: this is a passive desktop agent-assist application. Local voice playback is not telephony integration, inbound/outbound call control, or full-duplex barge-in.
`);

function previewHtml() {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Ambient feature slide proof</title><style>
  *{box-sizing:border-box}body{margin:0;background:#111118;padding:24px;font-family:Arial,sans-serif}
  .slide{position:relative;width:13.333in;height:7.5in;background:#${C.paper};overflow:hidden;box-shadow:0 18px 46px rgba(0,0,0,.5)}
  </style></head><body><div class="slide">${html.join("\n")}</div></body></html>`;
}

async function main() {
  if (boundsErrors.length) throw new Error(`Objects outside slide bounds:\n${boundsErrors.join("\n")}`);
  fs.mkdirSync(path.dirname(OUTPUT), { recursive: true });
  await pptx.writeFile({ fileName: OUTPUT, compression: true });
  if (PREVIEW) {
    fs.mkdirSync(path.dirname(PREVIEW), { recursive: true });
    fs.writeFileSync(PREVIEW, previewHtml(), "utf8");
  }
  console.log(`Wrote ${OUTPUT}`);
  if (PREVIEW) console.log(`Preview: ${PREVIEW}`);
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
