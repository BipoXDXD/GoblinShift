// Chunk da cena 3D da abertura (ADR 027 e 030). O nome do arquivo vira o nome do chunk, e o
// check-budget reconhece o prefixo `assets/oficina-3d`: não renomeie sem mudar os dois.
import {
  Box3,
  Color,
  DirectionalLight,
  Group,
  LinearSRGBColorSpace,
  Mesh,
  NoToneMapping,
  PCFShadowMap,
  PerspectiveCamera,
  PMREMGenerator,
  Scene,
  SRGBColorSpace,
  Vector3,
  WebGLRenderer,
  type Material,
  type Object3D,
  type Texture,
} from "three";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

import modelUrl from "../assets/art/oficina-3d/oficina-3d-rubia.glb?url";
import type { MountOptions, SceneHandle } from "./OficinaHero";

const FIELD_OF_VIEW_DEGREES = 22;
/** Folga em volta da goblin, para o cabelo não encostar na borda do quadro. */
const FRAME_MARGIN = 1.06;
/** Entrada: gira de um lado para o outro e para; abaixo dos 5 s da WCAG 2.2.2, sem botão. */
const INTRO_MS = 3_500;
const INTRO_FROM_RADIANS = 0.9;
/** Mesmo ângulo da imagem estática (render a 20°), para a troca não dar salto. */
const REST_RADIANS = -0.35;
/** Arrastar 1 px gira isto; o arrasto é só horizontal, a rolagem vertical continua da página. */
const DRAG_RADIANS_PER_PX = 0.01;
const MAX_PIXEL_RATIO = 2;
/*
 * Luz do render realista que o usuário aprovou (render_glb.py --realista, ADR 030): três luzes de
 * área do Cycles (principal, preenchimento e recorte), mundo cinza e tom Standard, que no
 * three é sem tone mapping. Cada luz de área vira uma direcional com a mesma irradiância no centro
 * da goblin: potência P a 3 raios de distância dá P / (9π) por raio² (emissor lambertiano). Os
 * ângulos são os do Blender, em graus a partir da frente, girados junto com a pose de repouso
 * para a imagem estática (render a 20°) e a cena baterem.
 */
const LIGHTS: readonly { power: number; yawDegrees: number; pitchDegrees: number }[] = [
  { power: 250, yawDegrees: -40, pitchDegrees: 35 },
  { power: 80, yawDegrees: 60, pitchDegrees: 10 },
  { power: 200, yawDegrees: 170, pitchDegrees: 40 },
];
const LIGHT_DISTANCE_RADII = 3;
/*
 * Sem a luz indireta do Cycles, a conversão direta clareava a pele e achatava o volume. Ajuste
 * medido contra o render realista, na pele (matiz de 55° a 85°): matiz 65° contra 64,6° e brilho
 * (V do HSV) 0,78 contra 0,76. A pele continua verde, sem puxar para o amarelo.
 */
const LIGHT_SCALE = 0.6;
/** Mundo cinza, em RGB linear (0,25 no render realista): ilumina e reflete por igual. */
const WORLD_GRAY = 0.2;
/** Só a luz principal faz sombra (o cabelo no rosto, o braço no corpo): uma passada a mais. */
const KEY_LIGHT_INDEX = 0;
const SHADOW_MAP_PX = 2048;
/** Em unidades do modelo: tira as listras de autossombra sem descolar a sombra do cabelo. */
const SHADOW_NORMAL_BIAS = 0.005;
const SHADOW_BIAS = -0.0005;
const SHADOW_SOFTNESS = 3;

export async function mountScene(host: HTMLElement, options: MountOptions): Promise<SceneHandle> {
  // O decoder Meshopt é WASM embutido neste chunk: nada vem de CDN e não há Worker (ADR 030).
  const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
  const gltf = await loader.parseAsync(await fetchModel(options.signal), "");
  const renderer = new WebGLRenderer({
    antialias: true,
    alpha: true,
    powerPreference: "low-power",
    failIfMajorPerformanceCaveat: true,
  });
  renderer.outputColorSpace = SRGBColorSpace;
  renderer.toneMapping = NoToneMapping;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = PCFShadowMap;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, MAX_PIXEL_RATIO));
  renderer.domElement.className = "block size-full";

  const scene = new Scene();
  scene.environment = grayWorld(renderer);

  const turntable = new Group();
  turntable.add(gltf.scene);
  scene.add(turntable);
  const camera = new PerspectiveCamera(FIELD_OF_VIEW_DEGREES, 4 / 5, 0.01, 100);
  const radius = frameModel(gltf.scene, camera);
  addLights(scene, gltf.scene, radius);
  // Compila os shaders em paralelo (KHR_parallel_shader_compile) antes do primeiro quadro, para a
  // compilação não virar uma tarefa longa na thread principal.
  await renderer.compileAsync(scene, camera);

  const render = (): void => {
    renderer.render(scene, camera);
  };
  const resize = (): void => {
    const { clientWidth: width, clientHeight: height } = host;
    if (width === 0 || height === 0) {
      return;
    }
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    render();
  };

  const stage: Stage = { host, renderer, turntable, render };
  const stopIntro = playIntro(stage);
  const stopDrag = enableDrag(stage, stopIntro);
  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(host);
  const onLost = (event: Event): void => {
    event.preventDefault();
    options.onContextLost();
  };
  renderer.domElement.addEventListener("webglcontextlost", onLost);

  host.append(renderer.domElement);
  resize();

  return {
    dispose: () => {
      stopIntro();
      stopDrag();
      resizeObserver.disconnect();
      renderer.domElement.removeEventListener("webglcontextlost", onLost);
      disposeScene(scene);
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}

async function fetchModel(signal: AbortSignal): Promise<ArrayBuffer> {
  const response = await fetch(modelUrl, { signal });
  if (!response.ok) {
    throw new Error(`modelo da oficina: HTTP ${String(response.status)}`);
  }
  return response.arrayBuffer();
}

/** Mundo de cor única, como o do Blender: vira o mapa de ambiente que ilumina e reflete. */
function grayWorld(renderer: WebGLRenderer): Texture {
  const world = new Scene();
  world.background = new Color().setRGB(WORLD_GRAY, WORLD_GRAY, WORLD_GRAY, LinearSRGBColorSpace);
  const generator = new PMREMGenerator(renderer);
  const texture = generator.fromScene(world).texture;
  generator.dispose();
  return texture;
}

/** As três luzes do render realista, fixas no mundo; a principal faz sombra na própria goblin. */
function addLights(scene: Scene, model: Object3D, radius: number): void {
  const up = new Vector3(0, 1, 0);
  LIGHTS.forEach(({ power, yawDegrees, pitchDegrees }, index) => {
    const yaw = (yawDegrees * Math.PI) / 180;
    const pitch = (pitchDegrees * Math.PI) / 180;
    const light = new DirectionalLight(0xffffff, (LIGHT_SCALE * power) / (9 * Math.PI));
    light.position
      .set(Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch), Math.cos(yaw) * Math.cos(pitch))
      .applyAxisAngle(up, REST_RADIANS)
      .multiplyScalar(radius * LIGHT_DISTANCE_RADII);
    if (index === KEY_LIGHT_INDEX) {
      castShadowOver(light, radius);
    }
    scene.add(light);
  });
  model.traverse((object) => {
    object.castShadow = true;
    object.receiveShadow = true;
  });
}

function castShadowOver(light: DirectionalLight, radius: number): void {
  light.castShadow = true;
  light.shadow.mapSize.set(SHADOW_MAP_PX, SHADOW_MAP_PX);
  light.shadow.normalBias = SHADOW_NORMAL_BIAS;
  light.shadow.bias = SHADOW_BIAS;
  light.shadow.radius = SHADOW_SOFTNESS;
  const frustum = light.shadow.camera;
  frustum.left = -radius;
  frustum.bottom = -radius;
  frustum.right = radius;
  frustum.top = radius;
  frustum.near = radius;
  frustum.far = radius * LIGHT_DISTANCE_RADII * 2;
  frustum.updateProjectionMatrix();
}

/**
 * Centra a goblin na origem, de pé, e afasta a câmera até ela caber inteira no quadro 4:5.
 * Devolve o raio da caixa da goblin, que dá a escala das luzes e da sombra.
 */
function frameModel(model: Object3D, camera: PerspectiveCamera): number {
  const box = new Box3().setFromObject(model);
  const size = box.getSize(new Vector3());
  const center = box.getCenter(new Vector3());
  model.position.sub(center);
  const halfFov = (FIELD_OF_VIEW_DEGREES * Math.PI) / 360;
  const fitHeight = size.y / 2 / Math.tan(halfFov);
  const fitWidth = size.x / 2 / Math.tan(halfFov) / camera.aspect;
  const distance = Math.max(fitHeight, fitWidth) * FRAME_MARGIN + size.z / 2;
  camera.position.set(0, 0, distance);
  camera.lookAt(0, 0, 0);
  return size.length() / 2;
}

/** O que a entrada e o arrasto mexem: o prato giratório e o quadro desenhado no host. */
interface Stage {
  readonly host: HTMLElement;
  readonly renderer: WebGLRenderer;
  readonly turntable: Group;
  readonly render: () => void;
}

/** Toca a entrada uma vez, com ease-out, e para; marca `data-movimento` para o E2E. */
function playIntro({ host, renderer, turntable, render }: Stage): () => void {
  let start: number | null = null;
  host.dataset["movimento"] = "entrada";
  const stop = (): void => {
    renderer.setAnimationLoop(null);
    host.dataset["movimento"] = "parado";
  };
  turntable.rotation.y = INTRO_FROM_RADIANS;
  renderer.setAnimationLoop((now) => {
    start ??= now;
    const progress = Math.min(1, (now - start) / INTRO_MS);
    const eased = 1 - (1 - progress) ** 3;
    turntable.rotation.y = INTRO_FROM_RADIANS + (REST_RADIANS - INTRO_FROM_RADIANS) * eased;
    render();
    if (progress === 1) {
      stop();
    }
  });
  return stop;
}

/** Arrastar gira a goblin; renderiza só quando ela muda, sem laço parado gastando bateria. */
function enableDrag({ host, turntable, render }: Stage, stopIntro: () => void): () => void {
  let lastX: number | null = null;
  const onDown = (event: PointerEvent): void => {
    stopIntro();
    lastX = event.clientX;
    host.setPointerCapture(event.pointerId);
  };
  const onMove = (event: PointerEvent): void => {
    if (lastX === null) {
      return;
    }
    turntable.rotation.y += (event.clientX - lastX) * DRAG_RADIANS_PER_PX;
    lastX = event.clientX;
    render();
  };
  const onUp = (): void => {
    lastX = null;
  };
  host.addEventListener("pointerdown", onDown);
  host.addEventListener("pointermove", onMove);
  host.addEventListener("pointerup", onUp);
  host.addEventListener("pointercancel", onUp);
  return () => {
    host.removeEventListener("pointerdown", onDown);
    host.removeEventListener("pointermove", onMove);
    host.removeEventListener("pointerup", onUp);
    host.removeEventListener("pointercancel", onUp);
  };
}

function disposeScene(scene: Scene): void {
  scene.environment?.dispose();
  scene.traverse((object) => {
    if (object instanceof DirectionalLight) {
      // A luz com sombra guarda o mapa de sombra na GPU.
      object.dispose();
      return;
    }
    if (!isMesh(object)) {
      return;
    }
    object.geometry.dispose();
    const materials: Material[] = Array.isArray(object.material)
      ? object.material
      : [object.material];
    for (const material of materials) {
      // Os mapas (map, normalMap, ...) são campos do material; o three não os libera sozinho.
      const fields: readonly unknown[] = Object.values(material);
      for (const value of fields) {
        if (isTexture(value)) {
          value.dispose();
        }
      }
      material.dispose();
    }
  });
}

function isMesh(object: Object3D): object is Mesh {
  return object instanceof Mesh;
}

function isTexture(value: unknown): value is Texture {
  return typeof value === "object" && value !== null && "isTexture" in value;
}
