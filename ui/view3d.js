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
  const influences = [];
  const vecCache = new Map();
  const uvCache = new Map();
  const morphCache = new Map();
  const shapeMap = new Map();
  const shapes = collectShapes(document);
  const nodes = document.nodes || [];
  const rig = collectRig(document, bin);
  const meshNodes = [];
  nodes.forEach((node, index) => {
    if (node && node.mesh != null) meshNodes.push(index);
  });
  const instances = meshNodes.length ? meshNodes : [null];
  for (const nodeIndex of instances) {
    const listed = nodeIndex == null
      ? (document.meshes || []).map((mesh, index) => [index, mesh])
      : [[nodes[nodeIndex].mesh, (document.meshes || [])[nodes[nodeIndex].mesh]]];
    const node = nodeIndex == null ? null : nodes[nodeIndex];
    const skin = node && node.skin != null ? rig.skins[node.skin] : null;
    const skinned = Boolean(skin && skin.joints.length);
    let useIndex = -1;
    if (skinned) {
      const meshWorld = worlds.get(nodeIndex) || identityMatrix();
      useIndex = rig.uses.length;
      rig.uses.push({ skin: node.skin, meshWorld, invMesh: invertMatrix(meshWorld) });
    }
    const matrix = skinned || nodeIndex == null ? null : worlds.get(nodeIndex);
    for (const pair of listed) {
      const meshIndex = pair[0];
      const mesh = pair[1];
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
        let joints = null;
        let weights = null;
        if (skinned && attr.JOINTS_0 != null && attr.WEIGHTS_0 != null) {
          joints = readVec4(document, bin, attr.JOINTS_0, false);
          weights = readVec4(document, bin, attr.WEIGHTS_0, true);
        }
        const posed = Boolean(joints && weights);
        const placed = posed ? null : (matrix || (skinned ? worlds.get(nodeIndex) : null));
        if (placed && !isIdentity(placed)) chunk = chunk.map((point) => mulPoint(placed, point));
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
        const morphs = morphSources(document, bin, prim, meshIndex, posed ? null : placed, shapes, morphCache);
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
            if (posed) {
              influences.push({
                j: joints[index] || [0, 0, 0, 0],
                w: weights[index] || [1, 0, 0, 0],
                use: useIndex,
              });
            } else {
              influences.push(null);
            }
            stashMorphs(shapeMap, morphs, index, mapped);
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
  const hasSkin = influences.some((item) => item);
  const humanoid = hasSkin ? humanoidMap(document) : {};
  return {
    positions,
    uvs,
    indices,
    ranges,
    shapes: shapeMap,
    shapeNames: shapes.names.filter((name) => shapeMap.has(name)),
    materials: (document.materials || []).map((_mat, index) => materialView(document, index)),
    images: readImages(document, bin),
    rig: hasSkin ? rig : null,
    influences: hasSkin ? influences : null,
    humanoid,
    boneList: boneList(humanoid),
    clips: hasSkin ? collectClips(document, bin) : [],
  };
}

function collectShapes(document) {
  const found = [];
  const binds = new Map();
  const add = (mesh, index, name, scale) => {
    if (mesh == null || index == null) return;
    const cleaned = String(name || "").trim();
    if (!cleaned || cleaned.toLowerCase() === "neutral") return;
    if (!found.includes(cleaned)) found.push(cleaned);
    let table = binds.get(mesh);
    if (!table) {
      table = new Map();
      binds.set(mesh, table);
    }
    const list = table.get(index) || [];
    list.push({ name: cleaned, scale: scale == null ? 1 : scale });
    table.set(index, list);
  };
  const groups = ((((document.extensions || {}).VRM || {}).blendShapeMaster || {}).blendShapeGroups) || [];
  for (const group of groups) {
    if (!group || group.presetName === "neutral") continue;
    const name = group.name || group.presetName;
    for (const bind of group.binds || []) {
      const weight = bind && bind.weight;
      const scale = weight == null ? 1 : (Math.abs(weight) > 1 ? weight / 100 : weight);
      add(bind.mesh, bind.index, name, scale);
    }
  }
  const expressions = (((document.extensions || {}).VRMC_vrm || {}).expressions) || {};
  for (const bucket of ["preset", "custom"]) {
    const table = expressions[bucket] || {};
    for (const key of Object.keys(table)) {
      if (key === "neutral") continue;
      const expr = table[key] || {};
      for (const bind of expr.morphTargetBinds || []) {
        const node = (document.nodes || [])[bind.node];
        const mesh = node && node.mesh != null ? node.mesh : null;
        const weight = bind.weight;
        const scale = weight == null ? 1 : (Math.abs(weight) > 1 ? weight / 100 : weight);
        add(mesh, bind.index, expr.name || key, scale);
      }
    }
  }
  if (!found.length) {
    (document.meshes || []).forEach((mesh, meshIndex) => {
      const names = (mesh && mesh.extras && mesh.extras.targetNames) || [];
      names.forEach((name, index) => add(meshIndex, index, name, 1));
    });
  }
  const preferred = ["Blink", "A", "I", "U", "E", "O", "Joy", "Angry", "Sorrow", "Fun", "Surprised", "Blink_L", "Blink_R"];
  const ordered = found.slice().sort((a, b) => {
    const ia = preferred.indexOf(a);
    const ib = preferred.indexOf(b);
    if (ia === -1 && ib === -1) return found.indexOf(a) - found.indexOf(b);
    if (ia === -1) return 1;
    if (ib === -1) return -1;
    return ia - ib;
  }).slice(0, 12);
  return { names: ordered, binds, keep: new Set(ordered) };
}

function morphSources(document, bin, prim, meshIndex, matrix, shapes, morphCache) {
  const table = shapes.binds.get(meshIndex);
  const sources = [];
  (prim.targets || []).forEach((target, index) => {
    const named = ((table && table.get(index)) || []).filter((item) => shapes.keep.has(item.name));
    if (!named.length || !target || target.POSITION == null) return;
    let raw = morphCache.get(target.POSITION);
    if (!raw) {
      try {
        raw = readVec3(document, bin, target.POSITION);
      } catch (_error) {
        return;
      }
      morphCache.set(target.POSITION, raw);
    }
    if (matrix && !isIdentity(matrix)) raw = raw.map((point) => mulDir(matrix, point));
    sources.push({ named, raw });
  });
  return sources;
}

function stashMorphs(shapeMap, sources, sourceIndex, mapped) {
  for (const source of sources) {
    const delta = source.raw[sourceIndex] || [0, 0, 0];
    for (const item of source.named) {
      let bucket = shapeMap.get(item.name);
      if (!bucket) {
        bucket = [];
        shapeMap.set(item.name, bucket);
      }
      const scaled = item.scale === 1
        ? delta
        : [delta[0] * item.scale, delta[1] * item.scale, delta[2] * item.scale];
      const previous = bucket[mapped];
      bucket[mapped] = previous
        ? [previous[0] + scaled[0], previous[1] + scaled[1], previous[2] + scaled[2]]
        : scaled;
    }
  }
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

function mulDir(matrix, point) {
  return [
    matrix[0] * point[0] + matrix[4] * point[1] + matrix[8] * point[2],
    matrix[1] * point[0] + matrix[5] * point[1] + matrix[9] * point[2],
    matrix[2] * point[0] + matrix[6] * point[1] + matrix[10] * point[2],
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
  grid: false,
  backdrop: "dark",
  shape: "",
  shapeNames: [],
  shapeKey: "",
  angle: "",
  thumbFor: {},
  poseMode: false,
  showBones: true,
  pose: {},
  bones: [],
  boneKey: "",
  selected: "",
  clips: [],
  clipKey: "",
  clip: -1,
  time: 0,
  playing: false,
  speed: 1,
  loop: true,
};
let meshSpin = 0;

function facingYaw(geom) {
  if (!geom || !geom.rig || !geom.humanoid || !geom.humanoid.hips) return 0;
  const worlds = geom._bindWorlds || boneWorlds(geom, {});
  const forward = modelAxes(geom, worlds).forward;
  const yaw = Math.atan2(-forward[0], forward[2]);
  return Number.isFinite(yaw) ? yaw : 0;
}

function resetMeshCamera(geom) {
  const front = facingYaw(geom);
  meshCamera.yaw = front - 0.62;
  meshCamera.pitch = 0.16;
  meshCamera.roll = 0;
  meshCamera.dist = meshCamera.baseDist || 3.2;
  meshCamera.panX = 0;
  meshCamera.panY = 0;
  meshCamera.angle = geom && geom.humanoid ? "three" : "";
}

function meshFlags() {
  return {
    auto: meshCamera.auto,
    wire: meshCamera.wire,
    light: meshCamera.light,
    large: meshCamera.large,
    grid: meshCamera.grid,
    backdrop: meshCamera.backdrop,
    shape: meshCamera.shape,
    angle: meshCamera.angle,
    poseMode: meshCamera.poseMode,
    showBones: meshCamera.showBones !== false,
    selected: meshCamera.selected || "",
  };
}

function showMesh(canvas, geom) {
  const id = canvas.dataset.meshId || "";
  if (meshCamera.meshId !== id) {
    meshCamera.shape = "";
    meshCamera.angle = "";
    meshCamera.shapeNames = [];
    meshCamera.shapeKey = "";
    meshCamera.pose = {};
    meshCamera.bones = [];
    meshCamera.boneKey = "";
    meshCamera.selected = "";
    meshCamera.playing = false;
    meshCamera.clip = -1;
    meshCamera.time = 0;
    meshCamera.clips = [];
    meshCamera.clipKey = "";
  }
  if (geom.rig) {
    applyClipFrame(geom);
  } else if (!geom.centered) {
    centerGeometry(geom);
    geom.centered = true;
    geom.rest = geom.positions.map((point) => [point[0], point[1], point[2]]);
    poseGeometry(geom);
  } else {
    poseGeometry(geom);
  }
  const span = Math.max(geom.half[0], geom.half[1], geom.half[2] || 0, 0.05);
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
    resetMeshCamera(geom);
  }
  canvas._geom = geom;
  canvas._orbit = meshCamera;
  bindOrbit(canvas);
  ensureSpin();
  const present = () => {
    redraw(canvas);
    if (useGl && webglBlank(canvas) && canvas.getContext("2d")) {
      geom.preferCanvas = true;
      canvas._gl = null;
      canvas.dataset.mode = "canvas";
      meshCamera.baseDist = Math.max(1.2, span * 2.15);
      meshCamera.dist = meshCamera.baseDist;
      redraw(canvas);
    }
    queueThumb(canvas);
    publishMeshUi(geom);
  };
  if (useGl) {
    canvas._gl = gl;
    canvas.dataset.mode = "webgl";
    requestAnimationFrame(present);
    return;
  }
  canvas._gl = null;
  canvas.dataset.mode = "canvas";
  requestAnimationFrame(present);
}

function poseGeometry(geom) {
  if (!geom.rest) return;
  const deltas = geom.shapes && meshCamera.shape && geom.shapes.get ? geom.shapes.get(meshCamera.shape) : null;
  geom.positions = geom.rest.map((point, index) => {
    const delta = deltas && deltas[index];
    if (!delta) return [point[0], point[1], point[2]];
    return [point[0] + delta[0], point[1] + delta[1], point[2] + delta[2]];
  });
  geom._glBuilt = null;
}

function publishMeshUi(geom) {
  const names = geom.shapeNames || [];
  const shapeKey = `${meshCamera.meshId}\n${names.join("\n")}`;
  const bones = geom.boneList || [];
  const boneKey = `${meshCamera.meshId}\n${bones.map((bone) => bone.name).join(",")}`;
  const clips = (geom.clips || []).map((clip) => ({ name: clip.name, duration: Number(clip.duration) || 0 }));
  const clipKey = `${meshCamera.meshId}\n${clips.map((clip) => `${clip.name}@${clip.duration}`).join("|")}`;
  const changed = meshCamera.shapeKey !== shapeKey || meshCamera.boneKey !== boneKey || meshCamera.clipKey !== clipKey;
  meshCamera.shapeKey = shapeKey;
  meshCamera.shapeNames = names.slice();
  meshCamera.boneKey = boneKey;
  meshCamera.bones = bones;
  meshCamera.clipKey = clipKey;
  meshCamera.clips = clips;
  if (meshCamera.clip >= clips.length) meshCamera.clip = -1;
  if (!meshCamera.selected && bones.length) {
    const upper = bones.find((bone) => bone.name === "leftUpperArm") || bones[0];
    meshCamera.selected = upper.name;
  }
  if (changed && window.render) render();
}

function queueThumb(canvas) {
  const id = canvas.dataset.meshId;
  if (!id || canvas.dataset.hasThumb === "1" || canvas.dataset.ready !== "1") return;
  if (meshCamera.thumbFor[id]) return;
  let png = "";
  try { png = canvas.toDataURL("image/png"); } catch (_error) { return; }
  if (!png || png.length < 64) return;
  meshCamera.thumbFor[id] = true;
  rpc("character.mesh_thumb", { id, png }).then((pack) => {
    if (pack && pack.tree && window.absorb) absorb(pack);
  }).catch(() => {});
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
    const point = canvasPoint(canvas, event);
    const axis = meshCamera.poseMode && event.button === 0 && !event.shiftKey && !event.altKey
      ? pickAxis(canvas, point)
      : null;
    const mode = axis ? "bone" : (event.button === 2 || event.shiftKey ? "pan" : (event.button === 1 || event.altKey ? "roll" : "orbit"));
    const selected = meshCamera.selected;
    const geom = canvas._geom;
    const node = geom && geom.humanoid ? geom.humanoid[selected] : null;
    const current = selected && meshCamera.pose[selected]
      ? meshCamera.pose[selected].slice()
      : (geom && node != null ? geom.rig.nodes[node].r.slice() : [0, 0, 0, 1]);
    canvas._drag = {
      x: event.clientX,
      y: event.clientY,
      px: point.x,
      py: point.y,
      mode,
      axis,
      quat: current,
      moved: false,
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
    if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
    if (drag.mode === "bone") {
      rotateSelected(canvas, drag, dx);
      return;
    }
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
  const end = (event) => {
    const drag = canvas._drag;
    canvas._drag = null;
    if (!drag || !meshCamera.poseMode) return;
    if (drag.mode === "bone") {
      if (window.render) render();
      return;
    }
    if (drag.moved || drag.mode !== "orbit") return;
    const point = canvasPoint(canvas, event);
    const bone = pickBone(canvas, point);
    if (!bone || bone === meshCamera.selected) return;
    meshCamera.selected = bone;
    if (window.render) render();
  };
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

let meshClock = 0;

function ensureSpin() {
  if (meshSpin) return;
  meshClock = performance.now();
  const tick = (now) => {
    meshSpin = requestAnimationFrame(tick);
    const canvas = document.querySelector("canvas[data-mesh-id]");
    const dt = Math.min(0.05, Math.max(0, (now - meshClock) / 1000));
    meshClock = now;
    let dirty = false;
    if (meshCamera.playing && canvas && canvas._geom && !canvas._drag) {
      const clip = activeClip(canvas._geom);
      if (clip) {
        const duration = Number(clip.duration) || 0;
        meshCamera.time = (Number(meshCamera.time) || 0) + dt * (Number(meshCamera.speed) || 1);
        if (duration > 0 && meshCamera.loop === false && meshCamera.time >= duration) {
          meshCamera.time = duration;
          meshCamera.playing = false;
          applyClipFrame(canvas._geom);
          redraw(canvas);
          paintAnimReadout(clip);
          if (window.render) render();
          return;
        }
        if (duration > 0 && meshCamera.loop !== false && meshCamera.time > duration) meshCamera.time %= duration;
        applyClipFrame(canvas._geom);
        paintAnimReadout(clip);
        dirty = true;
      }
    }
    if (meshCamera.auto && canvas && !canvas._drag && canvas._geom) {
      meshCamera.yaw += 0.012;
      dirty = true;
    }
    if (dirty && canvas) redraw(canvas);
  };
  meshSpin = requestAnimationFrame(tick);
}

function paintAnimReadout(clip) {
  const slider = document.querySelector("[data-anim-time]");
  if (slider && document.activeElement !== slider) slider.value = String(meshCamera.time || 0);
  const readout = document.querySelector("[data-anim-readout]");
  if (readout) {
    const duration = Number(clip && clip.duration) || 0;
    readout.textContent = `${(Number(meshCamera.time) || 0).toFixed(2)}s / ${duration.toFixed(2)}s`;
  }
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
      const darkClear = gray && pixel[0] > 34 && pixel[0] < 52;
      const lightClear = gray && pixel[0] > 220 && pixel[0] < 245;
      if (!darkClear && !lightClear) colored += 1;
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

function gridSegments(geom) {
  const half = geom.half || [0.5, 0.5, 0.5];
  const y = -half[1] - Math.max(half[1], 0.05) * 0.04;
  const span = Math.max(half[0], half[2], 0.25) * 2.4;
  const step = span / 4;
  const lines = [];
  for (let i = -4; i <= 4; i += 1) {
    const t = i * step;
    lines.push([[-span, y, t], [span, y, t]]);
    lines.push([[t, y, -span], [t, y, span]]);
  }
  return lines;
}

function paintBackdrop(ctx, canvas) {
  const light = meshCamera.backdrop === "light";
  ctx.fillStyle = light ? "#e8e8ec" : "#2a2a2c";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (!meshCamera.grid || !canvas._geom) return;
  ctx.strokeStyle = light ? "rgba(60,60,67,0.45)" : "rgba(210,210,214,0.4)";
  ctx.lineWidth = 1;
  for (const segment of gridSegments(canvas._geom)) {
    const a = projectPoint(segment[0], canvas._orbit, canvas.width, canvas.height);
    const b = projectPoint(segment[1], canvas._orbit, canvas.width, canvas.height);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }
}

function drawGridGl(gl, geom) {
  const lines = gridSegments(geom);
  const verts = new Float32Array(lines.length * 6);
  lines.forEach((segment, index) => {
    const at = index * 6;
    verts[at] = segment[0][0];
    verts[at + 1] = segment[0][1];
    verts[at + 2] = segment[0][2];
    verts[at + 3] = segment[1][0];
    verts[at + 4] = segment[1][1];
    verts[at + 5] = segment[1][2];
  });
  if (!geom._gridBuf || geom._gridGl !== gl) {
    if (geom._gridBuf && geom._gridGl === gl && gl.isBuffer(geom._gridBuf)) gl.deleteBuffer(geom._gridBuf);
    geom._gridBuf = gl.createBuffer();
    geom._gridGl = gl;
  }
  gl.bindBuffer(gl.ARRAY_BUFFER, geom._gridBuf);
  gl.bufferData(gl.ARRAY_BUFFER, verts, gl.DYNAMIC_DRAW);
  gl.uniform1f(gl._line, 1);
  gl.uniform1f(gl._tex, 0);
  const tone = meshCamera.backdrop === "light" ? 0.45 : 0.72;
  gl.uniform3f(gl._color, tone, tone, tone);
  gl.enableVertexAttribArray(gl._pos);
  gl.vertexAttribPointer(gl._pos, 3, gl.FLOAT, false, 12, 0);
  if (gl._normal >= 0) gl.disableVertexAttribArray(gl._normal);
  if (gl._uv >= 0) gl.disableVertexAttribArray(gl._uv);
  gl.depthMask(true);
  gl.drawArrays(gl.LINES, 0, verts.length / 3);
  gl.uniform1f(gl._line, 0);
}

function drawCanvas(canvas) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const geom = canvas._geom;
  const orbit = canvas._orbit;
  paintBackdrop(ctx, canvas);
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
  paintPose(ctx, canvas);
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
        gl_FragColor = vec4(uColor, 1.0);
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
  const lightGround = meshCamera.backdrop === "light";
  gl.clearColor(lightGround ? 0.91 : 0.165, lightGround ? 0.91 : 0.165, lightGround ? 0.925 : 0.173, 1);
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
  if (meshCamera.grid) drawGridGl(gl, geom);
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
    gl.uniform3f(gl._color, 1, 1, 1);
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
  paintPoseGl(gl, canvas);
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
  const same = geom._glBuilt == null;
  for (const range of geom.ranges) {
    if (same && range.buffer && gl.isBuffer(range.buffer)) gl.deleteBuffer(range.buffer);
    if (same && range.lines && gl.isBuffer(range.lines)) gl.deleteBuffer(range.lines);
    range.buffer = null;
    range.lines = null;
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

function identityMatrix() {
  return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
}

function invertMatrix(matrix) {
  const rows = [0, 1, 2, 3].map((row) => [0, 1, 2, 3].map((column) => matrix[column * 4 + row]));
  const inverse = [0, 1, 2, 3].map((row) => [0, 1, 2, 3].map((column) => (row === column ? 1 : 0)));
  for (let column = 0; column < 4; column += 1) {
    let pivot = column;
    for (let row = column + 1; row < 4; row += 1) {
      if (Math.abs(rows[row][column]) > Math.abs(rows[pivot][column])) pivot = row;
    }
    if (Math.abs(rows[pivot][column]) < 1e-12) return identityMatrix();
    [rows[column], rows[pivot]] = [rows[pivot], rows[column]];
    [inverse[column], inverse[pivot]] = [inverse[pivot], inverse[column]];
    const scale = rows[column][column];
    for (let cell = 0; cell < 4; cell += 1) {
      rows[column][cell] /= scale;
      inverse[column][cell] /= scale;
    }
    for (let row = 0; row < 4; row += 1) {
      if (row === column) continue;
      const factor = rows[row][column];
      for (let cell = 0; cell < 4; cell += 1) {
        rows[row][cell] -= factor * rows[column][cell];
        inverse[row][cell] -= factor * inverse[column][cell];
      }
    }
  }
  const out = new Array(16);
  for (let row = 0; row < 4; row += 1) {
    for (let column = 0; column < 4; column += 1) out[column * 4 + row] = inverse[row][column];
  }
  return out;
}

function readVec4(document, bin, index, asWeight) {
  const accessor = document.accessors[index];
  const view = accessor ? document.bufferViews[accessor.bufferView] : null;
  if (!accessor || !view || accessor.type !== "VEC4") return null;
  const width = { 5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4 }[accessor.componentType];
  if (!width) return null;
  const start = (view.byteOffset || 0) + (accessor.byteOffset || 0);
  const stride = view.byteStride || width * 4;
  const data = new DataView(bin.buffer, bin.byteOffset, bin.byteLength);
  const values = [];
  for (let i = 0; i < accessor.count; i += 1) {
    const at = start + i * stride;
    const tuple = [0, 0, 0, 0];
    for (let k = 0; k < 4; k += 1) {
      const off = at + k * width;
      let value = 0;
      if (accessor.componentType === 5126) value = data.getFloat32(off, true);
      else if (accessor.componentType === 5121) value = data.getUint8(off);
      else if (accessor.componentType === 5123) value = data.getUint16(off, true);
      else if (accessor.componentType === 5125) value = data.getUint32(off, true);
      else if (accessor.componentType === 5120) value = data.getInt8(off);
      else value = data.getInt16(off, true);
      if (asWeight && accessor.componentType !== 5126) {
        if (accessor.componentType === 5121) value /= 255;
        else if (accessor.componentType === 5123) value /= 65535;
        else if (accessor.componentType === 5120) value = Math.max(value / 127, -1);
        else value = Math.max(value / 32767, -1);
      }
      tuple[k] = value;
    }
    values.push(tuple);
  }
  return values;
}

function readMat4(document, bin, index) {
  const accessor = document.accessors[index];
  const view = accessor ? document.bufferViews[accessor.bufferView] : null;
  if (!accessor || !view || accessor.componentType !== 5126 || accessor.type !== "MAT4") return null;
  const start = (view.byteOffset || 0) + (accessor.byteOffset || 0);
  const stride = view.byteStride || 64;
  const data = new DataView(bin.buffer, bin.byteOffset, bin.byteLength);
  const values = [];
  for (let i = 0; i < accessor.count; i += 1) {
    const at = start + i * stride;
    const matrix = new Array(16);
    for (let k = 0; k < 16; k += 1) matrix[k] = data.getFloat32(at + k * 4, true);
    values.push(matrix);
  }
  return values;
}

function collectRig(document, bin) {
  const skins = (document.skins || []).map((skin) => {
    const joints = ((skin && skin.joints) || []).slice();
    let ibms = null;
    if (skin && skin.inverseBindMatrices != null) {
      try { ibms = readMat4(document, bin, skin.inverseBindMatrices); } catch (_error) { ibms = null; }
    }
    if (!ibms) ibms = joints.map(() => identityMatrix());
    return { joints, ibms };
  });
  const nodes = (document.nodes || []).map((node) => {
    const item = node || {};
    return {
      name: item.name || "",
      t: (item.translation || [0, 0, 0]).slice(0, 3),
      r: (item.rotation || [0, 0, 0, 1]).slice(0, 4),
      s: (item.scale || [1, 1, 1]).slice(0, 3),
      matrix: item.matrix && item.matrix.length === 16 ? item.matrix.slice() : null,
    };
  });
  const parent = nodes.map(() => -1);
  (document.nodes || []).forEach((node, index) => {
    for (const child of (node && node.children) || []) {
      if (child >= 0 && child < parent.length) parent[child] = index;
    }
  });
  return { skins, uses: [], nodes, parent };
}

const BONE_ALIASES = [
  ["hips", ["hips", "pelvis"]],
  ["spine", ["spine"]],
  ["chest", ["chest"]],
  ["upperChest", ["upperchest"]],
  ["neck", ["neck"]],
  ["head", ["head"]],
  ["leftShoulder", ["leftshoulder", "lshoulder", "shoulderl"]],
  ["leftUpperArm", ["leftupperarm", "leftarm", "lupperarm", "upperarml"]],
  ["leftLowerArm", ["leftlowerarm", "leftforearm", "lforearm", "forearml", "lowerarml"]],
  ["leftHand", ["lefthand", "lhand", "handl"]],
  ["rightShoulder", ["rightshoulder", "rshoulder", "shoulderr"]],
  ["rightUpperArm", ["rightupperarm", "rightarm", "rupperarm", "upperarmr"]],
  ["rightLowerArm", ["rightlowerarm", "rightforearm", "rforearm", "forearmr", "lowerarmr"]],
  ["rightHand", ["righthand", "rhand", "handr"]],
  ["leftUpperLeg", ["leftupperleg", "leftupleg", "leftthigh", "lupleg", "thighl"]],
  ["leftLowerLeg", ["leftlowerleg", "leftleg", "leftcalf", "lcalf", "calfl"]],
  ["leftFoot", ["leftfoot", "lfoot", "footl"]],
  ["leftToes", ["lefttoes", "lefttoe", "ltoe", "toel"]],
  ["rightUpperLeg", ["rightupperleg", "rightupleg", "rightthigh", "rupleg", "thighr"]],
  ["rightLowerLeg", ["rightlowerleg", "rightleg", "rightcalf", "rcalf", "calfr"]],
  ["rightFoot", ["rightfoot", "rfoot", "footr"]],
  ["rightToes", ["righttoes", "righttoe", "rtoe", "toer"]],
];

function humanoidMap(document) {
  const mapped = vrmHumanoid(document);
  if (Object.keys(mapped).length) return mapped;
  const nodes = (document.nodes || []).map((node, index) => ({
    index,
    name: (node && node.name) || "",
    parent: -1,
  }));
  (document.nodes || []).forEach((node, index) => {
    for (const child of (node && node.children) || []) {
      if (nodes[child]) nodes[child].parent = index;
    }
  });
  return humanoidFromNames(nodes);
}

function vrmHumanoid(document) {
  const found = {};
  const groups = ((((document.extensions || {}).VRM || {}).humanoid || {}).humanBones) || [];
  for (const bone of groups) {
    if (bone && typeof bone.bone === "string" && Number.isInteger(bone.node)) found[bone.bone] = bone.node;
  }
  const bones = ((((document.extensions || {}).VRMC_vrm || {}).humanoid || {}).humanBones) || {};
  Object.keys(bones).forEach((name) => {
    const spec = bones[name];
    if (spec && Number.isInteger(spec.node)) found[name] = spec.node;
  });
  return found;
}

function humanoidFromNames(nodes) {
  const assigned = {};
  const used = new Set();
  const compact = nodes.map((node) => [node.index, String(node.name || "").toLowerCase().replace(/[^a-z0-9]/g, "")]);
  for (const pair of BONE_ALIASES) {
    const bone = pair[0];
    const aliases = pair[1];
    let best = null;
    let bestScore = null;
    for (const item of compact) {
      const index = item[0];
      const name = item[1];
      if (used.has(index) || !name) continue;
      for (const alias of aliases) {
        if (!name.includes(alias)) continue;
        const score = [-alias.length, name === alias ? 0 : 1, name.length];
        if (!bestScore || score[0] < bestScore[0] || (score[0] === bestScore[0] && score[1] < bestScore[1]) || (score[0] === bestScore[0] && score[1] === bestScore[1] && score[2] < bestScore[2])) {
          bestScore = score;
          best = index;
        }
      }
    }
    if (best != null) {
      assigned[bone] = best;
      used.add(best);
    }
  }
  const spine = nameChain(nodes, ["hips", "pelvis", "spine", "chest", "torso", "abdomen"], "", used);
  takeChain(assigned, used, spine, fitSlots(spine.length, ["hips", "spine", "chest", "upperChest"], false));
  let neck = nameChain(nodes, ["neck", "head"], "", used);
  if (neck.length && assigned.neck == null) {
    assigned.neck = neck[0];
    used.add(neck[0]);
    neck = neck.slice(1);
  }
  if (neck.length && assigned.head == null) {
    assigned.head = neck[neck.length - 1];
    used.add(neck[neck.length - 1]);
  }
  ["left", "right"].forEach((side) => {
    const arms = nameChain(nodes, ["shoulder", "arm", "forearm", "elbow", "wrist", "hand"], side, used);
    takeChain(assigned, used, arms, fitSlots(arms.length, [`${side}Shoulder`, `${side}UpperArm`, `${side}LowerArm`, `${side}Hand`], true));
    const legs = nameChain(nodes, ["upleg", "thigh", "leg", "calf", "shin", "knee", "foot", "toe", "ball"], side, used);
    takeChain(assigned, used, legs, fitSlots(legs.length, [`${side}UpperLeg`, `${side}LowerLeg`, `${side}Foot`, `${side}Toes`], false));
  });
  return assigned;
}

function boneSide(name) {
  const raw = String(name || "").toLowerCase();
  if (raw.includes("left") || /(^|[_\.])l($|[_\.])/.test(raw) || raw.includes("joint_l")) return "left";
  if (raw.includes("right") || /(^|[_\.])r($|[_\.])/.test(raw) || raw.includes("joint_r")) return "right";
  return "";
}

function nameChain(nodes, words, side, used) {
  const picked = [];
  for (const node of nodes) {
    if (used.has(node.index)) continue;
    const sideName = boneSide(node.name);
    if (side && sideName !== side) continue;
    if (!side && sideName) continue;
    const compact = String(node.name || "").toLowerCase().replace(/[^a-z0-9]/g, "");
    if (words.some((word) => compact.includes(word))) picked.push(node.index);
  }
  const depthOf = (index) => {
    let depth = 0;
    const seen = new Set();
    let cursor = index;
    while (cursor >= 0 && !seen.has(cursor)) {
      seen.add(cursor);
      const node = nodes[cursor];
      if (!node || node.parent == null || node.parent < 0) break;
      cursor = node.parent;
      depth += 1;
    }
    return depth;
  };
  picked.sort((a, b) => depthOf(a) - depthOf(b) || a - b);
  return picked;
}

function fitSlots(count, slots, dropFirst) {
  if (count <= 0) return [];
  if (count >= slots.length) return slots;
  if (dropFirst && slots.length > 1) return slots.slice(1, 1 + count);
  return slots.slice(0, count);
}

function takeChain(assigned, used, chain, slots) {
  let cursor = 0;
  for (const bone of slots) {
    if (!bone || assigned[bone] != null) continue;
    if (cursor >= chain.length) break;
    assigned[bone] = chain[cursor];
    used.add(chain[cursor]);
    cursor += 1;
  }
}

const BONE_LABELS = {
  hips: "Hips", spine: "Spine", chest: "Chest", upperChest: "Upper chest",
  neck: "Neck", head: "Head", leftEye: "Left eye", rightEye: "Right eye",
  leftShoulder: "Shoulder", leftUpperArm: "Upper arm", leftLowerArm: "Lower arm", leftHand: "Hand",
  rightShoulder: "Shoulder", rightUpperArm: "Upper arm", rightLowerArm: "Lower arm", rightHand: "Hand",
  leftUpperLeg: "Upper leg", leftLowerLeg: "Lower leg", leftFoot: "Foot", leftToes: "Toes",
  rightUpperLeg: "Upper leg", rightLowerLeg: "Lower leg", rightFoot: "Foot", rightToes: "Toes",
};

const FINGER_BONES = [
  "leftThumbProximal", "leftThumbIntermediate", "leftThumbDistal",
  "leftIndexProximal", "leftIndexIntermediate", "leftIndexDistal",
  "leftMiddleProximal", "leftMiddleIntermediate", "leftMiddleDistal",
  "leftRingProximal", "leftRingIntermediate", "leftRingDistal",
  "leftLittleProximal", "leftLittleIntermediate", "leftLittleDistal",
  "rightThumbProximal", "rightThumbIntermediate", "rightThumbDistal",
  "rightIndexProximal", "rightIndexIntermediate", "rightIndexDistal",
  "rightMiddleProximal", "rightMiddleIntermediate", "rightMiddleDistal",
  "rightRingProximal", "rightRingIntermediate", "rightRingDistal",
  "rightLittleProximal", "rightLittleIntermediate", "rightLittleDistal",
];

function boneList(humanoid) {
  const groups = [
    ["Body", ["hips", "spine", "chest", "upperChest"]],
    ["Head", ["neck", "head", "leftEye", "rightEye"]],
    ["Left arm", ["leftShoulder", "leftUpperArm", "leftLowerArm", "leftHand"]],
    ["Right arm", ["rightShoulder", "rightUpperArm", "rightLowerArm", "rightHand"]],
    ["Left leg", ["leftUpperLeg", "leftLowerLeg", "leftFoot", "leftToes"]],
    ["Right leg", ["rightUpperLeg", "rightLowerLeg", "rightFoot", "rightToes"]],
  ];
  const list = [];
  for (const group of groups) {
    for (const name of group[1]) {
      if (humanoid[name] == null) continue;
      list.push({ name, group: group[0], label: BONE_LABELS[name] || name });
    }
  }
  for (const name of FINGER_BONES) {
    if (humanoid[name] == null) continue;
    const label = name.replace(/^(left|right)/, "").replace(/([A-Z])/g, " $1").trim();
    list.push({ name, group: "Hands", label });
  }
  return list;
}

function applyPose(geom) {
  if (!geom.rig) return;
  if (!geom.bindPositions) geom.bindPositions = geom.positions.map((point) => [point[0], point[1], point[2]]);
  if (!geom._bindWorlds) {
    const saved = geom._animLocals;
    geom._animLocals = null;
    geom._bindWorlds = boneWorlds(geom, {});
    geom._animLocals = saved;
  }
  if (!geom.bindCenter) {
    const saved = meshCamera.pose;
    const savedAnim = geom._animLocals;
    meshCamera.pose = {};
    geom._animLocals = null;
    const bind = posedPositions(geom);
    meshCamera.pose = saved;
    geom._animLocals = savedAnim;
    geom.bindCenter = centerOf(bind);
    geom.half = halfOf(bind, geom.bindCenter);
  }
  const world = posedPositions(geom);
  const center = geom.bindCenter;
  geom.positions = world.map((point) => [point[0] - center[0], point[1] - center[1], point[2] - center[2]]);
  geom._glBuilt = null;
  geom.markers = markerPoints(geom);
  geom.gizmo = gizmoLines(geom);
}

function centerOf(points) {
  let minX = Infinity; let minY = Infinity; let minZ = Infinity;
  let maxX = -Infinity; let maxY = -Infinity; let maxZ = -Infinity;
  for (const point of points) {
    minX = Math.min(minX, point[0]); maxX = Math.max(maxX, point[0]);
    minY = Math.min(minY, point[1]); maxY = Math.max(maxY, point[1]);
    minZ = Math.min(minZ, point[2]); maxZ = Math.max(maxZ, point[2]);
  }
  return [(minX + maxX) / 2, (minY + maxY) / 2, (minZ + maxZ) / 2];
}

function halfOf(points, center) {
  let x = 0; let y = 0; let z = 0;
  for (const point of points) {
    x = Math.max(x, Math.abs(point[0] - center[0]));
    y = Math.max(y, Math.abs(point[1] - center[1]));
    z = Math.max(z, Math.abs(point[2] - center[2]));
  }
  return [x || 0.5, y || 0.5, z || 0.5];
}

function posedPositions(geom) {
  const worlds = boneWorlds(geom, meshCamera.pose || {});
  geom._worlds = worlds;
  const shift = animRootShift(geom, worlds);
  geom._animShift = shift;
  const mats = new Map();
  geom.rig.uses.forEach((use, useIndex) => {
    const skin = geom.rig.skins[use.skin];
    if (!skin) return;
    mats.set(useIndex, skin.joints.map((node, joint) => mul(mul(use.invMesh, worlds[node] || identityMatrix()), skin.ibms[joint] || identityMatrix())));
  });
  const deltas = geom.shapes && meshCamera.shape && geom.shapes.get ? geom.shapes.get(meshCamera.shape) : null;
  const out = new Array(geom.bindPositions.length);
  for (let i = 0; i < geom.bindPositions.length; i += 1) {
    const base = geom.bindPositions[i];
    const delta = deltas && deltas[i];
    const point = delta ? [base[0] + delta[0], base[1] + delta[1], base[2] + delta[2]] : base;
    const influence = geom.influences && geom.influences[i];
    if (!influence) {
      out[i] = [point[0] - shift[0], point[1] - shift[1], point[2] - shift[2]];
      continue;
    }
    const use = geom.rig.uses[influence.use];
    const table = mats.get(influence.use);
    const acc = [0, 0, 0];
    let covered = 0;
    for (let k = 0; k < 4; k += 1) {
      const weight = influence.w[k] || 0;
      if (!weight) continue;
      const joint = influence.j[k] | 0;
      if (!table || !table[joint]) continue;
      const moved = mulPoint(table[joint], point);
      acc[0] += moved[0] * weight;
      acc[1] += moved[1] * weight;
      acc[2] += moved[2] * weight;
      covered += weight;
    }
    const skinned = covered ? acc : [point[0], point[1], point[2]];
    const placed = mulPoint(use.meshWorld, skinned);
    out[i] = [placed[0] - shift[0], placed[1] - shift[1], placed[2] - shift[2]];
  }
  return out;
}

function animRootShift(geom, worlds) {
  if (!geom._animLocals || !geom._bindWorlds) return [0, 0, 0];
  let node = geom.humanoid && geom.humanoid.hips != null ? geom.humanoid.hips : -1;
  if (node < 0) {
    let best = -1;
    let bestDepth = Infinity;
    Object.keys(geom._animLocals).forEach((key) => {
      const local = geom._animLocals[key];
      if (!local || !local.t) return;
      const index = Number(key);
      let depth = 0;
      let cursor = index;
      const seen = new Set();
      while (cursor >= 0 && !seen.has(cursor)) {
        seen.add(cursor);
        cursor = geom.rig.parent[cursor];
        depth += 1;
      }
      if (depth < bestDepth) {
        bestDepth = depth;
        best = index;
      }
    });
    node = best;
  }
  const now = worlds[node];
  const bind = geom._bindWorlds[node];
  if (!now || !bind) return [0, 0, 0];
  return [now[12] - bind[12], 0, now[14] - bind[14]];
}

function boneWorlds(geom, pose) {
  const nodes = geom.rig.nodes;
  const parent = geom.rig.parent;
  const humanoid = geom.humanoid || {};
  const override = new Map();
  Object.keys(pose || {}).forEach((name) => {
    const node = humanoid[name];
    const quat = pose[name];
    if (node != null && quat && quat.length === 4) override.set(node, quat);
  });
  const cache = new Array(nodes.length);
  const walk = (index) => {
    if (cache[index]) return cache[index];
    const node = nodes[index] || { t: [0, 0, 0], r: [0, 0, 0, 1], s: [1, 1, 1], matrix: null };
    const anim = geom._animLocals && geom._animLocals[index];
    const translation = anim && anim.t ? anim.t : node.t;
    const rotation = override.has(index) ? override.get(index) : (anim && anim.r ? anim.r : node.r);
    const scale = anim && anim.s ? anim.s : node.s;
    let local;
    if (override.has(index) || anim || !node.matrix) local = nodeMatrix({ translation, rotation, scale });
    else local = node.matrix;
    const above = parent[index];
    cache[index] = above == null || above < 0 ? local : mul(walk(above), local);
    return cache[index];
  };
  for (let index = 0; index < nodes.length; index += 1) walk(index);
  return cache;
}

function markerPoints(geom) {
  const worlds = geom._worlds || [];
  const center = geom.bindCenter || [0, 0, 0];
  const shift = geom._animShift || [0, 0, 0];
  return (geom.boneList || []).filter((bone) => bone.group !== "Hands" || bone.name === meshCamera.selected).map((bone) => {
    const matrix = worlds[geom.humanoid[bone.name]] || identityMatrix();
    return {
      name: bone.name,
      point: [matrix[12] - shift[0] - center[0], matrix[13] - shift[1] - center[1], matrix[14] - shift[2] - center[2]],
    };
  });
}

function gizmoLines(geom) {
  if (!meshCamera.poseMode || meshCamera.showBones === false || !meshCamera.selected || !geom.humanoid) return [];
  const node = geom.humanoid[meshCamera.selected];
  const matrix = geom._worlds && geom._worlds[node];
  if (!matrix) return [];
  const center = geom.bindCenter || [0, 0, 0];
  const shift = geom._animShift || [0, 0, 0];
  const origin = [matrix[12] - shift[0] - center[0], matrix[13] - shift[1] - center[1], matrix[14] - shift[2] - center[2]];
  const len = Math.max((geom.half && geom.half[1]) || 0.2, 0.05) * 0.28;
  return [
    ["x", [matrix[0], matrix[1], matrix[2]], [0.95, 0.28, 0.24]],
    ["y", [matrix[4], matrix[5], matrix[6]], [0.3, 0.82, 0.38]],
    ["z", [matrix[8], matrix[9], matrix[10]], [0.28, 0.48, 0.95]],
  ].map((item) => {
    const dir = norm(item[1]);
    return {
      axis: item[0],
      color: item[2],
      a: origin,
      b: [origin[0] + dir[0] * len, origin[1] + dir[1] * len, origin[2] + dir[2] * len],
    };
  });
}

function paintPose(ctx, canvas) {
  if (!meshCamera.poseMode || meshCamera.showBones === false || !canvas._geom) return;
  const geom = canvas._geom;
  ctx.save();
  for (const marker of geom.markers || []) {
    const point = projectPoint(marker.point, canvas._orbit, canvas.width, canvas.height);
    ctx.fillStyle = marker.name === meshCamera.selected ? "#ffd60a" : "#f2f2f7";
    ctx.beginPath();
    ctx.arc(point.x, point.y, marker.name === meshCamera.selected ? 5 : 3, 0, Math.PI * 2);
    ctx.fill();
  }
  for (const line of geom.gizmo || []) {
    const a = projectPoint(line.a, canvas._orbit, canvas.width, canvas.height);
    const b = projectPoint(line.b, canvas._orbit, canvas.width, canvas.height);
    ctx.strokeStyle = `rgb(${Math.round(line.color[0] * 255)}, ${Math.round(line.color[1] * 255)}, ${Math.round(line.color[2] * 255)})`;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }
  ctx.restore();
}

function paintPoseGl(gl, canvas) {
  const geom = canvas._geom;
  if (!meshCamera.poseMode || meshCamera.showBones === false || !geom) return;
  const lines = [];
  for (const marker of geom.markers || []) {
    const s = Math.max((geom.half && geom.half[1]) || 0.2, 0.05) * 0.03;
    const p = marker.point;
    const color = marker.name === meshCamera.selected ? [1, 0.84, 0.04] : [0.95, 0.95, 0.97];
    lines.push([[p[0] - s, p[1], p[2]], [p[0] + s, p[1], p[2]], color]);
    lines.push([[p[0], p[1] - s, p[2]], [p[0], p[1] + s, p[2]], color]);
  }
  for (const line of geom.gizmo || []) lines.push([line.a, line.b, line.color]);
  if (!lines.length) return;
  gl.disable(gl.DEPTH_TEST);
  gl.uniform1f(gl._line, 1);
  gl.uniform1f(gl._tex, 0);
  const groups = new Map();
  for (const line of lines) {
    const key = line[2].join(",");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(line);
  }
  groups.forEach((group, key) => {
    const color = key.split(",").map(Number);
    const verts = new Float32Array(group.length * 6);
    group.forEach((line, index) => {
      const at = index * 6;
      verts[at] = line[0][0]; verts[at + 1] = line[0][1]; verts[at + 2] = line[0][2];
      verts[at + 3] = line[1][0]; verts[at + 4] = line[1][1]; verts[at + 5] = line[1][2];
    });
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, verts, gl.DYNAMIC_DRAW);
    gl.uniform3f(gl._color, color[0], color[1], color[2]);
    gl.enableVertexAttribArray(gl._pos);
    gl.vertexAttribPointer(gl._pos, 3, gl.FLOAT, false, 12, 0);
    if (gl._normal >= 0) gl.disableVertexAttribArray(gl._normal);
    if (gl._uv >= 0) gl.disableVertexAttribArray(gl._uv);
    gl.drawArrays(gl.LINES, 0, verts.length / 3);
    gl.deleteBuffer(buffer);
  });
  gl.uniform1f(gl._line, 0);
  gl.enable(gl.DEPTH_TEST);
}

function canvasPoint(canvas, event) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: (event.clientX - rect.left) * (canvas.width / Math.max(1, rect.width)),
    y: (event.clientY - rect.top) * (canvas.height / Math.max(1, rect.height)),
  };
}

function segmentDistance(point, a, b) {
  const abx = b.x - a.x;
  const aby = b.y - a.y;
  const len = abx * abx + aby * aby || 1;
  let t = ((point.x - a.x) * abx + (point.y - a.y) * aby) / len;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(point.x - (a.x + abx * t), point.y - (a.y + aby * t));
}

function pickAxis(canvas, point) {
  const geom = canvas._geom;
  if (!geom || !geom.gizmo || !geom.gizmo.length) return null;
  let best = null;
  let bestDist = 14;
  for (const line of geom.gizmo) {
    const a = projectPoint(line.a, canvas._orbit, canvas.width, canvas.height);
    const b = projectPoint(line.b, canvas._orbit, canvas.width, canvas.height);
    const dist = segmentDistance(point, a, b);
    if (dist < bestDist) {
      bestDist = dist;
      best = line.axis;
    }
  }
  return best;
}

function pickBone(canvas, point) {
  const geom = canvas._geom;
  if (meshCamera.showBones === false || !geom || !geom.markers) return "";
  let best = "";
  let bestDist = 22;
  for (const marker of geom.markers) {
    const projected = projectPoint(marker.point, canvas._orbit, canvas.width, canvas.height);
    const dist = Math.hypot(projected.x - point.x, projected.y - point.y);
    if (dist < bestDist) {
      bestDist = dist;
      best = marker.name;
    }
  }
  return best;
}

function rotateSelected(canvas, drag, dx) {
  const name = meshCamera.selected;
  const geom = canvas._geom;
  if (!name || !geom || !geom.rig || !drag.axis) return;
  const axis = drag.axis === "y" ? [0, 1, 0] : (drag.axis === "z" ? [0, 0, 1] : [1, 0, 0]);
  const spun = mulQuat(drag.quat, quatFromAxis(axis, dx * 0.01));
  const pose = Object.assign({}, meshCamera.pose, { [name]: normalizeQuat(spun) });
  clampHinges(geom, pose);
  meshCamera.pose = pose;
  applyPose(geom);
  redraw(canvas);
}

function boneEuler(geom, name) {
  if (!geom || !geom.rig || !geom.humanoid || geom.humanoid[name] == null) return { x: 0, y: 0, z: 0 };
  const bind = geom.rig.nodes[geom.humanoid[name]].r;
  const local = (meshCamera.pose && meshCamera.pose[name]) || bind;
  const delta = mulQuat(invQuat(bind), local);
  const euler = quatToEuler(normalizeQuat(delta));
  return { x: Math.round(euler[0]), y: Math.round(euler[1]), z: Math.round(euler[2]) };
}

function setPoseAxis(axis, degrees) {
  const canvas = document.querySelector("canvas[data-mesh-id]");
  const geom = canvas && canvas._geom;
  const name = meshCamera.selected;
  if (!geom || !geom.rig || !name || geom.humanoid[name] == null) return;
  const bind = geom.rig.nodes[geom.humanoid[name]].r;
  const euler = boneEuler(geom, name);
  euler[axis] = Number(degrees) || 0;
  const pose = Object.assign({}, meshCamera.pose, { [name]: normalizeQuat(mulQuat(bind, eulerQuat(euler.x, euler.y, euler.z))) });
  clampHinges(geom, pose);
  meshCamera.pose = pose;
  applyPose(geom);
  redraw(canvas);
}

function refreshMeshPose() {
  const canvas = document.querySelector("canvas[data-mesh-id]");
  if (!canvas || !canvas._geom) return;
  if (canvas._geom.rig) applyPose(canvas._geom);
  else poseGeometry(canvas._geom);
  redraw(canvas);
}

function applyPosePreset(name) {
  const canvas = document.querySelector("canvas[data-mesh-id]");
  const geom = canvas && canvas._geom;
  if (!geom || !geom.rig) return {};
  meshCamera.pose = !name || name === "bind" ? {} : buildPreset(geom, name);
  applyPose(geom);
  redraw(canvas);
  return meshCamera.pose;
}

function meshPoseInfo() {
  const canvas = document.querySelector("canvas[data-mesh-id]");
  const geom = canvas && canvas._geom;
  if (!geom) return null;
  let minX = Infinity; let maxX = -Infinity; let minY = Infinity; let maxY = -Infinity;
  for (const point of geom.positions) {
    minX = Math.min(minX, point[0]); maxX = Math.max(maxX, point[0]);
    minY = Math.min(minY, point[1]); maxY = Math.max(maxY, point[1]);
  }
  return {
    rig: Boolean(geom.rig),
    bones: (geom.boneList || []).length,
    pose: Object.keys(meshCamera.pose || {}).length,
    shape: meshCamera.shape || "",
    selected: meshCamera.selected || "",
    poseMode: Boolean(meshCamera.poseMode),
    minX, maxX, minY, maxY,
    ready: canvas.dataset.ready || "",
  };
}

function collectClips(document, bin) {
  const clips = [];
  for (const anim of document.animations || []) {
    if (!anim) continue;
    const channels = [];
    let duration = 0;
    const samplers = anim.samplers || [];
    for (const channel of anim.channels || []) {
      const target = (channel && channel.target) || {};
      const path = target.path;
      if (!["translation", "rotation", "scale"].includes(path) || !Number.isInteger(target.node)) continue;
      const sampler = samplers[channel.sampler];
      if (!sampler) continue;
      let interpolation = sampler.interpolation || "LINEAR";
      if (!["LINEAR", "STEP", "CUBICSPLINE"].includes(interpolation)) interpolation = "LINEAR";
      let times;
      let values;
      try {
        times = readFloats(document, bin, sampler.input).map((row) => row[0]);
        values = readFloats(document, bin, sampler.output);
      } catch (_error) {
        continue;
      }
      if (!times.length || !values.length) continue;
      if ((interpolation === "CUBICSPLINE" ? times.length * 3 : times.length) !== values.length) continue;
      duration = Math.max(duration, times[times.length - 1]);
      channels.push({ node: target.node, path, interpolation, times, values });
    }
    if (!channels.length) continue;
    clips.push({ name: String(anim.name || "").trim() || `Clip ${clips.length + 1}`, duration, channels });
  }
  return clips;
}

function readFloats(document, bin, index) {
  const accessor = document.accessors && document.accessors[index];
  const view = accessor ? document.bufferViews[accessor.bufferView] : null;
  const width = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 }[accessor && accessor.type];
  if (!accessor || !view || accessor.componentType !== 5126 || !width || accessor.sparse) {
    throw new Error("That animation uses a format Koma cannot read.");
  }
  const start = (view.byteOffset || 0) + (accessor.byteOffset || 0);
  const stride = view.byteStride || width * 4;
  const data = new DataView(bin.buffer, bin.byteOffset, bin.byteLength);
  const values = [];
  for (let i = 0; i < accessor.count; i += 1) {
    const at = start + i * stride;
    const row = [];
    for (let k = 0; k < width; k += 1) row.push(data.getFloat32(at + k * 4, true));
    values.push(row);
  }
  return values;
}

function activeClip(geom) {
  const clips = geom && geom.clips;
  if (!clips || meshCamera.clip < 0 || meshCamera.clip >= clips.length) return null;
  return clips[meshCamera.clip];
}

function applyClipFrame(geom) {
  if (!geom || !geom.rig) return;
  const clip = activeClip(geom);
  geom._animLocals = clip ? sampleClip(clip, meshCamera.time || 0, meshCamera.loop !== false) : null;
  applyPose(geom);
}

function installAnimationClips(clips) {
  const canvas = document.querySelector("canvas[data-mesh-id]");
  const geom = canvas && canvas._geom;
  if (!geom || !geom.rig) return 0;
  const imported = (clips || []).map((clip, index) => ({
    name: String((clip && clip.name) || `Imported ${index + 1}`),
    duration: Number(clip && clip.duration) || 0,
    channels: (clip && clip.channels) || [],
    imported: true,
  })).filter((clip) => clip.channels.length);
  geom.clips = (geom.clips || []).filter((clip) => !clip.imported).concat(imported);
  if (!imported.length) return 0;
  meshCamera.clip = geom.clips.length - imported.length;
  meshCamera.time = 0;
  meshCamera.playing = false;
  applyClipFrame(geom);
  if (canvas) redraw(canvas);
  return imported.length;
}

function sampleClip(clip, time, loop) {
  const duration = Number(clip.duration) || 0;
  const at = clipTime(duration, Number(time) || 0, loop !== false);
  const posed = {};
  for (const channel of clip.channels || []) {
    const sampled = sampleChannel(channel.times, channel.values, at, channel.interpolation || "LINEAR");
    if (!sampled.length) continue;
    const slot = posed[channel.node] || (posed[channel.node] = {});
    if (channel.path === "translation") slot.t = sampled.slice(0, 3);
    else if (channel.path === "rotation") slot.r = normalizeQuat(sampled.slice(0, 4));
    else if (channel.path === "scale") slot.s = sampled.slice(0, 3);
  }
  return posed;
}

function clipTime(duration, time, loop) {
  if (!(duration > 0)) return 0;
  if (loop) {
    if (time < 0) return duration + (time % duration);
    if (time > duration) return time % duration;
    return time;
  }
  return Math.max(0, Math.min(duration, time));
}

function sampleChannel(times, values, time, interpolation) {
  if (!times || !times.length || !values || !values.length) return [];
  const mode = interpolation || "LINEAR";
  const quat = keyValue(values, 0, mode).length === 4;
  const finish = (row) => (quat ? normalizeQuat(row) : row.slice());
  if (time <= times[0]) return finish(keyValue(values, 0, mode));
  if (time >= times[times.length - 1]) return finish(keyValue(values, times.length - 1, mode));
  let hi = 1;
  while (hi < times.length && times[hi] < time) hi += 1;
  const lo = hi - 1;
  const span = times[hi] - times[lo];
  const local = span <= 1e-8 ? 0 : (time - times[lo]) / span;
  if (mode === "STEP") return finish(keyValue(values, lo, mode));
  if (mode === "CUBICSPLINE") {
    const row = cubicPoint(values, lo, local, span);
    return quat ? normalizeQuat(row) : row;
  }
  const start = keyValue(values, lo, mode);
  const end = keyValue(values, hi, mode);
  if (start.length === 4 && end.length === 4) return quatSlerp(start, end, local);
  return start.map((value, index) => value + (end[index] - value) * local);
}

function keyValue(values, index, interpolation) {
  if (interpolation === "CUBICSPLINE") return values[index * 3 + 1].slice();
  return values[index].slice();
}

function cubicPoint(values, lo, t, dt) {
  const start = values[lo * 3 + 1];
  const outTangent = values[lo * 3 + 2];
  const end = values[(lo + 1) * 3 + 1];
  const inTangent = values[(lo + 1) * 3];
  const t2 = t * t;
  const t3 = t2 * t;
  const h00 = 2 * t3 - 3 * t2 + 1;
  const h10 = t3 - 2 * t2 + t;
  const h01 = -2 * t3 + 3 * t2;
  const h11 = t3 - t2;
  return start.map((value, index) => h00 * value + h10 * dt * outTangent[index] + h01 * end[index] + h11 * dt * inTangent[index]);
}

function quatSlerp(a, b, t) {
  const start = normalizeQuat(a);
  let end = normalizeQuat(b);
  let dot = start[0] * end[0] + start[1] * end[1] + start[2] * end[2] + start[3] * end[3];
  if (dot < 0) {
    end = [-end[0], -end[1], -end[2], -end[3]];
    dot = -dot;
  }
  if (dot > 0.9995) return normalizeQuat(start.map((value, index) => value + (end[index] - value) * t));
  const theta = Math.acos(Math.min(1, dot));
  const sine = Math.sin(theta);
  if (Math.abs(sine) < 1e-8) return start;
  const w1 = Math.sin((1 - t) * theta) / sine;
  const w2 = Math.sin(t * theta) / sine;
  return start.map((value, index) => value * w1 + end[index] * w2);
}

function posedExportPayload(geom) {
  const positions = [];
  const uvs = [];
  for (const point of geom.positions) positions.push(point[0], point[1], point[2]);
  for (const uv of geom.uvs || []) uvs.push(uv[0], uv[1]);
  const used = new Map();
  const images = [];
  const ranges = (geom.ranges || []).map((range) => {
    const mat = materialAt(geom, range.material);
    const color = (mat && mat.color) || [0.78, 0.78, 0.8];
    let image = -1;
    const source = geom.images && mat ? geom.images[mat.imageIndex] : null;
    const bytes = source && source.bytes;
    if (bytes && bytes.length && bytes.length <= 2000000) {
      if (!used.has(mat.imageIndex)) {
        used.set(mat.imageIndex, images.length);
        let raw = "";
        const chunk = 0x8000;
        for (let offset = 0; offset < bytes.length; offset += chunk) {
          raw += String.fromCharCode.apply(null, bytes.subarray(offset, offset + chunk));
        }
        images.push(btoa(raw));
      }
      image = used.get(mat.imageIndex);
    }
    return { start: range.start, count: range.count, color: [color[0], color[1], color[2]], image };
  });
  return { positions, indices: geom.indices, uvs, ranges, images };
}

function meshAnimInfo() {
  const canvas = document.querySelector("canvas[data-mesh-id]");
  const geom = canvas && canvas._geom;
  const clip = geom && activeClip(geom);
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const point of (geom && geom.positions) || []) {
    minX = Math.min(minX, point[0]);
    maxX = Math.max(maxX, point[0]);
    minY = Math.min(minY, point[1]);
    maxY = Math.max(maxY, point[1]);
  }
  return {
    clips: ((geom && geom.clips) || []).map((item) => item.name),
    clip: meshCamera.clip,
    time: meshCamera.time || 0,
    playing: Boolean(meshCamera.playing),
    duration: clip ? clip.duration : 0,
    rig: Boolean(geom && geom.rig),
    minX, maxX, minY, maxY,
  };
}

window.boneEuler = boneEuler;
window.setPoseAxis = setPoseAxis;
window.refreshMeshPose = refreshMeshPose;
window.applyPosePreset = applyPosePreset;
window.meshPoseInfo = meshPoseInfo;
window.sampleChannel = sampleChannel;
window.applyClipFrame = applyClipFrame;
window.installAnimationClips = installAnimationClips;
window.posedExportPayload = posedExportPayload;
window.meshAnimInfo = meshAnimInfo;

const POSE_ORDER = [
  "hips", "spine", "chest", "upperChest", "neck", "head",
  "leftShoulder", "leftUpperArm", "leftLowerArm", "leftHand",
  "rightShoulder", "rightUpperArm", "rightLowerArm", "rightHand",
  "leftUpperLeg", "leftLowerLeg", "leftFoot",
  "rightUpperLeg", "rightLowerLeg", "rightFoot",
];

const CHILD_CHAIN = {
  hips: ["spine", "chest", "upperChest", "neck", "head"],
  spine: ["chest", "upperChest", "neck", "head"],
  chest: ["upperChest", "neck", "head"],
  upperChest: ["neck", "head"],
  neck: ["head"],
  leftShoulder: ["leftUpperArm", "leftLowerArm", "leftHand"],
  leftUpperArm: ["leftLowerArm", "leftHand"],
  leftLowerArm: ["leftHand"],
  rightShoulder: ["rightUpperArm", "rightLowerArm", "rightHand"],
  rightUpperArm: ["rightLowerArm", "rightHand"],
  rightLowerArm: ["rightHand"],
  leftUpperLeg: ["leftLowerLeg", "leftFoot", "leftToes"],
  leftLowerLeg: ["leftFoot", "leftToes"],
  leftFoot: ["leftToes"],
  rightUpperLeg: ["rightLowerLeg", "rightFoot", "rightToes"],
  rightLowerLeg: ["rightFoot", "rightToes"],
  rightFoot: ["rightToes"],
};

function mix(axes, parts) {
  const acc = [0, 0, 0];
  for (const part of parts) {
    const axis = axes[part[0]];
    acc[0] += axis[0] * part[1];
    acc[1] += axis[1] * part[1];
    acc[2] += axis[2] * part[1];
  }
  return norm(acc);
}

function presetTargets(name) {
  const down = (axes) => mix(axes, [["up", -1]]);
  const table = {
    relaxed: {
      leftUpperArm: (axes) => mix(axes, [["up", -0.92], ["right", -0.28], ["forward", 0.18]]),
      rightUpperArm: (axes) => mix(axes, [["up", -0.92], ["right", 0.28], ["forward", 0.18]]),
      leftLowerArm: (axes) => mix(axes, [["up", -0.78], ["right", -0.1], ["forward", 0.5]]),
      rightLowerArm: (axes) => mix(axes, [["up", -0.78], ["right", 0.1], ["forward", 0.5]]),
      leftUpperLeg: down, rightUpperLeg: down, leftLowerLeg: down, rightLowerLeg: down,
    },
    walk: {
      leftUpperLeg: (axes) => mix(axes, [["up", -0.7], ["forward", 0.7]]),
      rightUpperLeg: (axes) => mix(axes, [["up", -0.86], ["forward", -0.5]]),
      leftLowerLeg: (axes) => mix(axes, [["up", -0.92], ["forward", -0.35]]),
      rightLowerLeg: (axes) => mix(axes, [["up", -0.35], ["forward", -0.94]]),
      leftUpperArm: (axes) => mix(axes, [["up", -0.72], ["forward", -0.58], ["right", -0.22]]),
      rightUpperArm: (axes) => mix(axes, [["up", -0.68], ["forward", 0.62], ["right", 0.22]]),
      leftLowerArm: (axes) => mix(axes, [["up", -0.35], ["forward", 0.2], ["right", -0.15]]),
      rightLowerArm: (axes) => mix(axes, [["up", -0.45], ["forward", 0.75]]),
    },
    run: {
      spine: (axes) => mix(axes, [["up", 0.84], ["forward", 0.54]]),
      chest: (axes) => mix(axes, [["up", 0.8], ["forward", 0.6]]),
      leftUpperLeg: (axes) => mix(axes, [["up", -0.35], ["forward", 0.92]]),
      rightUpperLeg: (axes) => mix(axes, [["up", -0.55], ["forward", -0.8]]),
      leftLowerLeg: (axes) => mix(axes, [["up", -0.7], ["forward", -0.7]]),
      rightLowerLeg: (axes) => mix(axes, [["up", 0.15], ["forward", -0.97]]),
      leftUpperArm: (axes) => mix(axes, [["up", -0.4], ["forward", -0.85], ["right", -0.2]]),
      rightUpperArm: (axes) => mix(axes, [["up", -0.25], ["forward", 0.85], ["right", 0.25]]),
      leftLowerArm: (axes) => mix(axes, [["up", -0.55], ["forward", -0.75]]),
      rightLowerArm: (axes) => mix(axes, [["up", -0.1], ["forward", 0.65]]),
    },
    sit: {
      spine: (axes) => mix(axes, [["up", 0.96], ["forward", 0.22]]),
      leftUpperLeg: (axes) => mix(axes, [["forward", 0.97], ["up", -0.22]]),
      rightUpperLeg: (axes) => mix(axes, [["forward", 0.97], ["up", -0.22]]),
      leftLowerLeg: down, rightLowerLeg: down,
      leftFoot: (axes) => mix(axes, [["forward", 0.96], ["up", -0.28]]),
      rightFoot: (axes) => mix(axes, [["forward", 0.96], ["up", -0.28]]),
      leftUpperArm: (axes) => mix(axes, [["up", -0.9], ["forward", 0.28], ["right", -0.16]]),
      rightUpperArm: (axes) => mix(axes, [["up", -0.9], ["forward", 0.28], ["right", 0.16]]),
      leftLowerArm: (axes) => mix(axes, [["up", -0.78], ["forward", 0.55], ["right", 0.22]]),
      rightLowerArm: (axes) => mix(axes, [["up", -0.78], ["forward", 0.55], ["right", -0.22]]),
    },
    point: {
      rightUpperArm: (axes) => mix(axes, [["forward", 0.82], ["up", 0.2], ["right", 0.4]]),
      rightLowerArm: (axes) => mix(axes, [["forward", 0.96], ["up", 0.12], ["right", 0.15]]),
      leftUpperArm: (axes) => mix(axes, [["up", -0.9], ["right", -0.3], ["forward", 0.12]]),
      leftLowerArm: (axes) => mix(axes, [["up", -0.75], ["forward", 0.45]]),
      head: (axes) => mix(axes, [["up", 0.96], ["right", 0.22], ["forward", 0.12]]),
    },
    cross: {
      leftUpperArm: (axes) => mix(axes, [["right", 0.72], ["forward", 0.55], ["up", -0.2]]),
      rightUpperArm: (axes) => mix(axes, [["right", -0.72], ["forward", 0.55], ["up", -0.2]]),
      leftLowerArm: (axes) => mix(axes, [["right", 0.9], ["up", 0.12], ["forward", 0.2]]),
      rightLowerArm: (axes) => mix(axes, [["right", -0.9], ["up", 0.12], ["forward", 0.2]]),
    },
    fight: {
      spine: (axes) => mix(axes, [["up", 0.92], ["forward", 0.28]]),
      leftUpperLeg: (axes) => mix(axes, [["up", -0.55], ["forward", 0.78]]),
      rightUpperLeg: (axes) => mix(axes, [["up", -0.88], ["forward", -0.35]]),
      leftLowerLeg: (axes) => mix(axes, [["up", -0.98], ["forward", 0.12]]),
      rightLowerLeg: (axes) => mix(axes, [["up", -0.55], ["forward", -0.8]]),
      leftUpperArm: (axes) => mix(axes, [["up", -0.55], ["forward", 0.48], ["right", -0.68]]),
      rightUpperArm: (axes) => mix(axes, [["up", -0.5], ["forward", 0.48], ["right", 0.7]]),
      leftLowerArm: (axes) => mix(axes, [["up", 0.58], ["forward", 0.62], ["right", 0.48]]),
      rightLowerArm: (axes) => mix(axes, [["up", 0.58], ["forward", 0.62], ["right", -0.48]]),
    },
  };
  return table[name] || null;
}

function buildPreset(geom, name) {
  const targets = presetTargets(name);
  if (!targets) return {};
  const bindWorlds = boneWorlds(geom, {});
  const axes = modelAxes(geom, bindWorlds);
  const pose = {};
  for (const bone of POSE_ORDER) {
    if (geom.humanoid[bone] == null || !targets[bone]) continue;
    const node = geom.humanoid[bone];
    const childName = (CHILD_CHAIN[bone] || []).find((item) => geom.humanoid[item] != null);
    const dir = boneDirection(geom, bindWorlds, bone, childName);
    if (!dir) continue;
    const bindRot = quatFromMatrix(bindWorlds[node]);
    const localDir = rotateVec(invQuat(bindRot), dir);
    const worlds = boneWorlds(geom, pose);
    const parent = geom.rig.parent[node];
    const parentRot = parent != null && parent >= 0 ? quatFromMatrix(worlds[parent]) : [0, 0, 0, 1];
    const worldRot = mulQuat(parentRot, geom.rig.nodes[node].r);
    const worldDir = rotateVec(worldRot, localDir);
    let aimed = mulQuat(quatFromTo(worldDir, targets[bone](axes)), worldRot);
    const reference = stableAxis(dir, axes);
    const localSide = rotateVec(invQuat(bindRot), projectPerp(axes[reference], dir));
    aimed = applyTwist(aimed, localDir, localSide, axes[reference]);
    pose[bone] = normalizeQuat(mulQuat(invQuat(parentRot), aimed));
  }
  clampHinges(geom, pose);
  return pose;
}

function modelAxes(geom, worlds) {
  const pos = (name) => (geom.humanoid[name] == null ? null : [worlds[geom.humanoid[name]][12], worlds[geom.humanoid[name]][13], worlds[geom.humanoid[name]][14]]);
  const hips = pos("hips");
  const head = pos("head") || pos("neck") || pos("chest") || pos("spine");
  let up = hips && head ? norm(subVec(head, hips)) : [0, 1, 0];
  const left = pos("leftUpperArm") || pos("leftShoulder") || pos("leftUpperLeg");
  const right = pos("rightUpperArm") || pos("rightShoulder") || pos("rightUpperLeg");
  let rightDir = left && right ? subVec(right, left) : [1, 0, 0];
  rightDir = subVec(rightDir, scaleVec(up, dot(rightDir, up)));
  rightDir = Math.hypot(rightDir[0], rightDir[1], rightDir[2]) < 1e-4 ? [1, 0, 0] : norm(rightDir);
  // Character forward is up × right. right × up points out the back, which folded knees backward.
  return { up, right: rightDir, forward: norm(cross(up, rightDir)) };
}

// Knees fold toward the back of the thigh. Elbows fold toward the front of the upper arm.
// Degrees from straight: negative is hyperextension.
const POSE_HINGES = [
  { bone: "leftUpperLeg", straight: "down", pole: "forward", min: -65, max: 115 },
  { bone: "rightUpperLeg", straight: "down", pole: "forward", min: -65, max: 115 },
  { bone: "leftLowerLeg", parent: "leftUpperLeg", pole: "back", min: 0, max: 150 },
  { bone: "rightLowerLeg", parent: "rightUpperLeg", pole: "back", min: 0, max: 150 },
  { bone: "leftLowerArm", parent: "leftUpperArm", pole: "forward", min: 0, max: 150 },
  { bone: "rightLowerArm", parent: "rightUpperArm", pole: "forward", min: 0, max: 150 },
];

function clampHinges(geom, pose) {
  if (!geom || !geom.rig || !geom.humanoid || !pose) return pose;
  const bindWorlds = boneWorlds(geom, {});
  const axes = modelAxes(geom, bindWorlds);
  for (const hinge of POSE_HINGES) {
    if (geom.humanoid[hinge.bone] == null) continue;
    if (hinge.parent && geom.humanoid[hinge.parent] == null) continue;
    const worlds = boneWorlds(geom, pose);
    const childName = (CHILD_CHAIN[hinge.bone] || []).find((item) => geom.humanoid[item] != null);
    const childDir = boneDirection(geom, worlds, hinge.bone, childName);
    if (!childDir) continue;
    let straight = null;
    let pole = null;
    if (hinge.parent) {
      straight = boneDirection(geom, worlds, hinge.parent, hinge.bone);
      pole = carriedPole(geom, bindWorlds, worlds, hinge.parent, axes, hinge.pole);
    } else {
      straight = hinge.straight === "down" ? scaleVec(axes.up, -1) : axes[hinge.straight];
      pole = axes[hinge.pole];
    }
    if (!straight || !pole) continue;
    const angle = flexionDegrees(straight, childDir, pole);
    const limited = Math.max(hinge.min, Math.min(hinge.max, angle));
    if (Math.abs(limited - angle) < 0.75) continue;
    const corrected = directionAtFlexion(straight, pole, limited * Math.PI / 180);
    const node = geom.humanoid[hinge.bone];
    const bindDir = boneDirection(geom, bindWorlds, hinge.bone, childName);
    if (!bindDir) continue;
    const bindRot = quatFromMatrix(bindWorlds[node]);
    const localDir = rotateVec(invQuat(bindRot), bindDir);
    const parent = geom.rig.parent[node];
    const parentRot = parent != null && parent >= 0 ? quatFromMatrix(worlds[parent]) : [0, 0, 0, 1];
    const worldRot = quatFromMatrix(worlds[node]);
    const worldDir = rotateVec(worldRot, localDir);
    const aimed = mulQuat(quatFromTo(worldDir, corrected), worldRot);
    pose[hinge.bone] = normalizeQuat(mulQuat(invQuat(parentRot), aimed));
  }
  return pose;
}

function carriedPole(geom, bindWorlds, worlds, parentBone, axes, which) {
  const node = geom.humanoid[parentBone];
  const basis = which === "back" ? scaleVec(axes.forward, -1) : axes.forward;
  const local = rotateVec(invQuat(quatFromMatrix(bindWorlds[node])), basis);
  return rotateVec(quatFromMatrix(worlds[node]), local);
}

function flexionDegrees(straight, childDir, pole) {
  const bend = projectPerp(pole, straight);
  if (Math.hypot(bend[0], bend[1], bend[2]) < 1e-5) return 0;
  const bendN = norm(bend);
  const flat = projectPerp(childDir, straight);
  const cos = Math.max(-1, Math.min(1, dot(norm(straight), norm(childDir))));
  const angle = Math.acos(cos) * 180 / Math.PI;
  if (Math.hypot(flat[0], flat[1], flat[2]) < 1e-5) return 0;
  return dot(norm(flat), bendN) >= 0 ? angle : -angle;
}

function directionAtFlexion(straight, pole, radians) {
  const hinge = cross(norm(straight), norm(projectPerp(pole, straight)));
  if (Math.hypot(hinge[0], hinge[1], hinge[2]) < 1e-5 || Math.abs(radians) < 1e-4) return norm(straight);
  return norm(rotateVec(quatFromAxis(norm(hinge), radians), norm(straight)));
}

function stableAxis(dir, axes) {
  if (Math.hypot.apply(null, projectPerp(axes.right, dir)) > 0.35) return "right";
  if (Math.hypot.apply(null, projectPerp(axes.up, dir)) > 0.35) return "up";
  return "forward";
}

function projectPerp(vector, axis) {
  const unit = norm(axis);
  return subVec(vector, scaleVec(unit, dot(vector, unit)));
}

function applyTwist(worldRot, localDir, localSide, desiredSide) {
  const boneDir = norm(rotateVec(worldRot, localDir));
  const sideNow = rotateVec(worldRot, localSide);
  const sideFlat = projectPerp(sideNow, boneDir);
  const desired = projectPerp(desiredSide, boneDir);
  const sideLen = Math.hypot(sideFlat[0], sideFlat[1], sideFlat[2]);
  const desiredLen = Math.hypot(desired[0], desired[1], desired[2]);
  if (sideLen < 1e-4 || desiredLen < 1e-4) return worldRot;
  const sideN = scaleVec(sideFlat, 1 / sideLen);
  const desiredN = scaleVec(desired, 1 / desiredLen);
  const cos = Math.max(-1, Math.min(1, dot(sideN, desiredN)));
  const sin = dot(cross(sideN, desiredN), boneDir);
  const angle = Math.atan2(sin, cos);
  if (Math.abs(angle) < 1e-4) return worldRot;
  return mulQuat(quatFromAxis(boneDir, angle), worldRot);
}

function boneDirection(geom, worlds, name, childName) {
  const node = geom.humanoid[name];
  const from = [worlds[node][12], worlds[node][13], worlds[node][14]];
  let to = null;
  if (childName && geom.humanoid[childName] != null) {
    const child = geom.humanoid[childName];
    to = [worlds[child][12], worlds[child][13], worlds[child][14]];
  } else {
    const child = geom.rig.parent.findIndex((parent) => parent === node);
    if (child >= 0) to = [worlds[child][12], worlds[child][13], worlds[child][14]];
  }
  if (!to) return null;
  const delta = subVec(to, from);
  if (Math.hypot(delta[0], delta[1], delta[2]) < 1e-5) return null;
  return norm(delta);
}

function subVec(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
function scaleVec(a, scale) { return [a[0] * scale, a[1] * scale, a[2] * scale]; }

function mulQuat(a, b) {
  return [
    a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1],
    a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
    a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3],
    a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2],
  ];
}

function invQuat(q) {
  return normalizeQuat([-q[0], -q[1], -q[2], q[3]]);
}

function normalizeQuat(q) {
  const length = Math.hypot(q[0], q[1], q[2], q[3]) || 1;
  return [q[0] / length, q[1] / length, q[2] / length, q[3] / length];
}

function quatFromAxis(axis, radians) {
  const half = radians / 2;
  const scale = Math.sin(half);
  return [axis[0] * scale, axis[1] * scale, axis[2] * scale, Math.cos(half)];
}

function quatFromTo(from, to) {
  const a = norm(from);
  const b = norm(to);
  const same = dot(a, b);
  if (same > 0.9999) return [0, 0, 0, 1];
  if (same < -0.9999) {
    let axis = cross(a, [1, 0, 0]);
    if (Math.hypot(axis[0], axis[1], axis[2]) < 1e-4) axis = cross(a, [0, 1, 0]);
    return quatFromAxis(norm(axis), Math.PI);
  }
  const turned = cross(a, b);
  return normalizeQuat([turned[0], turned[1], turned[2], 1 + same]);
}

function rotateVec(q, v) {
  const spun = mulQuat(mulQuat(q, [v[0], v[1], v[2], 0]), invQuat(q));
  return [spun[0], spun[1], spun[2]];
}

function quatFromMatrix(matrix) {
  const x = norm([matrix[0], matrix[1], matrix[2]]);
  let y = [matrix[4], matrix[5], matrix[6]];
  let z = norm(cross(x, y));
  y = norm(cross(z, x));
  const m00 = x[0]; const m10 = x[1]; const m20 = x[2];
  const m01 = y[0]; const m11 = y[1]; const m21 = y[2];
  const m02 = z[0]; const m12 = z[1]; const m22 = z[2];
  const trace = m00 + m11 + m22;
  let q;
  if (trace > 0) {
    const s = Math.sqrt(trace + 1) * 2;
    q = [(m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, 0.25 * s];
  } else if (m00 > m11 && m00 > m22) {
    const s = Math.sqrt(1 + m00 - m11 - m22) * 2;
    q = [0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s];
  } else if (m11 > m22) {
    const s = Math.sqrt(1 + m11 - m00 - m22) * 2;
    q = [(m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s];
  } else {
    const s = Math.sqrt(1 + m22 - m00 - m11) * 2;
    q = [(m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s];
  }
  return normalizeQuat(q);
}

function eulerQuat(x, y, z) {
  const qx = quatFromAxis([1, 0, 0], x * Math.PI / 180);
  const qy = quatFromAxis([0, 1, 0], y * Math.PI / 180);
  const qz = quatFromAxis([0, 0, 1], z * Math.PI / 180);
  return mulQuat(mulQuat(qz, qy), qx);
}

function quatToEuler(q) {
  const matrix = nodeMatrix({ rotation: q });
  const sy = Math.max(-1, Math.min(1, -matrix[2]));
  const pitch = Math.asin(sy);
  const cy = Math.cos(pitch);
  let roll;
  let yaw;
  if (Math.abs(cy) > 1e-4) {
    roll = Math.atan2(matrix[6], matrix[10]);
    yaw = Math.atan2(matrix[1], matrix[0]);
  } else {
    roll = Math.atan2(-matrix[9], matrix[5]);
    yaw = 0;
  }
  return [roll * 180 / Math.PI, pitch * 180 / Math.PI, yaw * 180 / Math.PI];
}
