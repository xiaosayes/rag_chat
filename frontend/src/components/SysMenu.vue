<template>
  <div class="sys-zone" @click.stop.prevent="onTap"></div>
  <div class="sys-menu" v-if="show">
    <img class="sys-item" :src="'img/icon_refresh.png'" @click.stop.prevent="refresh" />
    <img class="sys-item" :src="'img/icon_quit.png'" @click.stop.prevent="quit" />
  </div>
</template>

<script lang="ts" setup>
/** 隐藏系统菜单（web-015）：1.8 设计稿——左上隐形区连点 3 次展开 刷新/退出。 */
import { onBeforeUnmount, ref } from "vue";

const show = ref(false);
let count = 0;
let timer = 0;

function onTap() {
  count++;
  clearTimeout(timer);
  timer = window.setTimeout(() => (count = 0), 300);
  if (count >= 3) {
    show.value = true;
    count = 0;
  }
}
function refresh() {
  location.reload();
}
function quit() {
  window.close();
}
onBeforeUnmount(() => clearTimeout(timer));
</script>

<style lang="scss" scoped>
.sys-zone {
  position: fixed;
  left: 0;
  top: 0;
  width: 10vh;
  height: 8vh;
  z-index: 50;
  background: transparent;
}
.sys-menu {
  position: fixed;
  top: 1.5vh;
  left: 50%;
  transform: translateX(-50%);
  z-index: 60;
  display: flex;
  gap: 2vh;
  background: rgba(255, 250, 235, 0.9);
  border-radius: 2vh;
  padding: 1vh 2vh;
  .sys-item {
    height: 4.166vh;
    cursor: pointer;
  }
}
</style>
