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

function showMesh(canvas, geom) {
  canvas._geom = geom;
  canvas._orbit = { yaw: 0.7, pitch: 0.42, dist: 3.2 };
  bindOrbit(canvas);
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

function bindOrbit(canvas) {
  canvas.addEventListener("pointerdown", (event) => {
    canvas._drag = {
      x: event.clientX,
      y: event.clientY,
      yaw: canvas._orbit.yaw,
      pitch: canvas._orbit.pitch,
    };
    try { canvas.setPointerCapture(event.pointerId); } catch (_error) { /* already gone */ }
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!canvas._drag) return;
    canvas._orbit.yaw = canvas._drag.yaw + (event.clientX - canvas._drag.x) * 0.01;
    const pitch = canvas._drag.pitch + (event.clientY - canvas._drag.y) * 0.01;
    canvas._orbit.pitch = Math.max(-1.2, Math.min(1.2, pitch));
    redraw(canvas);
  });
  const end = () => { canvas._drag = null; };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const step = event.deltaY > 0 ? 0.28 : -0.28;
    canvas._orbit.dist = Math.max(1.5, Math.min(9, canvas._orbit.dist + step));
    redraw(canvas);
  }, { passive: false });
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
  return [x1, point[1] * cp - z1 * sp, point[1] * sp + z1 * cp];
}

function projectPoint(point, orbit, width, height) {
  let x = point[0];
  let y = point[1];
  let z = point[2];
  const cy = Math.cos(orbit.yaw);
  const sy = Math.sin(orbit.yaw);
  const x1 = x * cy + z * sy;
  const z1 = -x * sy + z * cy;
  const cp = Math.cos(orbit.pitch);
  const sp = Math.sin(orbit.pitch);
  const y2 = y * cp - z1 * sp;
  const z2 = y * sp + z1 * cp;
  const depth = z2 + orbit.dist;
  const scale = (Math.min(width, height) * 0.72) / Math.max(0.2, depth);
  return { x: width / 2 + x1 * scale, y: height / 2 - y2 * scale, z: z2 };
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
  for (const face of faces) {
    const shade = Math.round(40 + 186 * face.light);
    ctx.beginPath();
    ctx.moveTo(face.a.x, face.a.y);
    ctx.lineTo(face.b.x, face.b.y);
    ctx.lineTo(face.c.x, face.c.y);
    ctx.closePath();
    ctx.fillStyle = `rgb(${shade}, ${Math.round(shade * 0.28)}, ${Math.round(shade * 0.22)})`;
    ctx.fill();
  }
}

function glProgram(gl) {
  const vs = `
    attribute vec3 aPos;
    uniform mat4 uMvp;
    varying vec3 vPos;
    void main() {
      vPos = aPos;
      gl_Position = uMvp * vec4(aPos, 1.0);
    }
  `;
  const fs = `
    precision mediump float;
    varying vec3 vPos;
    void main() {
      float shade = clamp(0.35 + vPos.y * 0.35 + vPos.x * 0.15, 0.25, 1.0);
      gl_FragColor = vec4(shade, shade * 0.28, shade * 0.22, 1.0);
    }
  `;
  const program = link(gl, vs, fs);
  if (!program) return false;
  gl._program = program;
  gl._pos = gl.getAttribLocation(program, "aPos");
  gl._mvp = gl.getUniformLocation(program, "uMvp");
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
  if (!gl._buffer) {
    const data = new Float32Array(geom.indices.length * 3);
    for (let i = 0; i < geom.indices.length; i += 1) {
      const point = geom.positions[geom.indices[i]];
      data[i * 3] = point[0];
      data[i * 3 + 1] = point[1];
      data[i * 3 + 2] = point[2];
    }
    gl._count = geom.indices.length;
    gl._buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, gl._buffer);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
  }
  gl.useProgram(gl._program);
  gl.bindBuffer(gl.ARRAY_BUFFER, gl._buffer);
  gl.enableVertexAttribArray(gl._pos);
  gl.vertexAttribPointer(gl._pos, 3, gl.FLOAT, false, 12, 0);
  gl.uniformMatrix4fv(gl._mvp, false, new Float32Array(mvp(canvas._orbit, canvas.width / canvas.height)));
  gl.drawArrays(gl.TRIANGLES, 0, gl._count);
}

function mvp(orbit, aspect) {
  const model = mul(rotX(orbit.pitch), rotY(orbit.yaw));
  const view = lookAt([0, 0, orbit.dist], [0, 0, 0], [0, 1, 0]);
  const projection = perspective(0.7, aspect, 0.05, 40);
  return mul(projection, mul(view, model));
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
