<template>
  <router-view />
</template>

<script setup>
// 性能测试模块外壳。实际导航由 layout/index.vue 的侧边菜单承载，
// 子路由（dashboard / projects / scenarios / executions / comparison / scheduled）
// 在 Layout 的 <router-view> 中渲染。此文件保留为模块统一入口占位。
</script>
