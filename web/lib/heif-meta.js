/*
 * Reads the EXIF block and the ICC colour profile out of a HEIC/HEIF file.
 *
 * HEIF files are ISO-BMFF "boxes". Inside the top-level `meta` box:
 *   - `iinf` lists the items (the image, its tiles, an `Exif` item, ...),
 *   - `iloc` says where each item's bytes are,
 *   - `iprp` holds properties such as `colr` (colour profile) and which item uses them,
 *   - `pitm` names the primary (main) image.
 */
(function (root) {
  "use strict";

  function fourcc(view, pos) {
    return String.fromCharCode(view.getUint8(pos), view.getUint8(pos + 1), view.getUint8(pos + 2), view.getUint8(pos + 3));
  }

  function readUint(view, pos, size) {
    switch (size) {
      case 0: return 0;
      case 1: return view.getUint8(pos);
      case 2: return view.getUint16(pos);
      case 4: return view.getUint32(pos);
      case 8: return Number(view.getBigUint64(pos));
      default: throw new Error("Unsupported field size " + size);
    }
  }

  /** Lists the boxes in [start, end): {type, start, end, body} where body is the payload start. */
  function boxes(view, start, end) {
    const list = [];
    let pos = start;
    while (pos + 8 <= end) {
      let size = view.getUint32(pos);
      const type = fourcc(view, pos + 4);
      let body = pos + 8;
      if (size === 1) {
        size = Number(view.getBigUint64(pos + 8));
        body = pos + 16;
      } else if (size === 0) {
        size = end - pos;
      }
      if (size < body - pos || pos + size > end) break; // damaged file: stop rather than misread
      list.push({ type: type, start: pos, end: pos + size, body: body });
      pos += size;
    }
    return list;
  }

  function child(list, type) {
    return list.find(function (b) { return b.type === type; }) || null;
  }

  function parseItemInfo(view, iinf) {
    const version = view.getUint8(iinf.body);
    const first = iinf.body + 4 + (version === 0 ? 2 : 4);
    const types = new Map();
    for (const infe of boxes(view, first, iinf.end)) {
      if (infe.type !== "infe") continue;
      const v = view.getUint8(infe.body);
      if (v < 2) continue;
      let pos = infe.body + 4;
      const id = v === 2 ? view.getUint16(pos) : view.getUint32(pos);
      pos += (v === 2 ? 2 : 4) + 2; // item_ID, item_protection_index
      types.set(id, fourcc(view, pos));
    }
    return types;
  }

  function parseItemLocations(view, iloc) {
    const version = view.getUint8(iloc.body);
    let pos = iloc.body + 4;
    const sizes = view.getUint16(pos);
    pos += 2;
    const offsetSize = sizes >> 12;
    const lengthSize = (sizes >> 8) & 15;
    const baseOffsetSize = (sizes >> 4) & 15;
    const indexSize = version === 1 || version === 2 ? sizes & 15 : 0;
    let count;
    if (version < 2) { count = view.getUint16(pos); pos += 2; } else { count = view.getUint32(pos); pos += 4; }

    const locations = new Map();
    for (let i = 0; i < count; i++) {
      let id;
      if (version < 2) { id = view.getUint16(pos); pos += 2; } else { id = view.getUint32(pos); pos += 4; }
      let method = 0;
      if (version === 1 || version === 2) { method = view.getUint16(pos) & 15; pos += 2; }
      pos += 2; // data_reference_index
      const baseOffset = readUint(view, pos, baseOffsetSize);
      pos += baseOffsetSize;
      const extentCount = view.getUint16(pos);
      pos += 2;
      const extents = [];
      for (let e = 0; e < extentCount; e++) {
        pos += indexSize;
        const offset = readUint(view, pos, offsetSize);
        pos += offsetSize;
        const length = readUint(view, pos, lengthSize);
        pos += lengthSize;
        extents.push({ offset: baseOffset + offset, length: length });
      }
      locations.set(id, { method: method, extents: extents });
    }
    return locations;
  }

  function itemBytes(bytes, location, idat) {
    const parts = [];
    let total = 0;
    for (const extent of location.extents) {
      let start;
      if (location.method === 0) start = extent.offset;
      else if (location.method === 1 && idat) start = idat.body + extent.offset;
      else return null; // item references other items: not used for metadata
      const length = extent.length || bytes.length - start;
      if (start < 0 || start + length > bytes.length) return null;
      parts.push(bytes.subarray(start, start + length));
      total += length;
    }
    if (parts.length === 1) return parts[0];
    const joined = new Uint8Array(total);
    let at = 0;
    for (const part of parts) { joined.set(part, at); at += part.length; }
    return joined;
  }

  function isTiffHeader(bytes, pos) {
    return (bytes[pos] === 0x49 && bytes[pos + 1] === 0x49 && bytes[pos + 2] === 0x2a && bytes[pos + 3] === 0x00) ||
      (bytes[pos] === 0x4d && bytes[pos + 1] === 0x4d && bytes[pos + 2] === 0x00 && bytes[pos + 3] === 0x2a);
  }

  /** The Exif item starts with a 4-byte offset to the TIFF header. Returns the TIFF bytes. */
  function tiffFromExifItem(data) {
    if (!data || data.length < 12) return null;
    const offset = new DataView(data.buffer, data.byteOffset, data.byteLength).getUint32(0);
    let start = 4 + offset;
    if (!isTiffHeader(data, start)) {
      // Some writers get the offset wrong; look for the header near the start.
      start = -1;
      for (let i = 4; i < Math.min(data.length - 4, 64); i++) {
        if (isTiffHeader(data, i)) { start = i; break; }
      }
      if (start < 0) return null;
    }
    return data.slice(start);
  }

  function parseProperties(view, iprp) {
    const inside = boxes(view, iprp.body, iprp.end);
    const ipco = child(inside, "ipco");
    const properties = ipco ? boxes(view, ipco.body, ipco.end) : [];
    const associations = new Map();
    for (const ipma of inside.filter(function (b) { return b.type === "ipma"; })) {
      const version = view.getUint8(ipma.body);
      const flags = view.getUint32(ipma.body) & 0xffffff;
      let pos = ipma.body + 4;
      const count = view.getUint32(pos);
      pos += 4;
      for (let i = 0; i < count; i++) {
        let id;
        if (version < 1) { id = view.getUint16(pos); pos += 2; } else { id = view.getUint32(pos); pos += 4; }
        const n = view.getUint8(pos);
        pos += 1;
        const indices = associations.get(id) || [];
        for (let a = 0; a < n; a++) {
          let index;
          if (flags & 1) { index = view.getUint16(pos) & 0x7fff; pos += 2; } else { index = view.getUint8(pos) & 0x7f; pos += 1; }
          if (index > 0) indices.push(index - 1); // 1-based; 0 means "none"
        }
        associations.set(id, indices);
      }
    }
    return { properties: properties, associations: associations };
  }

  function iccFromColr(bytes, view, colr) {
    if (colr.end - colr.body < 4) return null;
    const kind = fourcc(view, colr.body);
    if (kind !== "prof" && kind !== "rICC") return null;
    return bytes.slice(colr.body + 4, colr.end);
  }

  function findIcc(bytes, view, props, primaryId) {
    const fromPrimary = (props.associations.get(primaryId) || [])
      .map(function (i) { return props.properties[i]; })
      .filter(function (b) { return b && b.type === "colr"; });
    for (const colr of fromPrimary) {
      const icc = iccFromColr(bytes, view, colr);
      if (icc) return icc;
    }
    // Grid images (as iPhones write) may only attach the profile to their tiles.
    for (const colr of props.properties.filter(function (b) { return b.type === "colr"; })) {
      const icc = iccFromColr(bytes, view, colr);
      if (icc) return icc;
    }
    return null;
  }

  /**
   * Returns {exif: Uint8Array|null (TIFF data), icc: Uint8Array|null}.
   * Never throws: a file we cannot read metadata from is still converted, just without it.
   */
  function extractHeifMetadata(input) {
    const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
    const result = { exif: null, icc: null };
    try {
      const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
      const meta = child(boxes(view, 0, bytes.length), "meta");
      if (!meta) return result;
      const inner = boxes(view, meta.body + 4, meta.end);

      const pitm = child(inner, "pitm");
      let primaryId = null;
      if (pitm) primaryId = view.getUint8(pitm.body) === 0 ? view.getUint16(pitm.body + 4) : view.getUint32(pitm.body + 4);

      const iinf = child(inner, "iinf");
      const iloc = child(inner, "iloc");
      if (iinf && iloc) {
        const types = parseItemInfo(view, iinf);
        const locations = parseItemLocations(view, iloc);
        const idat = child(inner, "idat");
        for (const [id, type] of types) {
          if (type !== "Exif" || !locations.has(id)) continue;
          const tiff = tiffFromExifItem(itemBytes(bytes, locations.get(id), idat));
          if (tiff) { result.exif = tiff; break; }
        }
      }

      const iprp = child(inner, "iprp");
      if (iprp) result.icc = findIcc(bytes, view, parseProperties(view, iprp), primaryId);
    } catch (error) {
      // Damaged or unusual file: carry on without metadata.
    }
    return result;
  }

  const api = { extractHeifMetadata: extractHeifMetadata };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.HeifMeta = api;
})(typeof self !== "undefined" ? self : this);
