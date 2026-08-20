<template>
  <div class="storybook">
    <button class="btn-back" @click="onBack">返回</button>

    <div v-if="story.phase.value === 'preparing'" class="story-overlay">
      <div class="prepare-text">湘小图正在想「{{ story.preparingTheme.value }}」的故事…</div>
    </div>

    <template v-else>
      <div class="story-title">{{ story.title.value }}</div>
      <div class="story-img-area">
        <img v-if="imgUrl" class="story-img" :src="imgUrl" :alt="story.title.value" />
        <div v-else class="story-img-placeholder"><span>插画绘制中…</span></div>
      </div>
      <div class="story-text">{{ currentText }}</div>
      <div class="story-bar">
        <button class="btn-prev" :disabled="story.page.value <= 1" @click="story.prev()">上一页</button>
        <span class="page-indicator">{{ story.page.value }} / {{ story.total.value }}</span>
        <button class="btn-next" :disabled="story.page.value >= story.total.value" @click="story.next()">下一页</button>
      </div>
      <div v-if="story.phase.value === 'finished'" class="story-overlay finished">
        <div class="finished-text">故事讲完啦</div>
      </div>
    </template>
  </div>
</template>

<script lang="ts" setup>
/** 绘本模式（web-061）：一页=一图+一段文；翻页/返回/占位/结束态。 */
import { computed } from "vue";

const props = defineProps<{ story: any }>();
const emit = defineEmits<{ (e: "back"): void }>();

const currentText = computed(() => {
  const p = props.story.pages.value.find((x: any) => x.n === props.story.page.value);
  return p?.text ?? "";
});
const imgUrl = computed(() => props.story.images[props.story.page.value] ?? "");

function onBack() {
  props.story.back();
  emit("back");
}
</script>

<style lang="scss" scoped>
/* web-061：1080×1920 设计坐标；羊皮纸色系与返回钮样式对齐 ChatPanel（web-042） */
.storybook {
  position: relative;
  z-index: 70;              /* web-062 补强：压过 DeerAvatar(z1)/SysMenu(z50,60)——故事态小鹿/隐藏菜单不透出不可点 */
  width: 1080px;
  height: 1920px;
  /* 不透明底（与首页同源的森林底图）：z 序修复后下层内容在缝隙处也不透出 */
  background: url("../../public/img/v1/bg.png") 100% 100% no-repeat;
  background-size: 100% 100%;
  display: flex;
  flex-direction: column;
  align-items: center;

  .btn-back {
    position: absolute;
    top: 30px;
    right: 30px;
    z-index: 10;
    height: 104px;           /* 对齐 ChatPanel 返回钮（web-042：80→104px） */
    padding: 0 42px;
    border: 0;
    border-radius: 31px;
    background: rgba(255, 250, 235, 0.92);   /* 羊皮纸 */
    font-family: "Source Han Serif CN", serif;
    font-size: 36px;
    color: #4a3f30;
    cursor: pointer;
    filter: drop-shadow(0 3px 6px rgba(74, 63, 48, 0.35));  /* 与底色分离 */
  }

  .story-title {
    position: absolute;
    top: 30px;
    left: 30px;
    z-index: 10;
    padding: 18px 36px;
    border-radius: 31px;
    background: rgba(255, 250, 235, 0.92);   /* 羊皮纸 */
    font-family: "Source Han Serif CN", serif;
    font-size: 38px;
    color: #4a3f30;
    filter: drop-shadow(0 3px 6px rgba(74, 63, 48, 0.35));
  }

  .story-img-area {
    flex: none;
    width: 1080px;
    height: 1150px;
    overflow: hidden;
    position: relative;

    .story-img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }

    .story-img-placeholder {
      width: 100%;
      height: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      background: linear-gradient(110deg, rgba(255, 250, 235, 0.55) 30%,
                  rgba(255, 255, 255, 0.85) 50%, rgba(255, 250, 235, 0.55) 70%);
      background-size: 200% 100%;
      animation: shimmer 1.6s linear infinite;   /* 占位 shimmer（插图异步就绪前） */
      span {
        font-family: "Source Han Serif CN", serif;
        font-size: 38px;
        color: rgba(74, 63, 48, 0.65);
      }
    }
  }

  .story-text {
    flex: 1;
    min-height: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 30px 70px;
    box-sizing: border-box;
    font-family: "Source Han Serif CN", serif;
    font-size: 44px;
    line-height: 1.6;
    text-align: center;
    color: #4a3f30;
    text-shadow: 0 1px 2px rgba(255, 250, 235, 0.8);
  }

  .story-bar {
    flex: none;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 60px;
    height: 220px;
    padding-bottom: 60px;
    box-sizing: border-box;

    .btn-prev,
    .btn-next {
      width: 200px;
      height: 96px;
      border: 0;
      border-radius: 31px;
      background: rgba(255, 250, 235, 0.92);   /* 羊皮纸 */
      font-family: "Source Han Serif CN", serif;
      font-size: 36px;
      color: #4a3f30;
      cursor: pointer;
      filter: drop-shadow(0 3px 6px rgba(74, 63, 48, 0.35));
      &:disabled {
        opacity: 0.4;
        cursor: default;
      }
    }

    .page-indicator {
      font-family: "Source Han Serif CN", serif;
      font-size: 36px;
      color: #4a3f30;
      text-shadow: 0 1px 2px rgba(255, 250, 235, 0.8);
    }
  }

  .story-overlay {
    position: absolute;
    inset: 0;
    z-index: 20;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(74, 63, 48, 0.35);        /* 半透明盖层 */

    .prepare-text,
    .finished-text {
      background: rgba(255, 250, 235, 0.92);   /* 羊皮纸 */
      border-radius: 31px;
      padding: 40px 60px;
      font-family: "Source Han Serif CN", serif;
      font-size: 44px;
      color: #4a3f30;
      filter: drop-shadow(0 3px 6px rgba(74, 63, 48, 0.35));
    }
  }
}

@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
</style>
