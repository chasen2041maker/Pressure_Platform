<template>
  <div class="home-container">
    <div class="content-wrapper">
      <div class="header-actions">
        <!-- PC：语言、用户分开 -->
        <div class="header-actions-pc">
          <el-dropdown @command="handleLanguageChange" class="language-dropdown">
            <span class="el-dropdown-link">
              <span class="language-icon">{{ currentLanguage === 'zh-cn' ? '🇨🇳' : '🇺🇸' }}</span>
              <span class="language-text">{{ $t('home.language.current') }}</span>
              <el-icon class="el-icon--right"><arrow-down /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="zh-cn" :disabled="currentLanguage === 'zh-cn'">
                  <span class="dropdown-flag">🇨🇳</span> {{ $t('home.language.zhCN') }}
                </el-dropdown-item>
                <el-dropdown-item command="en" :disabled="currentLanguage === 'en'">
                  <span class="dropdown-flag">🇺🇸</span> {{ $t('home.language.en') }}
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <el-dropdown @command="handleCommand">
            <span class="el-dropdown-link">
              <el-avatar :size="32" :icon="UserFilled" />
              <span class="username">{{ userStore.user?.username || $t('home.user') }}</span>
              <el-icon class="el-icon--right"><arrow-down /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="logout">{{ $t('home.logout') }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>

        <!-- 移动端：合并菜单 -->
        <div class="header-actions-mobile">
          <el-dropdown trigger="click" @command="handleHeaderCommand">
            <span class="user-menu-trigger">
              <span class="avatar-wrap">
                <el-avatar :size="28" :icon="UserFilled" />
                <span class="lang-badge">{{ currentLanguage === 'zh-cn' ? '🇨🇳' : '🇺🇸' }}</span>
              </span>
              <el-icon class="trigger-arrow"><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="zh-cn" :disabled="currentLanguage === 'zh-cn'">
                  <span class="dropdown-flag">🇨🇳</span> {{ $t('home.language.zhCN') }}
                </el-dropdown-item>
                <el-dropdown-item command="en" :disabled="currentLanguage === 'en'">
                  <span class="dropdown-flag">🇺🇸</span> {{ $t('home.language.en') }}
                </el-dropdown-item>
                <el-dropdown-item command="logout" divided>
                  {{ $t('home.logout') }}
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>

      <h1 class="main-title">{{ $t('home.title') }}</h1>
      <p class="subtitle">{{ $t('home.subtitle') }}</p>

      <section class="cards-section" aria-labelledby="developed-heading">
        <h2 id="developed-heading" class="section-title">{{ $t('home.developed') }}</h2>
        <div class="cards-container developed-cards">
          <div class="nav-card" @click="handleNavigate('performance')" role="button" tabindex="0">
            <div class="card-icon perf-icon">
              <el-icon><Odometer /></el-icon>
            </div>
            <h3>{{ $t('home.performanceTesting') }}</h3>
            <p>{{ $t('home.performanceTestingDesc') }}</p>
          </div>
        </div>
      </section>

      <section class="cards-section" aria-labelledby="undeveloped-heading">
        <h2 id="undeveloped-heading" class="section-title">{{ $t('home.undeveloped') }}</h2>
      <div class="cards-container">
        <!-- AI用例生成 -->
        <div class="nav-card" @click="handleNavigate('ai')" role="button" tabindex="0">
          <div class="card-icon ai-icon">
            <el-icon><MagicStick /></el-icon>
          </div>
          <h3>{{ $t('home.aiCaseGeneration') }}</h3>
          <p>{{ $t('home.aiCaseGenerationDesc') }}</p>
        </div>

        <!-- 接口测试 -->
        <div class="nav-card" @click="handleNavigate('api')" role="button" tabindex="0">
          <div class="card-icon api-icon">
            <el-icon><Link /></el-icon>
          </div>
          <h3>{{ $t('home.apiTesting') }}</h3>
          <p>{{ $t('home.apiTestingDesc') }}</p>
        </div>

        <!-- UI自动化测试 -->
        <div class="nav-card" @click="handleNavigate('ui')" role="button" tabindex="0">
          <div class="card-icon ui-icon">
            <el-icon><Monitor /></el-icon>
          </div>
          <h3>{{ $t('home.uiAutomation') }}</h3>
          <p>{{ $t('home.uiAutomationDesc') }}</p>
        </div>

        <!-- Bug缺陷管理 -->
        <div class="nav-card" @click="handleNavigate('defects')" role="button" tabindex="0">
          <div class="card-icon defects-icon">
            <el-icon><Tickets /></el-icon>
          </div>
          <h3>{{ $t('home.defectManagement') }}</h3>
          <p>{{ $t('home.defectManagementDesc') }}</p>
        </div>

        <!-- 数据工厂 -->
        <div class="nav-card" @click="handleNavigate('data')" role="button" tabindex="0">
          <div class="card-icon data-icon">
            <el-icon><DataLine /></el-icon>
          </div>
          <h3>{{ $t('home.dataFactory') }}</h3>
          <p>{{ $t('home.dataFactoryDesc') }}</p>
        </div>

        <!-- APP自动化测试 -->
        <div class="nav-card" @click="handleNavigate('app')" role="button" tabindex="0">
          <div class="card-icon app-icon">
            <el-icon><Cellphone /></el-icon>
          </div>
          <h3>{{ $t('home.appAutomation') }}</h3>
          <p>{{ $t('home.appAutomationDesc') }}</p>
        </div>

        <!-- AI 智能模式 -->
        <div class="nav-card" @click="handleNavigate('ai-intelligent')" role="button" tabindex="0">
          <div class="card-icon ai-intelligent-icon">
            <el-icon><Cpu /></el-icon>
          </div>
          <h3>{{ $t('home.aiIntelligentMode') }}</h3>
          <p>{{ $t('home.aiIntelligentModeDesc') }}</p>
        </div>
        <!-- AI评测师 -->
        <div class="nav-card" @click="handleNavigate('assistant')" role="button" tabindex="0">
          <div class="card-icon assistant-icon">
            <el-icon><ChatDotRound /></el-icon>
          </div>
          <h3>{{ $t('home.aiEvaluator') }}</h3>
          <p>{{ $t('home.aiEvaluatorDesc') }}</p>
        </div>
        <!-- 配置中心 -->
        <div class="nav-card" @click="handleNavigate('config')" role="button" tabindex="0">
          <div class="card-icon config-icon">
            <el-icon><Setting /></el-icon>
          </div>
          <h3>{{ $t('home.configCenter') }}</h3>
          <p>{{ $t('home.configCenterDesc') }}</p>
        </div>

        <!-- 智能评分器 -->
        <div class="nav-card" @click="handleNavigate('judge')" role="button" tabindex="0">
          <div class="card-icon judge-icon">
            <el-icon><Select /></el-icon>
          </div>
          <h3>{{ $t('llmJudge.title') }}</h3>
          <p>{{ $t('llmJudge.subtitle') }}</p>
        </div>

        <!-- 监控中心 -->
        <div class="nav-card" @click="handleNavigate('monitor')" role="button" tabindex="0">
          <div class="card-icon monitor-icon">
            <el-icon><Odometer /></el-icon>
          </div>
          <h3>{{ $t('home.monitorCenter') }}</h3>
          <p>{{ $t('home.monitorCenterDesc') }}</p>
        </div>

        <!-- MCP 管理端 -->
        <div class="nav-card" @click="handleNavigate('mcp')" role="button" tabindex="0">
          <div class="card-icon mcp-icon">
            <el-icon><Connection /></el-icon>
          </div>
          <h3>{{ $t('home.mcpConsole') }}</h3>
          <p>{{ $t('home.mcpConsoleDesc') }}</p>
        </div>

        <!-- 文档中心 -->
        <div class="nav-card" @click="handleNavigate('docs')" role="button" tabindex="0">
          <div class="card-icon docs-icon">
            <el-icon><Document /></el-icon>
          </div>
          <h3>{{ $t('home.docsCenter') }}</h3>
          <p>{{ $t('home.docsCenterDesc') }}</p>
        </div>
      </div>
      </section>
    </div>

    <el-dialog
      v-model="mobileDialogVisible"
      class="mobile-tip-dialog"
      :title="$t('home.mobileTipTitle')"
      width="88%"
      align-center
      :close-on-click-modal="true"
      append-to-body
    >
      <div class="mobile-tip-dialog-body">
        <div class="dialog-icon-wrap">
          <el-icon><Monitor /></el-icon>
        </div>
        <p class="dialog-desc">{{ $t('home.mobileTipDesc') }}</p>
      </div>
      <template #footer>
        <el-button type="primary" class="dialog-confirm-btn" @click="mobileDialogVisible = false">
          {{ $t('home.mobileTipOk') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'
import { useAppStore } from '@/stores/app'
import { track } from '@/utils/tracker'
import { ElMessage, ElMessageBox } from 'element-plus'
import { MagicStick, Link, Monitor, DataLine, Cpu, Setting, ChatDotRound, UserFilled, ArrowDown, Cellphone, Tickets, Select, Odometer, Connection, Document } from '@element-plus/icons-vue'

const router = useRouter()
const { t } = useI18n()
const userStore = useUserStore()
const appStore = useAppStore()

// 当前语言
const currentLanguage = computed(() => appStore.language)
const isMobile = ref(false)
const mobileTipDismissed = ref(false)
const MOBILE_BREAKPOINT = 768
const MOBILE_TIP_STORAGE_KEY = 'testhub_home_mobile_tip_seen'

const dismissMobileTip = () => {
  mobileTipDismissed.value = true
  try {
    localStorage.setItem(MOBILE_TIP_STORAGE_KEY, '1')
  } catch {
    // ignore quota / private mode
  }
}

const mobileDialogVisible = computed({
  get: () => isMobile.value && !mobileTipDismissed.value,
  set: (val) => {
    if (!val) dismissMobileTip()
  }
})

const updateMobileTip = () => {
  isMobile.value = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT}px)`).matches
}

onMounted(() => {
  try {
    if (localStorage.getItem(MOBILE_TIP_STORAGE_KEY) === '1') {
      mobileTipDismissed.value = true
    }
  } catch {
    // ignore
  }
  updateMobileTip()
  window.addEventListener('resize', updateMobileTip)
})

onUnmounted(() => {
  window.removeEventListener('resize', updateMobileTip)
})

const handleLanguageChange = (lang) => {
  appStore.setLanguage(lang)
}

const handleCommand = (command) => {
  if (command === 'logout') {
    handleLogout()
  }
}

const handleHeaderCommand = (command) => {
  if (command === 'logout') {
    handleLogout()
    return
  }
  if (command === 'zh-cn' || command === 'en') {
    appStore.setLanguage(command)
  }
}

const handleLogout = () => {
  ElMessageBox.confirm(t('home.logoutConfirm'), t('common.tips'), {
    confirmButtonText: t('common.confirm'),
    cancelButtonText: t('common.cancel'),
    type: 'warning'
  }).then(() => {
    userStore.logout()
    router.push('/login')
    ElMessage.success(t('home.logoutSuccess'))
  }).catch(() => {})
}

const handleNavigate = (type) => {
  const routes = {
    'ai': '/ai-generation/requirement-analysis',
    'api': '/api-testing/dashboard',
    'ui': '/ui-automation/dashboard',
    'defects': '/defects/dashboard',
    'app': '/app-automation/dashboard',
    'ai-intelligent': '/ai-intelligent-mode/testing',
    'assistant': '/ai-generation/assistant',
    'config': '/configuration/ai-model',
    'judge': '/llm-judge/dashboard',
    'data': '/data-factory',
    'performance': '/performance-testing/dashboard',
    'monitor': '/monitor/dashboard',
    'mcp': '/mcp/console',
    'docs': '/docs-center'
  }

  if (routes[type]) {
    track('module_card_click', {
      event_type: 'click',
      module: 'home',
      page_path: '/home',
      target_path: routes[type],
      metadata: {
        card_type: type
      }
    })
    if (type === 'performance') {
      router.push(routes[type])
      return
    }
    const routeData = router.resolve({ path: routes[type] })
    window.open(routeData.href, '_blank')
  }
}
</script>

<style scoped lang="scss">
.home-container {
  min-height: 100vh;
  background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 20px;
}

.content-wrapper {
  text-align: center;
  max-width: 1200px;
  width: 100%;
  position: relative;
}

.header-actions {
  position: absolute;
  top: 0;
  right: 0;
  padding: 10px;
}

.header-actions-pc {
  display: flex;
  align-items: center;
  gap: 20px;

  .language-dropdown {
    .el-dropdown-link {
      display: flex;
      align-items: center;
      cursor: pointer;
      color: #5e6d82;
      transition: color 0.3s;
      outline: none;

      &:focus {
        outline: none;
      }

      .language-icon {
        font-size: 18px;
        margin-right: 5px;
        line-height: 1;
      }

      .language-text {
        margin: 0 5px;
        font-size: 14px;
      }

      &:hover {
        color: #409eff;
      }
    }
  }

  .el-dropdown-link {
    display: flex;
    align-items: center;
    cursor: pointer;
    color: #5e6d82;
    transition: color 0.3s;
    outline: none;

    &:focus {
      outline: none;
    }

    .username {
      margin: 0 8px;
      font-size: 14px;
    }

    &:hover {
      color: #409eff;
    }
  }
}

.header-actions-mobile {
  display: none;
}

.user-menu-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  color: #5e6d82;
  padding: 6px 10px 6px 6px;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border-radius: 24px;
  border: 1px solid rgba(255, 255, 255, 0.8);
  box-shadow: 0 2px 8px rgba(31, 45, 61, 0.06);
  transition: color 0.3s, background 0.3s;
  outline: none;

  &:focus {
    outline: none;
  }

  &:hover {
    color: #409eff;
    background: rgba(255, 255, 255, 0.85);
  }

  .avatar-wrap {
    position: relative;
    display: inline-flex;
    flex-shrink: 0;
  }

  .lang-badge {
    position: absolute;
    right: -5px;
    bottom: -3px;
    font-size: 11px;
    line-height: 1;
    background: #fff;
    border-radius: 50%;
    padding: 1px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
  }

  .trigger-arrow {
    font-size: 12px;
    color: #909399;
  }
}

.dropdown-flag {
  font-size: 16px;
  margin-right: 5px;
}

.main-title {
  font-size: 3.5rem;
  color: #2c3e50;
  margin-bottom: 1rem;
  font-weight: 700;
  letter-spacing: 2px;
}

.subtitle {
  font-size: 1.5rem;
  color: #5e6d82;
  margin-bottom: 4rem;
}

.cards-container {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 30px;
  padding: 20px;
}

.cards-section + .cards-section {
  margin-top: 24px;
}

.section-title {
  margin: 0;
  padding: 0 20px;
  color: #2c3e50;
  text-align: left;
  font-size: 20px;
  font-weight: 600;
}

.cards-container.developed-cards {
  grid-template-columns: minmax(0, 280px);
}

.nav-card {
  background: rgba(255, 255, 255, 0.9);
  border-radius: 16px;
  padding: 40px 20px;
  cursor: pointer;
  transition: all 0.3s ease;
  box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
  display: flex;
  flex-direction: column;
  align-items: center;

  &:hover {
    transform: translateY(-10px);
    box-shadow: 0 20px 30px rgba(0, 0, 0, 0.1);
    background: #fff;
  }

  h3 {
    font-size: 1.5rem;
    color: #2c3e50;
    margin: 20px 0 10px;
  }

  p {
    color: #7f8c8d;
    line-height: 1.5;
    margin: 0;
  }
}

.card-icon {
  width: 80px;
  height: 80px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 40px;
  margin-bottom: 10px;
  transition: all 0.3s ease;

  &.ai-icon {
    background: #e8f4ff;
    color: #409eff;
  }

  &.api-icon {
    background: #f0f9eb;
    color: #67c23a;
  }

  &.ui-icon {
    background: #fdf6ec;
    color: #e6a23c;
  }

  &.data-icon {
    background: #e8f4ff;
    color: #409eff;
  }

  &.defects-icon {
    background: #fef0f0;
    color: #f56c6c;
  }

  &.app-icon {
    background: #f9f0ff;
    color: #722ed1;
  }

  &.ai-intelligent-icon {
    background: #f0f5ff;
    color: #2f54eb;
  }

  &.config-icon {
    background: #e6fffb;
    color: #13c2c2;
  }

  &.judge-icon {
    background: #f0f5ff;
    color: #1d39c4;
  }

  &.assistant-icon {
    background: #fff7e6;
    color: #fa8c16;
  }

  &.monitor-icon {
    background: #fff1f0;
    color: #f5222d;
  }

  &.mcp-icon {
    background: #fff0f6;
    color: #eb2f96;
  }

  &.docs-icon {
    background: #f0f5ff;
    color: #2f54eb;
  }

  &.perf-icon {
    background: #e6fffb;
    color: #13c2c2;
  }
}

.nav-card:hover .card-icon {
  transform: scale(1.1);
}

@media screen and (max-width: 1920px) {
  .main-title {
    font-size: 3.2rem;
  }

  .subtitle {
    font-size: 1.4rem;
  }

  .cards-container {
    gap: 28px;
    padding: 18px;
  }
}

@media screen and (max-width: 1600px) {
  .main-title {
    font-size: 3rem;
  }

  .subtitle {
    font-size: 1.3rem;
  }

  .cards-container {
    gap: 26px;
    padding: 16px;
    grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  }

  .nav-card {
    padding: 35px 18px;
  }
}

@media screen and (max-width: 1440px) {
  .main-title {
    font-size: 2.8rem;
  }

  .subtitle {
    font-size: 1.2rem;
  }

  .cards-container {
    gap: 24px;
    padding: 14px;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  }

  .nav-card {
    padding: 30px 16px;

    h3 {
      font-size: 1.4rem;
    }
  }

  .card-icon {
    width: 70px;
    height: 70px;
    font-size: 35px;
  }
}

@media screen and (max-width: 1366px) {
  .main-title {
    font-size: 2.6rem;
  }

  .subtitle {
    font-size: 1.1rem;
  }

  .cards-container {
    gap: 22px;
    padding: 12px;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  }

  .nav-card {
    padding: 28px 14px;

    h3 {
      font-size: 1.3rem;
    }
  }

  .card-icon {
    width: 65px;
    height: 65px;
    font-size: 32px;
  }
}

@media screen and (max-width: 1280px) {
  .main-title {
    font-size: 2.4rem;
  }

  .subtitle {
    font-size: 1rem;
  }

  .cards-container {
    gap: 20px;
    padding: 12px;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  }

  .nav-card {
    padding: 25px 12px;

    h3 {
      font-size: 1.2rem;
    }
  }

  .card-icon {
    width: 60px;
    height: 60px;
    font-size: 30px;
  }
}

@media screen and (max-width: 1024px) {
  .home-container {
    padding: 15px;
  }

  .main-title {
    font-size: 2.2rem;
  }

  .subtitle {
    font-size: 1rem;
    margin-bottom: 3rem;
  }

  .cards-container {
    gap: 18px;
    padding: 10px;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  }

  .nav-card {
    padding: 20px 10px;

    h3 {
      font-size: 1.1rem;
    }

    p {
      font-size: 0.9rem;
    }
  }

  .card-icon {
    width: 55px;
    height: 55px;
    font-size: 28px;
  }

  .header-actions {
    padding: 8px;
  }
}

/* 移动端：≤768px 专用样式 */
@media screen and (max-width: 768px) {
  .cards-container.developed-cards {
    grid-template-columns: minmax(0, 1fr);
  }

  .home-container {
    position: relative;
    overflow: hidden;
    padding: 14px 14px 24px;
    padding-top: max(14px, env(safe-area-inset-top));
    background: linear-gradient(165deg, #eef2f7 0%, #e2eaf2 42%, #d5dfea 100%);

    &::before,
    &::after {
      content: '';
      position: absolute;
      border-radius: 50%;
      pointer-events: none;
      z-index: 0;
    }

    &::before {
      width: 260px;
      height: 260px;
      top: -70px;
      right: -50px;
      background: radial-gradient(circle, rgba(64, 158, 255, 0.14) 0%, transparent 68%);
    }

    &::after {
      width: 220px;
      height: 220px;
      bottom: 8%;
      left: -70px;
      background: radial-gradient(circle, rgba(99, 126, 234, 0.1) 0%, transparent 70%);
    }
  }

  .content-wrapper {
    position: relative;
    z-index: 1;
  }

  .header-actions {
    position: static;
    margin-bottom: 12px;
    padding: 0;
  }

  .header-actions-pc {
    display: none;
  }

  .header-actions-mobile {
    display: flex;
    justify-content: flex-end;
  }

  .main-title {
    font-size: 1.75rem;
    letter-spacing: 0.5px;
    color: #1f2d3d;
    margin-bottom: 8px;
  }

  .subtitle {
    font-size: 0.9375rem;
    color: #7a8494;
    margin: 0 auto 24px;
    max-width: 280px;
    line-height: 1.5;
  }

  .cards-container {
    grid-template-columns: repeat(2, 1fr);
    gap: 14px;
  }

  .nav-card {
    min-height: 148px;
    padding: 18px 12px 16px;
    border-radius: 14px;
    background: rgba(255, 255, 255, 0.88);
    border: 1px solid rgba(255, 255, 255, 0.95);
    box-shadow:
      0 4px 14px rgba(31, 45, 61, 0.07),
      0 1px 3px rgba(31, 45, 61, 0.04);
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);

    &:hover {
      transform: none;
      box-shadow:
        0 6px 18px rgba(31, 45, 61, 0.1),
        0 2px 4px rgba(31, 45, 61, 0.05);
    }

    &:active {
      transform: scale(0.98);
      background: rgba(255, 255, 255, 0.96);
    }

    h3 {
      font-size: 15px;
      margin: 12px 0 6px;
      color: #1f2d3d;
      line-height: 1.35;
    }

    p {
      font-size: 12px;
      line-height: 1.45;
      color: #8a939d;
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
  }

  .card-icon {
    width: 48px;
    height: 48px;
    font-size: 24px;
    border-radius: 14px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  }

  .nav-card:hover .card-icon {
    transform: none;
  }
}

@media screen and (max-width: 480px) {
  .home-container {
    padding: 12px 12px 20px;
    padding-top: max(12px, env(safe-area-inset-top));
  }

  .header-actions {
    margin-bottom: 16px;
  }

  .header-actions-mobile .user-menu-trigger {
    padding: 5px 8px 5px 5px;
    gap: 4px;
  }

  .main-title {
    font-size: 1.5rem;
  }

  .subtitle {
    font-size: 0.875rem;
    margin-bottom: 20px;
  }

  .cards-container {
    gap: 12px;
  }

  .nav-card {
    min-height: 140px;
    padding: 16px 10px 14px;
    border-radius: 12px;

    h3 {
      font-size: 14px;
      margin: 10px 0 5px;
    }

    p {
      font-size: 11px;
      -webkit-line-clamp: 3;
    }
  }

  .card-icon {
    width: 44px;
    height: 44px;
    font-size: 22px;
    border-radius: 12px;
  }
}
</style>

<style lang="scss">
.mobile-tip-dialog.el-dialog {
  max-width: 340px;
  border-radius: 16px;
  overflow: hidden;

  .el-dialog__header {
    padding: 20px 20px 8px;
    margin-right: 0;
    text-align: center;

    .el-dialog__title {
      font-size: 17px;
      font-weight: 600;
      color: #303133;
      line-height: 1.4;
    }

    .el-dialog__headerbtn {
      top: 14px;
      right: 14px;
    }
  }

  .el-dialog__body {
    padding: 4px 24px 8px;
  }

  .el-dialog__footer {
    padding: 8px 20px 20px;

    .dialog-confirm-btn {
      width: 100%;
      height: 40px;
      border-radius: 20px;
      font-size: 15px;
    }
  }
}

.mobile-tip-dialog-body {
  text-align: center;

  .dialog-icon-wrap {
    width: 56px;
    height: 56px;
    margin: 0 auto 14px;
    border-radius: 14px;
    background: linear-gradient(145deg, #ecf5ff 0%, #d9ecff 100%);
    color: #409eff;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 28px;
  }

  .dialog-desc {
    margin: 0;
    font-size: 14px;
    color: #606266;
    line-height: 1.6;
  }
}
</style>
