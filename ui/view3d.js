/* Local orbit viewer. No network, no CDN. Textures are the ones packed in the
   model. Small meshes, and any mesh whose WebGL frame comes back blank, are
   drawn on a 2D canvas so they stay visible without a GPU. */

const meshBytes = new Map();
const meshGeom = new Map();

function mountMeshViewers() {
  document.querySelectorAll("canvas[data-mesh-id]").forEach((canvas) => {
    const id = canvas.dataset.meshId;
    if (!id || canvas.dataset.bound === "1") return;
    canvas.dataset.bound = "1";
    loadMesh(canvas, id);
  });
}

async function loadMesh(canvas, id) {
  const note = canvas.parentElement ? canvas.parentElement.querySelector(".mesh-note") : null;
  try {
    let geom = meshGeom.get(id);
    if (!geom) {
      let bytes = meshBytes.get(id);
      if (!bytes) {
        bytes = await readMeshBytes(canvas.dataset.meshUrl, id);
        meshBytes.set(id, bytes);
        if (meshBytes.size > 2) meshBytes.delete(meshBytes.keys().next().value);
      }
      geom = parseGlb(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
      await decodeImages(geom);
      meshGeom.set(id, geom);
      if (meshGeom.size > 2) meshGeom.delete(meshGeom.keys().next().value);
    }
    if (!canvas.isConnected) return;
    showMesh(canvas, geom);
  } catch (error) {
    if (note) note.textContent = (error && error.message) || "Could not show the model.";
  }
}

async function readMeshBytes(url, id) {
  if (url) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        const buffer = await response.arrayBuffer();
        if (buffer.byteLength > 16 && new DataView(buffer).getUint32(0, true) === 0x46546c67) {
          return new Uint8Array(buffer);
        }
      }
    } catch (_error) {
      /* The custom scheme can refuse fetch. The bytes still come back over RPC. */
    }
  }
  const pieces = [];
  let offset = 0;
  let total = Infinity;
  while (offset < total) {
    const result = await rpc("character.mesh_bytes", { id, offset });
    const chunk = decodeMesh(result.glb);
    if (!chunk.length) break;
    pieces.push(chunk);
    total = Number(result.total || chunk.length);
    offset += chunk.length;
  }
  const out = new Uint8Array(pieces.reduce((sum, piece) => sum + piece.length, 0));
  let at = 0;
  for (const piece of pieces) {
    out.set(piece, at);
    at += piece.length;
  }
  return out;
}

function decodeMesh(b64) {
  const raw = atob(b64);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

function parseGlb(buffer) {
  const view = new DataView(buffer);
  if (buffer.byteLength < 20 || view.getUint32(0, true) !== 0x46546c67) {
    throw new Error("That file is not a GLB.");
  }
  let offset = 12;
  let document = null;
  let bin = null;
  while (offset + 8 <= view.byteLength) {
    const length = view.getUint32(offset, true);
    const kind = view.getUint32(offset + 4, true);
    offset += 8;
    if (kind === 0x4e4f534a) {
      const text = new TextDecoder().decode(new Uint8Array(buffer, offset, length));
      document = JSON.parse(text);
    } else if (kind === 0x004e4942) {
      bin = new Uint8Array(buffer, offset, length);
    }
    offset += length;
  }
  if (!document || !bin) throw new Error("That GLB has no mesh.");
  const worlds = nodeWorlds(document);
  const positions = [];
  const uvs = [];
  const indices = [];
  const ranges = [];
  const vecCache = new Map();
  const uvCache = new Map();
  const nodes = document.nodes || [];
  const meshNodes = [];
  nodes.forEach((node, index) => {
    if (node && node.mesh != null) meshNodes.push(index);
  });
  const instances = meshNodes.length ? meshNodes : [null];
  for (const nodeIndex of instances) {
    const meshes = nodeIndex == null ? document.meshes || [] : [((document.meshes || [])[nodes[nodeIndex].mesh])];
    const matrix = nodeIndex == null ? null : worlds[nodeIndex];
    for (const mesh of meshes) {
      if (!mesh) continue;
      for (const prim of mesh.primitives || []) {
        if (prim.mode != null && prim.mode !== 4) continue;
        const attr = prim.attributes || {};
        if (attr.POSITION == null) continue;
        let chunk = vecCache.get(attr.POSITION);
        if (!chunk) {
          chunk = readVec3(document, bin, attr.POSITION);
          vecCache.set(attr.POSITION, chunk);
        }
        if (matrix && !isIdentity(matrix)) chunk = chunk.map((point) => mulPoint(matrix, point));
        let uv = null;
        if (attr.TEXCOORD_0 != null) {
          uv = uvCache.get(attr.TEXCOORD_0);
          if (!uv) {
            uv = readVec2(document, bin, attr.TEXCOORD_0);
            uvCache.set(attr.TEXCOORD_0, uv);
          }
        }
        const material = materialView(document, prim.material);
        if (uv && material.transform) {
          const scale = material.transform.scale || [1, 1];
          const shift = material.transform.offset || [0, 0];
          uv = uv.map((coord) => [coord[0] * scale[0] + shift[0], coord[1] * scale[1] + shift[1]]);
        }
        const local = prim.indices == null ? chunk.map((_, index) => index) : readScalar(document, bin, prim.indices);
        const remap = new Map();
        const start = indices.length;
        for (const index of local) {
          if (index < 0 || index >= chunk.length) continue;
          let mapped = remap.get(index);
          if (mapped == null) {
            mapped = positions.length;
            remap.set(index, mapped);
            positions.push(chunk[index]);
            uvs.push(uv && uv[index] ? uv[index] : [0, 0]);
          }
          indices.push(mapped);
        }
        ranges.push({ start, count: indices.length - start, material: prim.material == null ? -1 : prim.material });
      }
    }
  }
  if (positions.length < 3 || indices.length < 3) {
    throw new Error("That model has no triangle mesh Koma can show.");
  }
  return {
    positions,
    uvs,
    indices,
    ranges,
    materials: (document.materials || []).map((_mat, index) => materialView(document, index)),
    images: readImages(document, bin),
  };
}

function materialView(document, index) {
  const mat = (document.materials || [])[index] || {};
  const pbr = mat.pbrMetallicRoughness || {};
  const ext = mat.extensions || {};
  const spec = ext.KHR_materials_pbrSpecularGlossiness || {};
  let tex = pbr.baseColorTexture || spec.diffuseTexture || null;
  let imageIndex = tex && tex.index != null ? tex.index : -1;
  if (imageIndex < 0) {
    const props = ((((document.extensions || {}).VRM || {}).materialProperties || [])[index]) || {};
    const main = (props.textureProperties || {})._MainTex;
    if (main != null) imageIndex = main;
  }
  const mtoon = ext.VRMC_materials_mtoon || {};
  if (imageIndex < 0 && mtoon.baseColorTexture && mtoon.baseColorTexture.index != null) {
    imageIndex = mtoon.baseColorTexture.index;
  }
  if (imageIndex >= 0) {
    const texture = (document.textures || [])[imageIndex];
    if (texture && texture.source != null) imageIndex = texture.source;
  }
  const factor = pbr.baseColorFactor || spec.diffuseFactor || [1, 1, 1, 1];
  return {
    color: [factor[0] == null ? 1 : factor[0], factor[1] == null ? 1 : factor[1], factor[2] == null ? 1 : factor[2]],
    alpha: mat.alphaMode || "OPAQUE",
    cutoff: mat.alphaCutoff == null ? 0.5 : mat.alphaCutoff,
    imageIndex,
    transform: tex && tex.extensions ? tex.extensions.KHR_texture_transform || null : null,
    sampler: null,
  };
}

function readImages(document, bin) {
  return (document.images || []).map((image) => {
    if (!image || image.bufferView == null) return null;
    const view = document.bufferViews[image.bufferView];
    if (!view) return null;
    const start = view.byteOffset || 0;
    const length = view.byteLength || 0;
    return { bytes: bin.slice(start, start + length), mime: image.mimeType || "image/png", element: null, sampler: null };
  });
}

function decodeImages(geom) {
  const jobs = (geom.images || []).map((image) => new Promise((resolve) => {
    if (!image || image.element || !image.bytes) {
      resolve();
      return;
    }
    const blob = new Blob([image.bytes], { type: image.mime || "image/png" });
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => {
      image.element = img;
      try { image.sampler = makeSampler(img); } catch (_error) { image.sampler = null; }
      URL.revokeObjectURL(url);
      resolve();
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      resolve();
    };
    img.src = url;
  }));
  return Promise.all(jobs).then(() => {
    for (const mat of geom.materials) {
      const image = geom.images[mat.imageIndex];
      mat.sampler = image && image.sampler ? image.sampler : null;
      mat.element = image && image.element ? image.element : null;
    }
  });
}

function makeSampler(img) {
  const copy = document.createElement("canvas");
  copy.width = img.width || 1;
  copy.height = img.height || 1;
  const ctx = copy.getContext("2d");
  ctx.drawImage(img, 0, 0);
  const data = ctx.getImageData(0, 0, copy.width, copy.height).data;
  const w = copy.width;
  const h = copy.height;
  return (u, v) => {
    const uu = ((u % 1) + 1) % 1;
    const vv = ((v % 1) + 1) % 1;
    const x = Math.min(w - 1, (uu * w) | 0);
    const y = Math.min(h - 1, ((1 - vv) * h) | 0);
    const i = (y * w + x) * 4;
    return [data[i], data[i + 1], data[i + 2], data[i + 3]];
  };
}

function readVec3(document, bin, index) {
  const accessor = document.accessors[index];
  const view = document.bufferViews[accessor.bufferView];
  if (!accessor || accessor.componentType !== 5126 || accessor.type !== "VEC3") {
    throw new Error("That model uses a position format Koma cannot show.");
  }
  const start = (view.byteOffset || 0) + (accessor.byteOffset || 0);
  const stride = view.byteStride || 12;
  const data = new DataView(bin.buffer, bin.byteOffset, bin.byteLength);
  const values = [];
  for (let i = 0; i < accessor.count; i += 1) {
    const at = start + i * stride;
    values.push([data.getFloat32(at, true), data.getFloat32(at + 4, true), data.getFloat32(at + 8, true)]);
  }
  return values;
}

function readVec2(document, bin, index) {
  const accessor = document.accessors[index];
  const view = accessor ? document.bufferViews[accessor.bufferView] : null;
  if (!accessor || !view || accessor.componentType !== 5126 || accessor.type !== "VEC2") return null;
  const start = (view.byteOffset || 0) + (accessor.byteOffset || 0);
  const stride = view.byteStride || 8;
  const data = new DataView(bin.buffer, bin.byteOffset, bin.byteLength);
  const values = [];
  for (let i = 0; i < accessor.count; i += 1) {
    const at = start + i * stride;
    values.push([data.getFloat32(at, true), data.getFloat32(at + 4, true)]);
  }
  return values;
}

function readScalar(document, bin, index) {
  const accessor = document.accessors[index];
  const view = document.bufferViews[accessor.bufferView];
  const width = { 5121: 1, 5123: 2, 5125: 4 }[accessor.componentType];
  if (!accessor || accessor.type !== "SCALAR" || !width) {
    throw new Error("That model uses an index format Koma cannot show.");
  }
  const start = (view.byteOffset || 0) + (accessor.byteOffset || 0);
  const stride = view.byteStride || width;
  const data = new DataView(bin.buffer, bin.byteOffset, bin.byteLength);
  const values = [];
  for (let i = 0; i < accessor.count; i += 1) {
    const at = start + i * stride;
    if (width === 1) values.push(data.getUint8(at));
    else if (width === 2) values.push(data.getUint16(at, true));
    else values.push(data.getUint32(at, true));
  }
  return values;
}

function nodeWorlds(document) {
  const nodes = document.nodes || [];
  const parentOf = new Map();
  nodes.forEach((node, index) => {
    for (const child of (node && node.children) || []) parentOf.set(child, index);
  });
  const cache = new Map();
  const walk = (index) => {
    if (cache.has(index)) return cache.get(index);
    const local = nodeMatrix(nodes[index] || {});
    const parent = parentOf.get(index);
    const world = parent == null ? local : mul(walk(parent), local);
    cache.set(index, world);
    return world;
  };
  for (let index = 0; index < nodes.length; index += 1) walk(index);
  return cache;
}

function nodeMatrix(node) {
  if (node.matrix && node.matrix.length === 16) return node.matrix;
  const t = node.translation || [0, 0, 0];
  const r = node.rotation || [0, 0, 0, 1];
  const s = node.scale || [1, 1, 1];
  const x = r[0];
  const y = r[1];
  const z = r[2];
  const w = r[3];
  return [
    (1 - 2 * y * y - 2 * z * z) * s[0], (2 * x * y + 2 * z * w) * s[0], (2 * x * z - 2 * y * w) * s[0], 0,
    (2 * x * y - 2 * z * w) * s[1], (1 - 2 * x * x - 2 * z * z) * s[1], (2 * y * z + 2 * x * w) * s[1], 0,
    (2 * x * z + 2 * y * w) * s[2], (2 * y * z - 2 * x * w) * s[2], (1 - 2 * x * x - 2 * y * y) * s[2], 0,
    t[0], t[1], t[2], 1,
  ];
}

function isIdentity(matrix) {
  const identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  for (let i = 0; i < 16; i += 1) if (Math.abs(matrix[i] - identity[i]) > 1e-5) return false;
  return true;
}

function mulPoint(matrix, point) {
  return [
    matrix[0] * point[0] + matrix[4] * point[1] + matrix[8] * point[2] + matrix[12],
    matrix[1] * point[0] + matrix[5] * point[1] + matrix[9] * point[2] + matrix[13],
    matrix[2] * point[0] + matrix[6] * point[1] + matrix[10] * point[2] + matrix[14],
  ];
}

const meshCamera = window.meshCamera = {
  meshId: "",
  yaw: 0.7,
  pitch: 0.42,
  roll: 0,
  dist: 3.2,
  baseDist: 3.2,
  panX: 0,
  panY: 0,
  auto: false,
  wire: false,
  light: true,
  large: false,
};
let meshSpin = 0;

function resetMeshCamera() {
  meshCamera.yaw = 0.7;
  meshCamera.pitch = 0.42;
  meshCamera.roll = 0;
  meshCamera.dist = meshCamera.baseDist || 3.2;
  meshCamera.panX = 0;
  meshCamera.panY = 0;
}

function meshFlags() {
  return { auto: meshCamera.auto, wire: meshCamera.wire, light: meshCamera.light, large: meshCamera.large };
}

function showMesh(canvas, geom) {
  const id = canvas.dataset.meshId || "";
  if (!geom.centered) {
    centerGeometry(geom);
    geom.centered = true;
  }
  const span = Math.max(geom.half[0], geom.half[1], 0.05);
  const textured = geom.materials.some((mat) => mat.element);
  const triangles = geom.indices.length / 3;
  const gl = !geom.preferCanvas && (textured || triangles > 12000)
    ? canvas.getContext("webgl2", { preserveDrawingBuffer: true, antialias: true })
      || canvas.getContext("webgl", { preserveDrawingBuffer: true, antialias: true })
    : null;
  const useGl = Boolean(gl && glProgram(gl));
  if (meshCamera.meshId !== id) {
    meshCamera.meshId = id;
    meshCamera.baseDist = useGl ? Math.max(1.2, span / Math.tan(0.35) / 0.72) : Math.max(1.2, span * 2.15);
    resetMeshCamera();
  }
  canvas._geom = geom;
  canvas._orbit = meshCamera;
  bindOrbit(canvas);
  ensureSpin();
  if (useGl) {
    canvas._gl = gl;
    canvas.dataset.mode = "webgl";
    requestAnimationFrame(() => {
      redraw(canvas);
      if (webglBlank(canvas) && canvas.getContext("2d")) {
        geom.preferCanvas = true;
        canvas._gl = null;
        canvas.dataset.mode = "canvas";
        meshCamera.baseDist = Math.max(1.2, span * 2.15);
        meshCamera.dist = meshCamera.baseDist;
        redraw(canvas);
      }
    });
    return;
  }
  canvas._gl = null;
  canvas.dataset.mode = "canvas";
  requestAnimationFrame(() => redraw(canvas));
}

function centerGeometry(geom) {
  let minX = Infinity;
  let minY = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  let maxZ = -Infinity;
  for (const point of geom.positions) {
    minX = Math.min(minX, point[0]);
    minY = Math.min(minY, point[1]);
    minZ = Math.min(minZ, point[2]);
    maxX = Math.max(maxX, point[0]);
    maxY = Math.max(maxY, point[1]);
    maxZ = Math.max(maxZ, point[2]);
  }
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  const cz = (minZ + maxZ) / 2;
  geom.positions = geom.positions.map((point) => [point[0] - cx, point[1] - cy, point[2] - cz]);
  geom.half = [(maxX - minX) / 2, (maxY - minY) / 2, (maxZ - minZ) / 2];
}

function frameRadius(geom) {
  const half = geom.half || [1, 1, 1];
  return Math.max(0.001, Math.hypot(half[0], half[1], half[2]));
}

function bindOrbit(canvas) {
  canvas.addEventListener("contextmenu", (event) => event.preventDefault());
  canvas.addEventListener("pointerdown", (event) => {
    const mode = event.button === 2 || event.shiftKey ? "pan" : (event.button === 1 || event.altKey ? "roll" : "orbit");
    canvas._drag = {
      x: event.clientX,
      y: event.clientY,
      mode,
      yaw: meshCamera.yaw,
      pitch: meshCamera.pitch,
      roll: meshCamera.roll,
      panX: meshCamera.panX,
      panY: meshCamera.panY,
    };
    try { canvas.setPointerCapture(event.pointerId); } catch (_error) { /* already gone */ }
  });
  canvas.addEventListener("pointermove", (event) => {
    const drag = canvas._drag;
    if (!drag) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (drag.mode === "pan") {
      meshCamera.panX = drag.panX + dx;
      meshCamera.panY = drag.panY + dy;
    } else if (drag.mode === "roll") {
      meshCamera.roll = drag.roll + dx * 0.01;
    } else {
      meshCamera.yaw = drag.yaw + dx * 0.01;
      meshCamera.pitch = drag.pitch + dy * 0.01;
    }
    redraw(canvas);
  });
  const end = () => { canvas._drag = null; };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const factor = event.deltaY > 0 ? 1.08 : 0.92;
    const near = Math.max(0.2, meshCamera.baseDist * 0.12);
    const far = Math.max(near + 0.5, meshCamera.baseDist * 10);
    meshCamera.dist = Math.max(near, Math.min(far, meshCamera.dist * factor));
    redraw(canvas);
  }, { passive: false });
}

function ensureSpin() {
  if (meshSpin) return;
  const tick = () => {
    meshSpin = requestAnimationFrame(tick);
    if (!meshCamera.auto) return;
    const canvas = document.querySelector("canvas[data-mesh-id]");
    if (!canvas || canvas._drag || !canvas._geom) return;
    meshCamera.yaw += 0.012;
    redraw(canvas);
  };
  meshSpin = requestAnimationFrame(tick);
}

function redraw(canvas) {
  if (!canvas.isConnected || !canvas._geom) return;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(320, Math.floor(rect.width || 640));
  const height = Math.max(240, Math.floor(rect.height || 340));
  const resized = canvas.width !== width || canvas.height !== height;
  if (resized) {
    canvas.width = width;
    canvas.height = height;
    if (canvas._gl) glProgram(canvas._gl);
  }
  if (canvas._gl && canvas._gl._program) drawGl(canvas);
  else drawCanvas(canvas);
  const pixel = sampleCenter(canvas);
  canvas.dataset.pixel = pixel.join(",");
  canvas.dataset.ready = "1";
}

function sampleCenter(canvas) {
  if (canvas._gl) {
    const gl = canvas._gl;
    const pixel = new Uint8Array(4);
    gl.readPixels(Math.floor(canvas.width / 2), Math.floor(canvas.height / 2), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
    return [pixel[0], pixel[1], pixel[2]];
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) return [0, 0, 0];
  const pixel = ctx.getImageData(Math.floor(canvas.width / 2), Math.floor(canvas.height / 2), 1, 1).data;
  return [pixel[0], pixel[1], pixel[2]];
}

function webglBlank(canvas) {
  const gl = canvas._gl;
  if (!gl) return false;
  const pixel = new Uint8Array(4);
  let colored = 0;
  const w = canvas.width;
  const h = canvas.height;
  for (let y = 0.28; y <= 0.72; y += 0.11) {
    for (let x = 0.32; x <= 0.68; x += 0.12) {
      gl.readPixels((w * x) | 0, (h * y) | 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
      const gray = Math.abs(pixel[0] - pixel[1]) < 6 && Math.abs(pixel[1] - pixel[2]) < 6;
      const clear = gray && pixel[0] > 34 && pixel[0] < 52;
      if (!clear) colored += 1;
    }
  }
  return colored < 2;
}

function faceLight(a, b, c, orbit) {
  const ux = b[0] - a[0];
  const uy = b[1] - a[1];
  const uz = b[2] - a[2];
  const vx = c[0] - a[0];
  const vy = c[1] - a[1];
  const vz = c[2] - a[2];
  const normal = rotatePoint([uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx], orbit);
  const length = Math.hypot(normal[0], normal[1], normal[2]) || 1;
  const facing = Math.max(0, normal[2] / length);
  return 0.58 + 0.42 * facing;
}

function rotatePoint(point, orbit) {
  const cy = Math.cos(orbit.yaw);
  const sy = Math.sin(orbit.yaw);
  const x1 = point[0] * cy + point[2] * sy;
  const z1 = -point[0] * sy + point[2] * cy;
  const cp = Math.cos(orbit.pitch);
  const sp = Math.sin(orbit.pitch);
  const y2 = point[1] * cp - z1 * sp;
  const z2 = point[1] * sp + z1 * cp;
  const cr = Math.cos(orbit.roll || 0);
  const sr = Math.sin(orbit.roll || 0);
  return [x1 * cr - y2 * sr, x1 * sr + y2 * cr, z2];
}

function projectPoint(point, orbit, width, height) {
  const rotated = rotatePoint(point, orbit);
  const depth = rotated[2] + orbit.dist;
  const scale = (Math.min(width, height) * 0.72) / Math.max(0.2, depth);
  return {
    x: width / 2 + rotated[0] * scale + (orbit.panX || 0),
    y: height / 2 - rotated[1] * scale + (orbit.panY || 0),
    z: rotated[2],
  };
}

function materialAt(geom, index) {
  return geom.materials[index] || { color: [1, 0.28, 0.22], alpha: "OPAQUE", cutoff: 0.5, sampler: null };
}

function drawCanvas(canvas) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const geom = canvas._geom;
  const orbit = canvas._orbit;
  ctx.fillStyle = "#2a2a2c";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const projected = geom.positions.map((point) => projectPoint(point, orbit, canvas.width, canvas.height));
  const stride = Math.max(1, Math.ceil(geom.indices.length / 3 / 7000));
  const faces = [];
  for (const range of geom.ranges) {
    const mat = materialAt(geom, range.material);
    for (let i = range.start; i < range.start + range.count; i += 3 * stride) {
      const ia = geom.indices[i];
      const ib = geom.indices[i + 1];
      const ic = geom.indices[i + 2];
      const a = projected[ia];
      const b = projected[ib];
      const c = projected[ic];
      const pa = geom.positions[ia];
      const pb = geom.positions[ib];
      const pc = geom.positions[ic];
      if (!a || !b || !c || !pa || !pb || !pc) continue;
      let red = mat.color[0];
      let green = mat.color[1];
      let blue = mat.color[2];
      let alpha = 1;
      if (mat.sampler) {
        const ua = geom.uvs[ia] || [0, 0];
        const ub = geom.uvs[ib] || [0, 0];
        const uc = geom.uvs[ic] || [0, 0];
        const sample = mat.sampler((ua[0] + ub[0] + uc[0]) / 3, (ua[1] + ub[1] + uc[1]) / 3);
        alpha = sample[3] / 255;
        if (mat.alpha === "MASK" && alpha < mat.cutoff) continue;
        if (mat.alpha === "BLEND" && alpha < 0.08) continue;
        red *= sample[0] / 255;
        green *= sample[1] / 255;
        blue *= sample[2] / 255;
      }
      faces.push({
        a, b, c,
        z: (a.z + b.z + c.z) / 3,
        light: faceLight(pa, pb, pc, orbit),
        red, green, blue,
        alpha: mat.alpha === "BLEND" ? alpha : 1,
      });
    }
  }
  faces.sort((left, right) => left.z - right.z);
  const lit = meshCamera.light;
  const wire = meshCamera.wire;
  for (const face of faces) {
    ctx.beginPath();
    ctx.moveTo(face.a.x, face.a.y);
    ctx.lineTo(face.b.x, face.b.y);
    ctx.lineTo(face.c.x, face.c.y);
    ctx.closePath();
    if (!wire || lit) {
      const shade = lit ? face.light : 1;
      ctx.globalAlpha = face.alpha;
      ctx.fillStyle = `rgb(${Math.round(face.red * shade * 255)}, ${Math.round(face.green * shade * 255)}, ${Math.round(face.blue * shade * 255)})`;
      ctx.fill();
      ctx.globalAlpha = 1;
    }
    if (wire) {
      ctx.strokeStyle = lit ? "rgba(255,255,255,0.9)" : "#f2f2f7";
      ctx.lineWidth = 1.25;
      ctx.stroke();
    }
  }
}

function glProgram(gl) {
  const vs = `
    attribute vec3 aPos;
    attribute vec3 aNormal;
    attribute vec2 aUv;
    uniform mat4 uMvp;
    uniform mat4 uModel;
    varying vec3 vNormal;
    varying vec2 vUv;
    void main() {
      vNormal = mat3(uModel) * aNormal;
      vUv = aUv;
      gl_Position = uMvp * vec4(aPos, 1.0);
    }
  `;
  const fs = `
    precision mediump float;
    uniform float uLight;
    uniform float uTex;
    uniform float uLine;
    uniform float uAlpha;
    uniform float uCutoff;
    uniform vec3 uColor;
    uniform sampler2D uMap;
    varying vec3 vNormal;
    varying vec2 vUv;
    void main() {
      if (uLine > 0.5) {
        gl_FragColor = vec4(1.0, 1.0, 1.0, 1.0);
        return;
      }
      vec4 base = vec4(uColor, 1.0);
      if (uTex > 0.5) base *= texture2D(uMap, vUv);
      if (uAlpha > 1.5) {
        if (base.a < 0.04) discard;
      } else if (uAlpha > 0.5) {
        if (base.a < uCutoff) discard;
        base.a = 1.0;
      }
      float shade = uLight > 0.5 ? clamp(abs(vNormal.z) * 0.4 + 0.62, 0.62, 1.0) : 1.0;
      gl_FragColor = vec4(base.rgb * shade, base.a);
    }
  `;
  const program = link(gl, vs, fs);
  if (!program) return false;
  gl._program = program;
  gl._pos = gl.getAttribLocation(program, "aPos");
  gl._normal = gl.getAttribLocation(program, "aNormal");
  gl._uv = gl.getAttribLocation(program, "aUv");
  gl._mvp = gl.getUniformLocation(program, "uMvp");
  gl._model = gl.getUniformLocation(program, "uModel");
  gl._light = gl.getUniformLocation(program, "uLight");
  gl._tex = gl.getUniformLocation(program, "uTex");
  gl._line = gl.getUniformLocation(program, "uLine");
  gl._alpha = gl.getUniformLocation(program, "uAlpha");
  gl._cutoff = gl.getUniformLocation(program, "uCutoff");
  gl._color = gl.getUniformLocation(program, "uColor");
  gl._map = gl.getUniformLocation(program, "uMap");
  return true;
}

function link(gl, vsSource, fsSource) {
  const vs = gl.createShader(gl.VERTEX_SHADER);
  gl.shaderSource(vs, vsSource);
  gl.compileShader(vs);
  const fs = gl.createShader(gl.FRAGMENT_SHADER);
  gl.shaderSource(fs, fsSource);
  gl.compileShader(fs);
  const program = gl.createProgram();
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return null;
  return program;
}

function drawGl(canvas) {
  const gl = canvas._gl;
  const geom = canvas._geom;
  gl.viewport(0, 0, canvas.width, canvas.height);
  gl.clearColor(0.165, 0.165, 0.173, 1);
  gl.enable(gl.DEPTH_TEST);
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  ensureGlBuffers(gl, geom);
  const orbit = canvas._orbit;
  const model = modelMatrix(orbit);
  const span = Math.min(canvas.width, canvas.height) * 0.72 || 1;
  const panX = (orbit.panX || 0) * orbit.dist / span;
  const panY = -((orbit.panY || 0) * orbit.dist / span);
  const view = mul(translate(panX, panY, 0), lookAt([0, 0, orbit.dist], [0, 0, 0], [0, 1, 0]));
  const projection = perspective(0.7, canvas.width / canvas.height, 0.05, Math.max(80, orbit.dist * 20));
  gl.useProgram(gl._program);
  gl.uniformMatrix4fv(gl._model, false, new Float32Array(model));
  gl.uniformMatrix4fv(gl._mvp, false, new Float32Array(mul(projection, mul(view, model))));
  gl.uniform1i(gl._map, 0);
  const opaque = [];
  const blended = [];
  for (const range of geom.ranges) {
    const mat = materialAt(geom, range.material);
    if (mat.alpha === "BLEND") blended.push(range);
    else opaque.push(range);
  }
  gl.depthMask(true);
  if (!meshCamera.wire || meshCamera.light) {
    for (const range of opaque) drawRange(gl, geom, range, false);
    gl.depthMask(false);
    for (const range of blended) drawRange(gl, geom, range, false);
    gl.depthMask(true);
  }
  if (meshCamera.wire) {
    gl.uniform1f(gl._line, 1);
    gl.uniform1f(gl._tex, 0);
    for (const range of geom.ranges) {
      if (!range.lines) continue;
      gl.bindBuffer(gl.ARRAY_BUFFER, range.lines);
      gl.enableVertexAttribArray(gl._pos);
      gl.vertexAttribPointer(gl._pos, 3, gl.FLOAT, false, 12, 0);
      if (gl._normal >= 0) gl.disableVertexAttribArray(gl._normal);
      if (gl._uv >= 0) gl.disableVertexAttribArray(gl._uv);
      gl.drawArrays(gl.LINES, 0, range.lineCount);
    }
    gl.uniform1f(gl._line, 0);
  }
}

function drawRange(gl, geom, range, _line) {
  if (!range.buffer) return;
  const mat = materialAt(geom, range.material);
  gl.uniform1f(gl._line, 0);
  gl.uniform1f(gl._light, meshCamera.light ? 1 : 0);
  gl.uniform3f(gl._color, mat.color[0], mat.color[1], mat.color[2]);
  gl.uniform1f(gl._alpha, mat.alpha === "BLEND" ? 2 : (mat.alpha === "MASK" ? 1 : 0));
  gl.uniform1f(gl._cutoff, mat.cutoff == null ? 0.5 : mat.cutoff);
  const texture = mat.element ? textureFor(gl, mat) : null;
  gl.uniform1f(gl._tex, texture ? 1 : 0);
  if (texture) {
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, texture);
  }
  gl.bindBuffer(gl.ARRAY_BUFFER, range.buffer);
  gl.enableVertexAttribArray(gl._pos);
  gl.vertexAttribPointer(gl._pos, 3, gl.FLOAT, false, 32, 0);
  if (gl._normal >= 0) {
    gl.enableVertexAttribArray(gl._normal);
    gl.vertexAttribPointer(gl._normal, 3, gl.FLOAT, false, 32, 12);
  }
  if (gl._uv >= 0) {
    gl.enableVertexAttribArray(gl._uv);
    gl.vertexAttribPointer(gl._uv, 2, gl.FLOAT, false, 32, 24);
  }
  gl.drawArrays(gl.TRIANGLES, 0, range.count);
}

function textureFor(gl, mat) {
  if (!mat.element) return null;
  if (!mat._textures) mat._textures = new Map();
  const cached = mat._textures.get(gl);
  if (cached) return cached;
  const tex = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, mat.element);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  const wrap = gl.REPEAT;
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, wrap);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, wrap);
  mat._textures.set(gl, tex);
  return tex;
}

function ensureGlBuffers(gl, geom) {
  if (geom._glBuilt === gl) return;
  for (const range of geom.ranges) {
    const count = range.count;
    const interleaved = new Float32Array(count * 8);
    const step = Math.max(1, Math.floor(count / 3 / 4000));
    const lineVerts = Math.ceil(count / 3 / step) * 6;
    const lines = new Float32Array(lineVerts * 3);
    let lineAt = 0;
    for (let i = 0; i < count; i += 3) {
      const a = geom.positions[geom.indices[range.start + i]];
      const b = geom.positions[geom.indices[range.start + i + 1]];
      const c = geom.positions[geom.indices[range.start + i + 2]];
      const ua = geom.uvs[geom.indices[range.start + i]] || [0, 0];
      const ub = geom.uvs[geom.indices[range.start + i + 1]] || [0, 0];
      const uc = geom.uvs[geom.indices[range.start + i + 2]] || [0, 0];
      const normal = faceNormal(a, b, c);
      const tri = [a, b, c];
      const uv = [ua, ub, uc];
      for (let k = 0; k < 3; k += 1) {
        const at = (i + k) * 8;
        interleaved[at] = tri[k][0];
        interleaved[at + 1] = tri[k][1];
        interleaved[at + 2] = tri[k][2];
        interleaved[at + 3] = normal[0];
        interleaved[at + 4] = normal[1];
        interleaved[at + 5] = normal[2];
        interleaved[at + 6] = uv[k][0];
        interleaved[at + 7] = uv[k][1];
      }
      if ((i / 3) % step === 0 && lineAt + 18 <= lines.length) {
        const edge = [a, b, b, c, c, a];
        for (let k = 0; k < 6; k += 1) {
          lines[lineAt] = edge[k][0];
          lines[lineAt + 1] = edge[k][1];
          lines[lineAt + 2] = edge[k][2];
          lineAt += 3;
        }
      }
    }
    range.buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, range.buffer);
    gl.bufferData(gl.ARRAY_BUFFER, interleaved, gl.STATIC_DRAW);
    range.lines = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, range.lines);
    gl.bufferData(gl.ARRAY_BUFFER, lines.subarray(0, lineAt), gl.STATIC_DRAW);
    range.lineCount = lineAt / 3;
  }
  geom._glBuilt = gl;
}

function faceNormal(a, b, c) {
  const ux = b[0] - a[0];
  const uy = b[1] - a[1];
  const uz = b[2] - a[2];
  const vx = c[0] - a[0];
  const vy = c[1] - a[1];
  const vz = c[2] - a[2];
  return norm([uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx]);
}

function modelMatrix(orbit) {
  return mul(rotZ(orbit.roll || 0), mul(rotX(orbit.pitch), rotY(orbit.yaw)));
}

function rotY(angle) {
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  return [c, 0, s, 0, 0, 1, 0, 0, -s, 0, c, 0, 0, 0, 0, 1];
}

function rotX(angle) {
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  return [1, 0, 0, 0, 0, c, -s, 0, 0, s, c, 0, 0, 0, 0, 1];
}

function rotZ(angle) {
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  return [c, -s, 0, 0, s, c, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
}

function translate(x, y, z) {
  return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1];
}

function perspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2);
  const nf = 1 / (near - far);
  return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0];
}

function lookAt(eye, center, up) {
  const z = norm([eye[0] - center[0], eye[1] - center[1], eye[2] - center[2]]);
  const x = norm(cross(up, z));
  const y = cross(z, x);
  return [
    x[0], y[0], z[0], 0,
    x[1], y[1], z[1], 0,
    x[2], y[2], z[2], 0,
    -dot(x, eye), -dot(y, eye), -dot(z, eye), 1,
  ];
}

function mul(a, b) {
  const out = new Array(16).fill(0);
  for (let col = 0; col < 4; col += 1) {
    for (let row = 0; row < 4; row += 1) {
      out[col * 4 + row] =
        a[row] * b[col * 4] +
        a[4 + row] * b[col * 4 + 1] +
        a[8 + row] * b[col * 4 + 2] +
        a[12 + row] * b[col * 4 + 3];
    }
  }
  return out;
}

function cross(a, b) {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

function dot(a, b) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function norm(v) {
  const length = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / length, v[1] / length, v[2] / length];
}
