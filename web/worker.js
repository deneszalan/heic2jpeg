/*
 * Converts one HEIC file at a time to JPEG, off the main thread.
 * Files that are already JPEG (iPhones may convert photos while you pick them) are
 * accepted too.
 *
 * Message in:  {id, file: File, options: {quality, maxSize, maxPixels, keepMetadata, removeLocation}}
 * Message out: {id, ok: true, jpeg: ArrayBuffer, crc, width, height, limited, preview: Blob|null}
 *          or  {id, ok: false, error: "message for the user"}
 */
/* global libheif, HeifMeta, JpegMeta, Zip */
importScripts("vendor/libheif/libheif-bundle.js", "lib/heif-meta.js", "lib/jpeg-meta.js", "lib/zip.js");

const PREVIEW_SIZE = 96;
let heifReady = null;

function loadLibheif() {
  if (!heifReady) {
    heifReady = new Promise(function (resolve) {
      // The runtime may finish starting before libheif() returns, or later.
      let instance = null;
      let started = false;
      instance = libheif({
        onRuntimeInitialized: function () {
          started = true;
          if (instance) resolve(instance);
        },
      });
      if (started || instance.calledRun) resolve(instance);
    });
  }
  return heifReady;
}

class UserError extends Error {}

async function decode(lib, bytes) {
  const decoder = new lib.HeifDecoder();
  let images = [];
  try {
    images = decoder.decode(bytes);
    if (!images.length) throw new UserError("This is not a readable HEIC photo (the file may be damaged).");
    const image = images.find(function (i) { return i.is_primary(); }) || images[0];
    const width = image.get_width();
    const height = image.get_height();
    let pixels;
    try {
      pixels = new ImageData(width, height);
    } catch (error) {
      throw new UserError("This photo is too large for your browser to convert.");
    }
    const result = await new Promise(function (resolve) { image.display(pixels, resolve); });
    if (!result) throw new UserError("The photo could not be decoded (the file may be damaged).");
    return { pixels: pixels, hasAlpha: image.has_alpha_channel() };
  } finally {
    for (const image of images) image.free();
    if (decoder.decoder) {
      lib.heif_context_free(decoder.decoder);
      decoder.decoder = null;
    }
  }
}

function targetSize(width, height, maxSize, maxPixels) {
  const longest = Math.max(width, height);
  const scale = maxSize && longest > maxSize ? maxSize / longest : 1;
  let size = { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)), limited: false };
  // Phone browsers cap how big an image may be (about 16.7 megapixels on iPhone).
  if (maxPixels && size.width * size.height > maxPixels) {
    const fit = Math.sqrt(maxPixels / (width * height));
    size = { width: Math.max(1, Math.floor(width * fit)), height: Math.max(1, Math.floor(height * fit)), limited: true };
  }
  return size;
}

/** Area-average downscale in plain JavaScript, for images too big for the browser's canvas. */
function downscale(source, width, height) {
  const out = new ImageData(width, height);
  const src = source.data;
  const dst = out.data;
  const sw = source.width;
  const xScale = sw / width;
  const yScale = source.height / height;
  const x0 = new Uint32Array(width);
  const x1 = new Uint32Array(width);
  for (let x = 0; x < width; x++) {
    x0[x] = Math.floor(x * xScale);
    x1[x] = Math.max(x0[x] + 1, Math.min(sw, Math.floor((x + 1) * xScale)));
  }
  const sums = new Float64Array(width * 4);
  for (let y = 0; y < height; y++) {
    const yStart = Math.floor(y * yScale);
    const yEnd = Math.max(yStart + 1, Math.min(source.height, Math.floor((y + 1) * yScale)));
    sums.fill(0);
    for (let sy = yStart; sy < yEnd; sy++) {
      const row = sy * sw * 4;
      for (let x = 0; x < width; x++) {
        let r = 0, g = 0, b = 0, a = 0;
        for (let i = row + x0[x] * 4, end = row + x1[x] * 4; i < end; i += 4) {
          r += src[i]; g += src[i + 1]; b += src[i + 2]; a += src[i + 3];
        }
        sums[x * 4] += r; sums[x * 4 + 1] += g; sums[x * 4 + 2] += b; sums[x * 4 + 3] += a;
      }
    }
    for (let x = 0; x < width; x++) {
      const n = (yEnd - yStart) * (x1[x] - x0[x]);
      const o = (y * width + x) * 4;
      dst[o] = sums[x * 4] / n; dst[o + 1] = sums[x * 4 + 1] / n; dst[o + 2] = sums[x * 4 + 2] / n; dst[o + 3] = sums[x * 4 + 3] / n;
    }
  }
  return out;
}

async function toBitmap(pixels, size) {
  const options = { colorSpaceConversion: "none", premultiplyAlpha: "premultiply" };
  if (size.width !== pixels.width || size.height !== pixels.height) {
    options.resizeWidth = size.width;
    options.resizeHeight = size.height;
    options.resizeQuality = "high";
  }
  return createImageBitmap(pixels, options);
}

function drawOnWhite(bitmap, width, height) {
  let canvas;
  try {
    canvas = new OffscreenCanvas(width, height);
  } catch (error) {
    throw new UserError("This photo is too large for your browser to convert. Try a smaller photo size.");
  }
  const context = canvas.getContext("2d");
  if (!context) throw new UserError("This photo is too large for your browser to convert. Try a smaller photo size.");
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = "high";
  context.fillStyle = "#fff"; // JPEG has no transparency
  context.fillRect(0, 0, width, height);
  context.drawImage(bitmap, 0, 0, width, height);
  return canvas;
}

async function makePreview(bitmap) {
  const canvas = new OffscreenCanvas(PREVIEW_SIZE, PREVIEW_SIZE);
  const context = canvas.getContext("2d");
  context.imageSmoothingQuality = "high";
  const side = Math.min(bitmap.width, bitmap.height);
  context.fillStyle = "#fff";
  context.fillRect(0, 0, PREVIEW_SIZE, PREVIEW_SIZE);
  context.drawImage(
    bitmap,
    (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side,
    0, 0, PREVIEW_SIZE, PREVIEW_SIZE
  );
  return canvas.convertToBlob({ type: "image/jpeg", quality: 0.8 });
}

async function encode(bitmap, size, quality) {
  const canvas = drawOnWhite(bitmap, size.width, size.height);
  const blob = await canvas.convertToBlob({ type: "image/jpeg", quality: quality / 100 });
  return new Uint8Array(await blob.arrayBuffer());
}

function exifFor(source, size, options, resetOrientation) {
  if (!options.keepMetadata || !source) return null;
  return JpegMeta.prepareExif(source, {
    width: size && size.width,
    height: size && size.height,
    removeLocation: options.removeLocation,
    resetOrientation: resetOrientation,
  });
}

function fileKind(bytes) {
  if (bytes.length > 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return "jpeg";
  if (bytes.length > 12 && String.fromCharCode(bytes[4], bytes[5], bytes[6], bytes[7]) === "ftyp") return "heif";
  return null;
}

async function convertHeic(bytes, options) {
  const metadata = HeifMeta.extractHeifMetadata(bytes);
  const lib = await loadLibheif();
  const decoded = await decode(lib, bytes);
  let pixels = decoded.pixels;
  const size = targetSize(pixels.width, pixels.height, options.maxSize, options.maxPixels);
  if (options.maxPixels && pixels.width * pixels.height > options.maxPixels) {
    // Too big for the browser's canvas: shrink it in plain JavaScript first.
    pixels = downscale(pixels, size.width, size.height);
  }
  decoded.pixels = null;
  const bitmap = await toBitmap(pixels, size);
  pixels = null; // let the full-size pixels go before encoding
  const encoded = await encode(bitmap, size, options.quality);
  const preview = await makePreview(bitmap);
  bitmap.close();
  // The colour profile is always kept: iPhone photos use Display P3 and look dull without it.
  const jpeg = JpegMeta.rewriteJpegMetadata(encoded, { exif: exifFor(metadata.exif, size, options, true), icc: metadata.icc });
  return { jpeg: jpeg, size: size, preview: preview };
}

/** The photo is already a JPEG (iPhones may convert photos while you pick them). */
async function convertJpeg(bytes, options) {
  let metadata;
  try {
    metadata = JpegMeta.readJpegMetadata(bytes);
  } catch (error) {
    throw new UserError("This photo could not be read (the file may be damaged).");
  }
  const blob = new Blob([bytes], { type: "image/jpeg" });
  const keepXmp = options.keepMetadata && !options.removeLocation;
  const needed = targetSize(metadata.width, metadata.height, options.maxSize, options.maxPixels);

  if (needed.width === metadata.width && needed.height === metadata.height) {
    // Right size already: keep the image data untouched (no quality loss), only adjust its details.
    const jpeg = JpegMeta.rewriteJpegMetadata(bytes, {
      exif: exifFor(metadata.exif, null, options, false),
      icc: metadata.icc,
      keepXmp: keepXmp,
    });
    let preview = null;
    try {
      const bitmap = await createImageBitmap(blob);
      preview = await makePreview(bitmap);
      bitmap.close();
    } catch (error) { /* no preview is fine */ }
    return { jpeg: jpeg, size: needed, preview: preview };
  }

  let bitmap;
  try {
    // The browser applies the EXIF rotation while decoding.
    bitmap = await createImageBitmap(blob, { colorSpaceConversion: "none" });
  } catch (error) {
    throw new UserError("This photo could not be read (the file may be damaged).");
  }
  const size = targetSize(bitmap.width, bitmap.height, options.maxSize, options.maxPixels);
  const encoded = await encode(bitmap, size, options.quality);
  const preview = await makePreview(bitmap);
  bitmap.close();
  const jpeg = JpegMeta.rewriteJpegMetadata(encoded, { exif: exifFor(metadata.exif, size, options, true), icc: metadata.icc });
  return { jpeg: jpeg, size: size, preview: preview };
}

async function convert(file, options) {
  if (typeof OffscreenCanvas === "undefined") {
    throw new UserError("Your browser is too old for this converter. Please update it, or use the Windows app.");
  }
  const bytes = new Uint8Array(await file.arrayBuffer());
  const kind = fileKind(bytes);
  if (!kind) throw new UserError("This is not a readable HEIC photo (the file may be damaged).");
  const result = kind === "jpeg" ? await convertJpeg(bytes, options) : await convertHeic(bytes, options);
  return {
    jpeg: result.jpeg.buffer,
    crc: Zip.crc32(result.jpeg),
    width: result.size.width,
    height: result.size.height,
    limited: !!result.size.limited,
    preview: result.preview,
  };
}

self.onmessage = async function (event) {
  const { id, file, options } = event.data;
  try {
    const result = await convert(file, options);
    self.postMessage(Object.assign({ id: id, ok: true }, result), [result.jpeg]);
  } catch (error) {
    let message;
    if (error instanceof UserError) message = error.message;
    else if (error && error.name === "NotReadableError") message = "The file could not be read. It may have been moved or deleted.";
    else if (error instanceof RangeError || (error && /memory/i.test(String(error.message)))) message = "Not enough memory to convert this photo. Try a smaller photo size.";
    else message = "Unexpected error: " + (error && error.message ? error.message : String(error));
    self.postMessage({ id: id, ok: false, error: message });
  }
};
