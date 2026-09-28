#!/usr/bin/env node
/**
 * Generate a .pptx from a JSON deck spec using PptxGenJS.
 *
 * Usage:
 *   node generate_pptxgenjs.js deck.json output.pptx
 *
 * Minimal schema:
 * {
 *   "title": "...",
 *   "author": "...",
 *   "slides": [
 *     {
 *       "title": "...",
 *       "bullets": ["...", "..."],
 *       "notes": "...",
 *       "shapes": [
 *         { "type": "text", "x": 0.5, "y": 1, "w": 9, "h": 1, "text": "...", "fontSize": 14 },
 *         { "type": "image", "path": "chart.png", "x": 1, "y": 1, "w": 4, "h": 3 }
 *       ]
 *     }
 *   ]
 * }
 */

import fs from "node:fs";
import path from "node:path";

import PptxGenJS from "pptxgenjs";

if (process.argv.length !== 4) {
  console.error("Usage: generate_pptxgenjs.js <deck.json> <output.pptx>");
  process.exit(2);
}

const [, , inputPath, outputPath] = process.argv;
const spec = JSON.parse(fs.readFileSync(inputPath, "utf8"));

const pptx = new PptxGenJS();
if (spec.title) pptx.title = spec.title;
if (spec.author) pptx.author = spec.author;

for (const slideSpec of spec.slides || []) {
  const slide = pptx.addSlide();

  if (slideSpec.title) {
    slide.addText(slideSpec.title, {
      x: 0.5,
      y: 0.3,
      w: 9,
      h: 0.8,
      fontSize: 24,
      bold: true,
    });
  }

  if (slideSpec.bullets?.length) {
    const bulletText = slideSpec.bullets.map((bullet) => ({
      text: String(bullet),
      options: { bullet: true },
    }));
    slide.addText(bulletText, { x: 0.5, y: 1.3, w: 9, h: 5, fontSize: 14 });
  }

  for (const shape of slideSpec.shapes || []) {
    if (shape.type === "text") {
      const options = {
        x: shape.x,
        y: shape.y,
        w: shape.w,
        h: shape.h,
        fontSize: shape.fontSize || 12,
      };
      if (shape.bold) options.bold = true;
      if (shape.color) options.color = shape.color;
      if (shape.fill) options.fill = { color: shape.fill };
      slide.addText(String(shape.text || ""), options);
    } else if (shape.type === "image") {
      slide.addImage({
        path: shape.path,
        x: shape.x,
        y: shape.y,
        w: shape.w,
        h: shape.h,
      });
    }
  }

  if (slideSpec.notes) {
    slide.addNotes(String(slideSpec.notes));
  }
}

fs.mkdirSync(path.dirname(outputPath), { recursive: true });
await pptx.writeFile({ fileName: outputPath });
console.log(`OK: wrote ${outputPath}`);
