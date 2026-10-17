/* Local orbit viewer. No network, no CDN. Small meshes are drawn on a 2D canvas
   so they stay visible without a GPU. Larger meshes use WebGL when it is there. */

const meshBytes = new Map();

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
    let bytes = meshBytes.get(id);
    if (!bytes) {
      const result = await rpc("character.mesh_bytes", { id });
      bytes = decodeMesh(result.glb);
      meshBytes.set(id, bytes);
      if (meshBytes.size > 3) meshBytes.delete(meshBytes.keys().next().value);
    }
    if (!canvas.isConnected) return;
    showMesh(canvas, parseGlb(bytes.buffer));
  } catch (error) {
    if (note) note.textContent = (error && error.message) || "Could not show the model.";
  }
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
  const positions = [];
  const indices = [];
  for (const mesh of document.meshes || []) {
    for (const prim of mesh.primitives || []) {
      if (prim.mode != null && prim.mode !== 4) continue;
      const attr = prim.attributes || {};
      if (attr.POSITION == null) continue;
      const chunk = readVec3(document, bin, attr.POSITION);
      const local = prim.indices == null ? chunk.map((_, index) => index) : readScalar(document, bin, prim.indices);
      const base = positions.length;
      for (const point of chunk) positions.push(point);
      for (const index of local) indices.push(index + base);
    }
  }
  if (positions.length < 3 || indices.length < 3) {
    throw new Error("That model has no triangle mesh Koma can show.");
  }
  return { positions, indices };
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

const meshCamera = {
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
  centerGeometry(geom);
  const radius = frameRadius(geom);
  if (meshCamera.meshId !== id) {
    meshCamera.meshId = id;
    meshCamera.baseDist = Math.max(1.4, radius * 2.8);
    resetMeshCamera();
  }
  canvas._geom = geom;
  canvas._orbit = meshCamera;
  bindOrbit(canvas);
  ensureSpin();
  const triangles = geom.indices.length / 3;
  const gl = triangles > 12000 ? canvas.getContext("webgl", { preserveDrawingBuffer: true, antialias: true }) : null;
  if (gl && glProgram(gl)) {
    canvas._gl = gl;
    canvas.dataset.mode = "webgl";
    requestAnimationFrame(() => redraw(canvas));
    return;
  }
  canvas.dataset.mode = "canvas";
  requestAnimationFrame(() => redraw(canvas));
}

function centerGeometry(geom) {
  let cx = 0;
  let cy = 0;
  let cz = 0;
  for (const point of geom.positions) {
    cx += point[0];
    cy += point[1];
    cz += point[2];
  }
  const count = geom.positions.length || 1;
  cx /= count;
  cy /= count;
  cz /= count;
  geom.positions = geom.positions.map((point) => [point[0] - cx, point[1] - cy, point[2] - cz]);
}

function frameRadius(geom) {
  let radius = 0.001;
  for (const point of geom.positions) radius = Math.max(radius, Math.hypot(point[0], point[1], point[2]));
  return radius;
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
    if (canvas._gl) {
      canvas._gl._buffer = null;
      canvas._gl._lines = null;
      canvas._gl._program = null;
      glProgram(canvas._gl);
    }
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
  const pixel = ctx.getImageData(Math.floor(canvas.width / 2), Math.floor(canvas.height / 2), 1, 1).data;
  return [pixel[0], pixel[1], pixel[2]];
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
  return 0.32 + 0.68 * facing;
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

function drawCanvas(canvas) {
  const ctx = canvas.getContext("2d");
  const { positions, indices } = canvas._geom;
  const orbit = canvas._orbit;
  ctx.fillStyle = "#2a2a2c";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const projected = positions.map((point) => projectPoint(point, orbit, canvas.width, canvas.height));
  const stride = Math.max(1, Math.ceil(indices.length / 3 / 8000));
  const faces = [];
  for (let i = 0; i < indices.length; i += 3 * stride) {
    const a = projected[indices[i]];
    const b = projected[indices[i + 1]];
    const c = projected[indices[i + 2]];
    const pa = positions[indices[i]];
    const pb = positions[indices[i + 1]];
    const pc = positions[indices[i + 2]];
    if (!a || !b || !c || !pa || !pb || !pc) continue;
    const light = faceLight(pa, pb, pc, orbit);
    faces.push({ a, b, c, z: (a.z + b.z + c.z) / 3, light });
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
      const shade = lit ? Math.round(40 + 186 * face.light) : 214;
      ctx.fillStyle = `rgb(${shade}, ${Math.round(shade * 0.28)}, ${Math.round(shade * 0.22)})`;
      ctx.fill();
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
    uniform mat4 uMvp;
    uniform mat4 uModel;
    varying vec3 vNormal;
    void main() {
      vNormal = mat3(uModel) * aNormal;
      gl_Position = uMvp * vec4(aPos, 1.0);
    }
  `;
  const fs = `
    precision mediump float;
    uniform float uLight;
    varying vec3 vNormal;
    void main() {
      float shade = uLight > 0.5 ? clamp(abs(vNormal.z) * 0.75 + 0.28, 0.28, 1.0) : 0.86;
      gl_FragColor = vec4(shade, shade * 0.28, shade * 0.22, 1.0);
    }
  `;
  const program = link(gl, vs, fs);
  if (!program) return false;
  gl._program = program;
  gl._pos = gl.getAttribLocation(program, "aPos");
  gl._normal = gl.getAttribLocation(program, "aNormal");
  gl._mvp = gl.getUniformLocation(program, "uMvp");
  gl._model = gl.getUniformLocation(program, "uModel");
  gl._light = gl.getUniformLocation(program, "uLight");
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
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  if (!gl._buffer) buildGlBuffers(gl, geom);
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
  gl.uniform1f(gl._light, meshCamera.light ? 1 : 0);
  if (!meshCamera.wire || meshCamera.light) {
    gl.bindBuffer(gl.ARRAY_BUFFER, gl._buffer);
    gl.enableVertexAttribArray(gl._pos);
    gl.vertexAttribPointer(gl._pos, 3, gl.FLOAT, false, 24, 0);
    if (gl._normal >= 0) {
      gl.enableVertexAttribArray(gl._normal);
      gl.vertexAttribPointer(gl._normal, 3, gl.FLOAT, false, 24, 12);
    }
    gl.drawArrays(gl.TRIANGLES, 0, gl._count);
  }
  if (meshCamera.wire && gl._lines) {
    gl.uniform1f(gl._light, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl._lines);
    gl.enableVertexAttribArray(gl._pos);
    gl.vertexAttribPointer(gl._pos, 3, gl.FLOAT, false, 12, 0);
    if (gl._normal >= 0) gl.disableVertexAttribArray(gl._normal);
    gl.drawArrays(gl.LINES, 0, gl._lineCount);
  }
}

function buildGlBuffers(gl, geom) {
  const count = geom.indices.length;
  const interleaved = new Float32Array(count * 6);
  const lines = new Float32Array(count * 6);
  for (let i = 0; i < count; i += 3) {
    const a = geom.positions[geom.indices[i]];
    const b = geom.positions[geom.indices[i + 1]];
    const c = geom.positions[geom.indices[i + 2]];
    const normal = faceNormal(a, b, c);
    const tri = [a, b, c];
    for (let k = 0; k < 3; k += 1) {
      const at = (i + k) * 6;
      interleaved[at] = tri[k][0];
      interleaved[at + 1] = tri[k][1];
      interleaved[at + 2] = tri[k][2];
      interleaved[at + 3] = normal[0];
      interleaved[at + 4] = normal[1];
      interleaved[at + 5] = normal[2];
    }
    const edge = [a, b, b, c, c, a];
    for (let k = 0; k < 6; k += 1) {
      lines[(i * 2 + k) * 3] = edge[k][0];
      lines[(i * 2 + k) * 3 + 1] = edge[k][1];
      lines[(i * 2 + k) * 3 + 2] = edge[k][2];
    }
  }
  gl._count = count;
  gl._buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, gl._buffer);
  gl.bufferData(gl.ARRAY_BUFFER, interleaved, gl.STATIC_DRAW);
  gl._lineCount = count * 2;
  gl._lines = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, gl._lines);
  gl.bufferData(gl.ARRAY_BUFFER, lines, gl.STATIC_DRAW);
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
