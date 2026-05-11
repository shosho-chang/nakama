// One-time icon generator. Creates solid accent-color (#c46a3d) PNGs at
// standard extension sizes using only built-in Node.js modules (no extra deps).
// Run: node scripts/gen-icons.mjs

import { createWriteStream } from "fs";
import { mkdir } from "fs/promises";
import { createDeflateRaw } from "zlib";
import { pipeline } from "stream/promises";
import { Readable } from "stream";

const SIZES = [16, 32, 48, 128];
const OUT_DIR = new URL("../public/icons/", import.meta.url).pathname;

// Accent color from popup.css
const R = 196;
const G = 106;
const B = 61;

function uint32BE(n) {
  const b = Buffer.allocUnsafe(4);
  b.writeUInt32BE(n >>> 0, 0);
  return b;
}

function makeCRCTable() {
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    let c = i;
    for (let j = 0; j < 8; j++) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[i] = c;
  }
  return table;
}

const CRC_TABLE = makeCRCTable();

function crc32(buf) {
  let crc = 0xffffffff;
  for (const byte of buf) {
    crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  }
  return uint32BE((crc ^ 0xffffffff) >>> 0);
}

function pngChunk(type, data) {
  const typeBytes = Buffer.from(type, "ascii");
  const len = uint32BE(data.length);
  const crcInput = Buffer.concat([typeBytes, data]);
  return Buffer.concat([len, typeBytes, data, crc32(crcInput)]);
}

async function deflateRaw(data) {
  const chunks = [];
  await pipeline(
    Readable.from([data]),
    createDeflateRaw({ level: 9 }),
    async function* (source) {
      for await (const chunk of source) {
        chunks.push(chunk);
      }
    },
  );
  return Buffer.concat(chunks);
}

async function makePNG(size) {
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

  // IHDR: width, height, bit-depth=8, color-type=2 (RGB), compression/filter/interlace=0
  const ihdrData = Buffer.concat([
    uint32BE(size),
    uint32BE(size),
    Buffer.from([8, 2, 0, 0, 0]),
  ]);
  const ihdr = pngChunk("IHDR", ihdrData);

  // Raw image data: each scanline = filter byte (0) + size*3 RGB bytes
  const scanline = Buffer.allocUnsafe(1 + size * 3);
  scanline[0] = 0;
  for (let i = 0; i < size; i++) {
    scanline[1 + i * 3] = R;
    scanline[2 + i * 3] = G;
    scanline[3 + i * 3] = B;
  }
  const raw = Buffer.concat(Array.from({ length: size }, () => scanline));
  const compressed = await deflateRaw(raw);
  const idat = pngChunk("IDAT", compressed);

  const iend = pngChunk("IEND", Buffer.alloc(0));

  return Buffer.concat([signature, ihdr, idat, iend]);
}

await mkdir(OUT_DIR, { recursive: true });

for (const size of SIZES) {
  const png = await makePNG(size);
  const outPath = `${OUT_DIR}icon-${size}.png`;
  await pipeline(Readable.from([png]), createWriteStream(outPath));
  console.log(`  wrote ${outPath} (${png.length} bytes)`);
}

console.log("done.");
