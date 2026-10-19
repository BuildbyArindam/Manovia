/**
 * A tiny recording WebGL context for tests (Day 46).
 *
 * Only the calls the calm renderer makes are implemented, and the constants it
 * reads are numbers. A real browser and a real driver are out of reach in this
 * sandbox; this proves the draw path, the pinned triangle count, the constant
 * clear colour and the absence of per-frame allocation, not GPU behaviour.
 */

export interface GlRecord {
  calls: Array<{ name: string; args: unknown[] }>;
  shaders: string[];
  bufferBytes: number;
  draws: number;
}

const CONSTANTS = {
  VERTEX_SHADER: 35633,
  FRAGMENT_SHADER: 35632,
  COMPILE_STATUS: 35713,
  LINK_STATUS: 35714,
  ARRAY_BUFFER: 34962,
  STATIC_DRAW: 35044,
  FLOAT: 5126,
  COLOR_BUFFER_BIT: 16384,
  DEPTH_BUFFER_BIT: 256,
  FRAMEBUFFER: 36160,
  TRIANGLES: 4,
} as const;

export function createFakeGl(options: { failShaders?: boolean; failLink?: boolean } = {}): {
  gl: WebGLRenderingContext;
  record: GlRecord;
} {
  const record: GlRecord = { calls: [], shaders: [], bufferBytes: 0, draws: 0 };
  const shader = { kind: "shader" };
  const program = { kind: "program" };
  const buffer = { kind: "buffer" };

  const gl = {
    ...CONSTANTS,
    createShader: (type: number) => {
      record.calls.push({ name: "createShader", args: [type] });
      return shader;
    },
    shaderSource: (_shader: unknown, source: string) => {
      record.shaders.push(source);
    },
    compileShader: () => undefined,
    getShaderParameter: () => !options.failShaders,
    deleteShader: () => undefined,
    createProgram: () => program,
    attachShader: () => undefined,
    linkProgram: () => undefined,
    getProgramParameter: () => !options.failLink,
    deleteProgram: () => undefined,
    getAttribLocation: (_program: unknown, name: string) => {
      if (name === "aPosition") return 0;
      if (name === "aColor") return 1;
      if (name === "aOrb") return 2;
      return -1;
    },
    getUniformLocation: (_program: unknown, name: string) => ({ name }),
    createBuffer: () => buffer,
    bindBuffer: () => undefined,
    bufferData: (_target: number, data: Float32Array) => {
      record.bufferBytes = data.byteLength;
    },
    deleteBuffer: () => undefined,
    useProgram: () => undefined,
    enableVertexAttribArray: () => undefined,
    vertexAttribPointer: () => undefined,
    uniformMatrix4fv: () => undefined,
    uniform3f: () => undefined,
    uniform1f: () => undefined,
    viewport: () => undefined,
    bindFramebuffer: () => undefined,
    clearColor: (...args: unknown[]) => {
      record.calls.push({ name: "clearColor", args });
    },
    clear: (...args: unknown[]) => {
      record.calls.push({ name: "clear", args });
    },
    drawArrays: (...args: unknown[]) => {
      record.draws += 1;
      record.calls.push({ name: "drawArrays", args });
    },
  };

  return { gl: gl as unknown as WebGLRenderingContext, record };
}
