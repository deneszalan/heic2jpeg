/*
 * Converts one HEIC file at a time to JPEG, off the main thread.
 *
 * Message in:  {id, file: File, options: {quality, maxSize, keepMetadata, removeLocation}}
 * Message out: {id, ok: true, jpeg: ArrayBuffer, crc, width, height, preview: Blob}
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

function targetSize(width, height, maxSize) {
  const longest = Math.max(width, height);
  if (!maxSize || longest <= maxSize) return { width: width, height: height };
  const scale = maxSize / longest;
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
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
    throw new UserError("This photo is too large for your browser to convert.");
  }
  const context = canvas.getContext("2d");
  if (!context) throw new UserError("This photo is too large for your browser to convert.");
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

async function convert(file, options) {
  if (typeof OffscreenCanvas === "undefined") {
    throw new UserError("Your browser is too old for this converter. Please update it, or use the Windows app.");
  }
  const bytes = new Uint8Array(await file.arrayBuffer());
  const metadata = HeifMeta.extractHeifMetadata(bytes);
  const lib = await loadLibheif();
  const decoded = await decode(lib, bytes);
  const size = targetSize(decoded.pixels.width, decoded.pixels.height, options.maxSize);

  const bitmap = await toBitmap(decoded.pixels, size);
  decoded.pixels = null; // let the full-size pixels go before encoding
  const canvas = drawOnWhite(bitmap, size.width, size.height);
  const preview = await makePreview(bitmap);
  bitmap.close();

  const blob = await canvas.convertToBlob({ type: "image/jpeg", quality: options.quality / 100 });
  let exif = null;
  if (options.keepMetadata && metadata.exif) {
    exif = JpegMeta.prepareExif(metadata.exif, {
      width: size.width,
      height: size.height,
      removeLocation: options.removeLocation,
    });
  }
  // The colour profile is always kept: iPhone photos use Display P3 and look dull without it.
  const jpeg = JpegMeta.addJpegMetadata(new Uint8Array(await blob.arrayBuffer()), { exif: exif, icc: metadata.icc });
  return { jpeg: jpeg.buffer, crc: Zip.crc32(jpeg), width: size.width, height: size.height, preview: preview };
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
