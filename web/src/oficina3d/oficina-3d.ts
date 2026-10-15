// Chunk da cena 3D da abertura (ADR 027 e 030). O nome do arquivo vira o nome do chunk, e o
// check-budget reconhece o prefixo `assets/oficina-3d`: não renomeie sem mudar os dois.
import {
  AgXToneMapping,
  Box3,
  DirectionalLight,
  Group,
  Mesh,
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
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
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
const ENVIRONMENT_BLUR = 0.04;
/*
 * Cor da pele: a textura do Rodin é verde-amarelada clara (média ~#bebd7e). O verde-oliva que o
 * usuário aprovou vem do render do Blender, com tom AgX, um sol forte e ambiente cinza escuro.
 * Com tom Neutral e só o ambiente claro do RoomEnvironment, a pele saía amarelada (ADR 030).
 * Estes valores reproduzem a média de pele do render aprovado (rgb 126,123,90) com o AgX.
 */
const TONE_EXPOSURE = 0.4;
const KEY_LIGHT_INTENSITY = 2.5;
const ENVIRONMENT_INTENSITY = 0.25;

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
  renderer.toneMapping = AgXToneMapping;
  renderer.toneMappingExposure = TONE_EXPOSURE;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, MAX_PIXEL_RATIO));
  renderer.domElement.className = "block size-full";

  const scene = new Scene();
  const environment = new PMREMGenerator(renderer);
  scene.environment = environment.fromScene(new RoomEnvironment(), ENVIRONMENT_BLUR).texture;
  scene.environmentIntensity = ENVIRONMENT_INTENSITY;
  const keyLight = new DirectionalLight(0xffffff, KEY_LIGHT_INTENSITY);
  // Luz de cima à esquerda, na frente, como o sol do render aprovado.
  keyLight.position.set(-1, 2, 2);
  scene.add(keyLight);
  environment.dispose();

  const turntable = new Group();
  turntable.add(gltf.scene);
  scene.add(turntable);
  const camera = new PerspectiveCamera(FIELD_OF_VIEW_DEGREES, 4 / 5, 0.01, 100);
  frameModel(gltf.scene, camera);
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

/** Centra a goblin na origem, de pé, e afasta a câmera até ela caber inteira no quadro 4:5. */
function frameModel(model: Object3D, camera: PerspectiveCamera): void {
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
