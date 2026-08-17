<template>
  <div class="deer-avatar" ref="container"></div>
</template>

<script lang="ts" setup>
/** 3D 小鹿（web-016）：移植重构自参考 Human.vue——
 *  本地 gltf/EXR、STANDBY/TALK 动画池随机轮播交叉淡入、进度上报 store。
 *  差异：不挂 window 全局，改 defineExpose 供父组件驱动。 */
import { onBeforeUnmount, onMounted, ref } from "vue";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { EXRLoader } from "three/examples/jsm/loaders/EXRLoader.js";
import { useAppStore } from "../stores/app";
import { ACTION_POOLS, pickNext, poolOf, type PoolName } from "./deer/animations";

const MODEL_URL = "model/deer/deer_final_5.gltf";
const ENV_URL = "model/deer/evening_road_01_puresky_1k.exr";

const store = useAppStore();
const container = ref<HTMLDivElement>();

let renderer: THREE.WebGLRenderer;
let mixer: THREE.AnimationMixer;
let scene: THREE.Scene;
let camera: THREE.PerspectiveCamera;
let clock: THREE.Clock;
let frameId = -1;
const actions: Record<string, THREE.AnimationAction> = {};
let current: string | undefined;
let currentPool: PoolName | undefined;

function play(pool: PoolName) {
  const name = pickNext(pool, current);
  const action = actions[name];
  if (!action) return;
  Object.keys(actions).forEach((n) => {
    if (n !== name && actions[n].isRunning()) actions[n].fadeOut(0.5);
  });
  current = name;
  currentPool = poolOf(name);
  action.reset().fadeIn(0.5).play();
}

function tick() {
  renderer.render(scene, camera);
  mixer.update(clock.getDelta());
  // 片段播完 → 同池随机续播（参考 playAnimationAgain 语义）
  if (current && actions[current]) {
    const a = actions[current];
    if (a.isRunning() && a.time >= a.getClip().duration - 0.05 && currentPool) {
      play(currentPool);
    }
  }
  frameId = requestAnimationFrame(tick);
}

async function init() {
  const el = container.value!;
  const width = 756;    // web-034：1080×1920 设计坐标（原 39.375vh×50.375vh）
  const height = 967;
  scene = new THREE.Scene();
  mixer = new THREE.AnimationMixer(scene);
  clock = new THREE.Clock();
  camera = new THREE.PerspectiveCamera(45, width / height, 1, 1200);
  camera.position.set(0, 0, 2.7);
  camera.lookAt(0, 0, 0);
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setSize(width, height);
  renderer.setPixelRatio(window.devicePixelRatio);
  el.appendChild(renderer.domElement);

  const manager = new THREE.LoadingManager();
  manager.onProgress = (_url, loaded, total) => {
    store.loadProgress = Math.min(99, Math.round((loaded / total) * 100));
  };
  // 双方向光 + 环境光（参考参数）
  const d1 = new THREE.DirectionalLight(0xffffff, 0.25);
  d1.position.set(-0.017, 0.005, 0.02).normalize();
  scene.add(d1);
  const d2 = new THREE.DirectionalLight(0xffffff, 0.25);
  d2.position.set(0.01, 0.005, 0.02).normalize();
  scene.add(d2);
  scene.add(new THREE.AmbientLight(0x404040, 0.8));

  new EXRLoader().load(ENV_URL, (hdr: THREE.Texture) => {
    hdr.mapping = THREE.EquirectangularReflectionMapping;
    scene.environment = hdr;
  });
  const gltf = await new GLTFLoader(manager).loadAsync(MODEL_URL);
  const model = gltf.scene;
  model.position.y = -0.95;
  scene.add(model);
  gltf.animations.forEach((clip) => {
    actions[clip.name] = mixer.clipAction(clip);
  });
  play("STANDBY");
  store.loadProgress = 100;
  store.modelReady = true;

  const shadow = document.createElement("img");
  shadow.src = "img/shadow.png";
  shadow.className = "deer-shadow";
  el.appendChild(shadow);
  tick();
}

onMounted(init);
onBeforeUnmount(() => cancelAnimationFrame(frameId));

defineExpose({
  playTalk: () => play("TALK"),
  playStandby: () => play("STANDBY"),
  poolOf: () => currentPool,
});
</script>

<style lang="scss" scoped>
.deer-avatar {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  top: -19px;
  width: 756px;
  height: 967px;
  z-index: 1;
  background: transparent;
  pointer-events: none; /* 触屏事件穿透到下层（一体机） */
}
.deer-shadow {
  position: absolute;
  bottom: 15px;
  left: 50%;
  transform: translateX(-50%);
  height: 84px;
}
</style>
