<template>
  <router-view />
</template>

<script lang="ts" setup>
import { onMounted } from "vue";
import { useAppStore } from "./stores/app";

const store = useAppStore();
onMounted(() => store.bootstrap());
</script>

<style lang="scss">
@font-face {
  font-family: "Source Han Serif CN";
  src: url("/fonts/SourceHanSerifCN-Medium.otf");
}
@font-face {
  font-family: "Source Han Serif CN";
  src: url("/fonts/SourceHanSerifCN-Bold.otf");
  font-weight: bold;
}
html, body, #app {
  width: 100%;
  height: 100%;
  border: 0;
  margin: 0;
  padding: 0;
  overflow: hidden;
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
}
img { -webkit-user-drag: none; }
</style>
