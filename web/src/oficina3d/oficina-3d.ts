// Chunk da cena 3D da abertura (ADR 027, 030 e 038). O nome do arquivo vira o nome do chunk, e o
// check-budget reconhece o prefixo `assets/oficina-3d`: não renomeie sem mudar os dois.
// Um renderer só para o elenco inteiro: trocar de goblin baixa o GLB dela e troca o modelo.
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

import type { CastId } from "./cast";
import { attachGestures } from "./gestures";
import type { MountOptions, SceneHandle } from "./OficinaHero";
import { REST_POSE, turned, type Pose } from "./pose";

const FIELD_OF_VIEW_DEGREES = 22;
/** Folga em volta da goblin, para o cabelo não encostar na borda do quadro. */
const FRAME_MARGIN = 1.06;
/** Entrada: gira de um lado para o outro e para; abaixo dos 5 s da WCAG 2.2.2, sem botão. */
const INTRO_MS = 3_500;
const INTRO_FROM_RADIANS = 0.9;
/** Auto-rotação do botão Girar: uma volta a cada 12 s. */
const SPIN_RADIANS_PER_MS = (2 * Math.PI) / 12_000;
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

/** Os GLBs do elenco, um por goblin; só o escolhido é baixado (ADR 038). */
const MODEL_URLS = import.meta.glob<string>("../assets/art/oficina-3d/*.glb", {
  query: "?url",
  import: "default",
  eager: true,
});

export function mountScene(host: HTMLElement, options: MountOptions): SceneHandle {
  // O decoder Meshopt é WASM embutido neste chunk: nada vem de CDN e não há Worker (ADR 030).
  const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
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
  const lights = new Group();
  scene.add(turntable, lights);
  const camera = new PerspectiveCamera(FIELD_OF_VIEW_DEGREES, 4 / 5, 0.01, 100);

  /** Distância da câmera ao centro da goblin, que muda com o tamanho de cada modelo. */
  let distance = 1;
  const stage = new Stage(host, renderer, {
    applyPose: (pose) => {
      // O giro roda a goblin sob as luzes fixas; a inclinação sobe ou desce a câmera numa órbita.
      turntable.rotation.y = pose.yaw;
      camera.position.set(0, Math.sin(pose.pitch), Math.cos(pose.pitch)).multiplyScalar(distance);
      camera.lookAt(0, 0, 0);
    },
    render: () => {
      renderer.render(scene, camera);
    },
  });
  /** Só a troca mais recente vale: uma carga mais antiga que termina depois é descartada. */
  let latestShow = 0;

  let shown: CastId | null = null;

  const show = async (goblin: CastId, signal: AbortSignal): Promise<void> => {
    latestShow += 1;
    if (goblin === shown) {
      // Voltou para a goblin que já está no palco antes de a outra chegar: nada a baixar.
      return;
    }
    const ticket = latestShow;
    const gltf = await loader.parseAsync(await fetchModel(modelUrlOf(goblin), signal), "");
    const superseded = (): boolean => ticket !== latestShow || signal.aborted;
    if (superseded()) {
      disposeTree(gltf.scene);
      return;
    }
    shown = null;
    turntable.children.forEach(disposeTree);
    turntable.clear();
    lights.children.forEach(disposeTree);
    lights.clear();
    turntable.add(gltf.scene);
    const framed = frameModel(gltf.scene, camera);
    distance = framed.distance;
    addLights(lights, gltf.scene, framed.radius);
    // Compila os shaders em paralelo (KHR_parallel_shader_compile) antes do primeiro quadro, para
    // a compilação não virar uma tarefa longa na thread principal.
    await renderer.compileAsync(scene, camera);
    if (superseded()) {
      return;
    }
    shown = goblin;
    host.dataset["goblin"] = goblin;
    stage.playIntro();
  };

  const resize = (): void => {
    const { clientWidth: width, clientHeight: height } = host;
    if (width === 0 || height === 0) {
      return;
    }
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    stage.render();
  };
  const stopGestures = attachGestures(host, {
    onStart: () => {
      stage.stopIntro();
    },
    onTurn: (by) => {
      stage.turn(by);
    },
  });
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
    show,
    turn: (by) => {
      stage.stopIntro();
      stage.turn(by);
    },
    resetPose: () => {
      stage.resetPose();
    },
    setSpinning: (spinning) => {
      stage.setSpinning(spinning);
    },
    dispose: () => {
      latestShow += 1;
      stage.stop();
      stopGestures();
      resizeObserver.disconnect();
      renderer.domElement.removeEventListener("webglcontextlost", onLost);
      scene.environment?.dispose();
      disposeTree(scene);
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}

function modelUrlOf(goblin: CastId): string {
  const url = MODEL_URLS[`../assets/art/oficina-3d/oficina-3d-${goblin}.glb`];
  if (url === undefined) {
    throw new Error(`modelo da goblin ${goblin} não existe; rode scripts/arte/otimizar-glb.sh`);
  }
  return url;
}

async function fetchModel(url: string, signal: AbortSignal): Promise<ArrayBuffer> {
  const response = await fetch(url, { signal });
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
function addLights(lights: Group, model: Object3D, radius: number): void {
  const up = new Vector3(0, 1, 0);
  LIGHTS.forEach(({ power, yawDegrees, pitchDegrees }, index) => {
    const yaw = (yawDegrees * Math.PI) / 180;
    const pitch = (pitchDegrees * Math.PI) / 180;
    const light = new DirectionalLight(0xffffff, (LIGHT_SCALE * power) / (9 * Math.PI));
    light.position
      .set(Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch), Math.cos(yaw) * Math.cos(pitch))
      .applyAxisAngle(up, REST_POSE.yaw)
      .multiplyScalar(radius * LIGHT_DISTANCE_RADII);
    if (index === KEY_LIGHT_INDEX) {
      castShadowOver(light, radius);
    }
    lights.add(light);
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
 * Centra a goblin na origem, de pé, e calcula a distância da câmera para ela caber inteira no
 * quadro 4:5. Devolve também o raio da caixa da goblin, que dá a escala das luzes e da sombra.
 */
function frameModel(
  model: Object3D,
  camera: PerspectiveCamera,
): { readonly radius: number; readonly distance: number } {
  const box = new Box3().setFromObject(model);
  const size = box.getSize(new Vector3());
  const center = box.getCenter(new Vector3());
  model.position.sub(center);
  const halfFov = (FIELD_OF_VIEW_DEGREES * Math.PI) / 360;
  const fitHeight = size.y / 2 / Math.tan(halfFov);
  const fitWidth = size.x / 2 / Math.tan(halfFov) / camera.aspect;
  return {
    radius: size.length() / 2,
    distance: Math.max(fitHeight, fitWidth) * FRAME_MARGIN + size.z / 2,
  };
}

interface StageView {
  readonly applyPose: (pose: Pose) => void;
  readonly render: () => void;
}

type Motion = "entrada" | "girando" | "parado";

/**
 * A pose da goblin e o que a mexe: a entrada (uma vez, até 3,5 s), a auto-rotação do botão
 * Girar, o arrasto e as setas. O laço de animação só roda com entrada ou giro; fora disso o canvas
 * redesenha só quando a pose muda, sem gastar bateria. Marca `data-movimento`, `data-giro` e
 * `data-inclinacao` (graus) no host para o E2E.
 */
class Stage {
  readonly #host: HTMLElement;
  readonly #renderer: WebGLRenderer;
  readonly #view: StageView;
  #pose: Pose = REST_POSE;
  #introStart: number | null = null;
  #introRunning = false;
  #spinning = false;
  #lastFrame: number | null = null;

  constructor(host: HTMLElement, renderer: WebGLRenderer, view: StageView) {
    this.#host = host;
    this.#renderer = renderer;
    this.#view = view;
    this.#mark();
  }

  render(): void {
    this.#view.render();
  }

  playIntro(): void {
    this.#pose = { yaw: INTRO_FROM_RADIANS, pitch: REST_POSE.pitch };
    this.#introStart = null;
    this.#introRunning = true;
    this.#apply();
    this.#loop();
  }

  stopIntro(): void {
    this.#introRunning = false;
    this.#loop();
  }

  turn(by: Pose): void {
    this.#pose = turned(this.#pose, by);
    this.#apply();
  }

  resetPose(): void {
    this.#introRunning = false;
    this.#spinning = false;
    this.#pose = REST_POSE;
    this.#apply();
    this.#loop();
  }

  setSpinning(spinning: boolean): void {
    this.#spinning = spinning;
    if (spinning) {
      this.#introRunning = false;
    }
    this.#loop();
  }

  stop(): void {
    this.#introRunning = false;
    this.#spinning = false;
    this.#renderer.setAnimationLoop(null);
  }

  /** Liga o laço se há o que animar e desliga quando não há. */
  #loop(): void {
    this.#mark();
    if (!this.#introRunning && !this.#spinning) {
      this.#renderer.setAnimationLoop(null);
      this.#lastFrame = null;
      return;
    }
    this.#renderer.setAnimationLoop((now) => {
      this.#frame(now);
    });
  }

  #frame(now: number): void {
    const elapsed = this.#lastFrame === null ? 0 : now - this.#lastFrame;
    this.#lastFrame = now;
    if (this.#introRunning) {
      this.#introStart ??= now;
      const progress = Math.min(1, (now - this.#introStart) / INTRO_MS);
      const eased = 1 - (1 - progress) ** 3;
      this.#pose = {
        ...this.#pose,
        yaw: INTRO_FROM_RADIANS + (REST_POSE.yaw - INTRO_FROM_RADIANS) * eased,
      };
      if (progress === 1) {
        this.#introRunning = false;
      }
    } else if (this.#spinning) {
      this.#pose = turned(this.#pose, { yaw: elapsed * SPIN_RADIANS_PER_MS, pitch: 0 });
    }
    this.#apply();
    if (!this.#introRunning && !this.#spinning) {
      this.#loop();
    }
  }

  #apply(): void {
    this.#view.applyPose(this.#pose);
    this.#view.render();
    this.#host.dataset["giro"] = String(Math.round((this.#pose.yaw * 180) / Math.PI));
    this.#host.dataset["inclinacao"] = String(Math.round((this.#pose.pitch * 180) / Math.PI));
  }

  #mark(): void {
    const motion: Motion = this.#introRunning ? "entrada" : this.#spinning ? "girando" : "parado";
    this.#host.dataset["movimento"] = motion;
  }
}

/** Libera da GPU a malha, os materiais, as texturas e os mapas de sombra de uma árvore. */
function disposeTree(root: Object3D): void {
  root.traverse((object) => {
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
