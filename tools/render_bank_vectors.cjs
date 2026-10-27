// Development-only SVG rasterization. Set SHARP_MODULE_PATH if sharp isn't local.
const sharp = require(process.env.SHARP_MODULE_PATH || "sharp");
const fs = require("node:fs/promises");
const path = require("node:path");
(async () => {
  const source = path.resolve("assets/banks/source");
  const output = path.resolve("assets/banks/raster");
  await fs.mkdir(output, { recursive: true });
  for (const file of await fs.readdir(source)) {
    if (!file.endsWith(".svg")) continue;
    const buffer = await sharp(path.join(source, file), { density: 1200 }).ensureAlpha().png().toBuffer();
    const logo = await sharp(buffer).trim({ background: "#00000000", threshold: 1 }).resize(440, 440, { fit: "inside" }).png().toBuffer();
    await sharp({ create: { width: 512, height: 512, channels: 4, background: "#00000000" } })
      .composite([{ input: logo, gravity: "centre" }]).png().toFile(path.join(output, file.replace(".svg", ".png")));
  }
})();
