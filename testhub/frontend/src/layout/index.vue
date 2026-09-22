<template>
  <div class="layout">
    <el-container>
      <!-- 侧边栏 -->
      <button v-if="currentModule === 'performance-testing' && performanceMenuOpen" class="performance-menu-backdrop" :aria-label="$t('performanceTesting.accountPool.closeMenu')" @click="performanceMenuOpen = false" />
      <el-aside id="module-navigation" width="240px" :class="{ 'mobile-open': currentModule === 'performance-testing' && performanceMenuOpen }">
        <el-button v-if="currentModule === 'performance-testing'" class="performance-menu-close" @click="performanceMenuOpen = false">{{ $t('performanceTesting.accountPool.closeMenu') }}</el-button>
        <div class="logo" @click="router.push('/home')" style="cursor: pointer;">
          <img :src="logoImage" alt="TestHub" class="logo-img" />
        </div>
        <el-menu
          :default-active="$route.path"
          router
          background-color="#001529"
          text-color="#fff"
          active-text-color="#1890ff"
          @select="performanceMenuOpen = false"
        >
          <!-- AI用例生成模块菜单 -->
          <template v-if="currentModule === 'ai-generation'">
            <el-sub-menu index="requirement">
              <template #title>
                <el-icon><MagicStick /></el-icon>
                <span>{{ $t('menu.intelligentCaseGeneration') }}</span>
              </template>
              <el-menu-item index="/ai-generation/requirement-analysis">{{ $t('menu.aiCaseGeneration') }}</el-menu-item>
              <el-menu-item index="/ai-generation/generated-testcases">{{ $t('menu.aiGeneratedTestcases') }}</el-menu-item>
            </el-sub-menu>
            <el-menu-item index="/ai-generation/projects">
              <el-icon><Folder /></el-icon>
              <span>{{ $t('menu.projectManagement') }}</span>
            </el-menu-item>
            <el-menu-item index="/ai-generation/testcases">
              <el-icon><Document /></el-icon>
              <span>{{ $t('menu.testCases') }}</span>
            </el-menu-item>
            <el-menu-item index="/ai-generation/versions">
              <el-icon><Flag /></el-icon>
              <span>{{ $t('menu.versionManagement') }}</span>
            </el-menu-item>
            <el-sub-menu index="reviews">
              <template #title>
                <el-icon><Check /></el-icon>
                <span>{{ $t('menu.reviewManagement') }}</span>
              </template>
              <el-menu-item index="/ai-generation/reviews">{{ $t('menu.reviewList') }}</el-menu-item>
              <el-menu-item index="/ai-generation/review-templates">{{ $t('menu.reviewTemplates') }}</el-menu-item>
            </el-sub-menu>

            <el-menu-item index="/ai-generation/executions">
              <el-icon><VideoPlay /></el-icon>
              <span>{{ $t('menu.testPlan') }}</span>
            </el-menu-item>
            <el-menu-item index="/ai-generation/reports">
              <el-icon><DataAnalysis /></el-icon>
              <span>{{ $t('menu.testReport') }}</span>
            </el-menu-item>
          </template>

          <!-- 接口测试模块菜单 -->
          <template v-else-if="currentModule === 'api-testing'">
            <el-menu-item index="/api-testing/dashboard">
              <el-icon><Odometer /></el-icon>
              <span>{{ $t('menu.dashboard') }}</span>
            </el-menu-item>
            <el-menu-item index="/api-testing/projects">
              <el-icon><Folder /></el-icon>
              <span>{{ $t('menu.projectManagement') }}</span>
            </el-menu-item>
            <el-menu-item index="/api-testing/interfaces">
              <el-icon><Link /></el-icon>
              <span>{{ $t('menu.interfaceManagement') }}</span>
            </el-menu-item>
            <el-menu-item index="/api-testing/automation">
              <el-icon><VideoPlay /></el-icon>
              <span>{{ $t('menu.automationTesting') }}</span>
            </el-menu-item>
            <el-menu-item index="/api-testing/history">
              <el-icon><Timer /></el-icon>
              <span>{{ $t('menu.requestHistory') }}</span>
            </el-menu-item>
            <el-menu-item index="/api-testing/environments">
              <el-icon><Setting /></el-icon>
              <span>{{ $t('menu.environmentManagement') }}</span>
            </el-menu-item>
            <el-menu-item index="/api-testing/reports">
              <el-icon><DataAnalysis /></el-icon>
              <span>{{ $t('menu.testReport') }}</span>
            </el-menu-item>
            <el-menu-item index="/api-testing/scheduled-tasks">
              <el-icon><AlarmClock /></el-icon>
              <span>{{ $t('menu.scheduledTasks') }}</span>
            </el-menu-item>
            <el-menu-item index="/api-testing/notification-logs">
              <el-icon><Bell /></el-icon>
              <span>{{ $t('menu.notificationList') }}</span>
            </el-menu-item>
          </template>

          <!-- Bug缺陷管理模块菜单 -->
          <template v-else-if="currentModule === 'defects'">
            <el-menu-item index="/defects/dashboard">
              <el-icon><Odometer /></el-icon>
              <span>{{ $t('menu.defectDashboard') }}</span>
            </el-menu-item>
            <el-menu-item index="/defects/list">
              <el-icon><Tickets /></el-icon>
              <span>{{ $t('menu.defectList') }}</span>
            </el-menu-item>
            <el-menu-item index="/defects/create">
              <el-icon><Plus /></el-icon>
              <span>{{ $t('menu.defectCreate') }}</span>
            </el-menu-item>
            <el-menu-item index="/defects/reports">
              <el-icon><DataAnalysis /></el-icon>
              <span>{{ $t('menu.defectReport') }}</span>
            </el-menu-item>
          </template>

          <!-- UI自动化测试模块菜单 -->
          <template v-else-if="currentModule === 'ui-automation'">
            <el-menu-item index="/ui-automation/dashboard">
              <el-icon><Odometer /></el-icon>
              <span>{{ $t('menu.dashboard') }}</span>
            </el-menu-item>
            <el-menu-item index="/ui-automation/projects">
              <el-icon><Folder /></el-icon>
              <span>{{ $t('menu.projectManagement') }}</span>
            </el-menu-item>
            <el-menu-item index="/ui-automation/elements-enhanced">
              <el-icon><Aim /></el-icon>
              <span>{{ $t('menu.elementManagement') }}</span>
            </el-menu-item>
            <el-menu-item index="/ui-automation/test-cases">
              <el-icon><Document /></el-icon>
              <span>{{ $t('menu.caseManagement') }}</span>
            </el-menu-item>
            <el-menu-item index="/ui-automation/scripts-enhanced">
              <el-icon><Edit /></el-icon>
              <span>{{ $t('menu.scriptGeneration') }}</span>
            </el-menu-item>
            <el-menu-item index="/ui-automation/scripts">
              <el-icon><DocumentCopy /></el-icon>
              <span>{{ $t('menu.scriptList') }}</span>
            </el-menu-item>
            <el-menu-item index="/ui-automation/suites">
              <el-icon><Collection /></el-icon>
              <span>{{ $t('menu.suiteManagement') }}</span>
            </el-menu-item>
            <el-menu-item index="/ui-automation/executions">
              <el-icon><VideoPlay /></el-icon>
              <span>{{ $t('menu.executionRecords') }}</span>
            </el-menu-item>
            <el-menu-item index="/ui-automation/reports">
              <el-icon><DataAnalysis /></el-icon>
              <span>{{ $t('menu.testReport') }}</span>
            </el-menu-item>
            <el-menu-item index="/ui-automation/scheduled-tasks">
              <el-icon><AlarmClock /></el-icon>
              <span>{{ $t('menu.scheduledTasks') }}</span>
            </el-menu-item>
            <el-menu-item index="/ui-automation/notification-logs">
              <el-icon><Bell /></el-icon>
              <span>{{ $t('menu.notificationList') }}</span>
            </el-menu-item>
          </template>

          <!-- APP自动化测试模块菜单 -->
          <template v-else-if="currentModule === 'app-automation'">
            <el-menu-item index="/app-automation/dashboard">
              <el-icon><Odometer /></el-icon>
              <span>{{ $t('menu.dashboard') }}</span>
            </el-menu-item>
            <el-menu-item index="/app-automation/projects">
              <el-icon><Folder /></el-icon>
              <span>{{ $t('menu.projectManagement') }}</span>
            </el-menu-item>
            <el-menu-item index="/app-automation/devices">
              <el-icon><Cellphone /></el-icon>
              <span>{{ $t('menu.deviceManagement') }}</span>
            </el-menu-item>
            <el-menu-item index="/app-automation/packages">
              <el-icon><Collection /></el-icon>
              <span>{{ $t('menu.packageManagement') }}</span>
            </el-menu-item>
            <el-menu-item index="/app-automation/elements">
              <el-icon><Aim /></el-icon>
              <span>{{ $t('menu.elementManagement') }}</span>
            </el-menu-item>
            <el-menu-item index="/app-automation/scene-builder">
              <el-icon><Connection /></el-icon>
              <span>{{ $t('menu.caseDesign') }}</span>
            </el-menu-item>
            <el-menu-item index="/app-automation/test-cases">
              <el-icon><Document /></el-icon>
              <span>{{ $t('menu.testCases') }}</span>
            </el-menu-item>
            <el-menu-item index="/app-automation/test-suites">
              <el-icon><FolderOpened /></el-icon>
              <span>{{ $t('menu.suiteManagement') }}</span>
            </el-menu-item>
            <el-menu-item index="/app-automation/executions">
              <el-icon><VideoPlay /></el-icon>
              <span>{{ $t('menu.executionRecords') }}</span>
            </el-menu-item>
            <el-menu-item index="/app-automation/reports">
              <el-icon><DataAnalysis /></el-icon>
              <span>{{ $t('menu.testReport') }}</span>
            </el-menu-item>
            <el-menu-item index="/app-automation/scheduled-tasks">
              <el-icon><AlarmClock /></el-icon>
              <span>{{ $t('menu.scheduledTasks') }}</span>
            </el-menu-item>
            <el-menu-item index="/app-automation/notification-logs">
              <el-icon><Bell /></el-icon>
              <span>{{ $t('menu.notificationList') }}</span>
            </el-menu-item>
          </template>

          <!-- AI 智能模式模块菜单 -->
          <template v-else-if="currentModule === 'ai-intelligent-mode'">
            <el-menu-item index="/ai-intelligent-mode/testing">
              <el-icon><VideoPlay /></el-icon>
              <span>{{ $t('menu.aiIntelligentTesting') }}</span>
            </el-menu-item>
            <el-menu-item index="/ai-intelligent-mode/cases">
              <el-icon><Document /></el-icon>
              <span>{{ $t('menu.aiCaseManagement') }}</span>
            </el-menu-item>
            <el-menu-item index="/ai-intelligent-mode/execution-records">
              <el-icon><Timer /></el-icon>
              <span>{{ $t('menu.aiExecutionRecords') }}</span>
            </el-menu-item>

          </template>

          <!-- 配置中心模块菜单 -->
          <template v-else-if="currentModule === 'configuration'">
            <el-sub-menu index="ai-case-generation">
              <template #title>
                <el-icon><MagicStick /></el-icon>
                <span>{{ $t('menu.aiCaseGenerationConfig') }}</span>
              </template>
              <el-menu-item index="/configuration/ai-model">
                <el-icon><Cpu /></el-icon>
                <span>{{ $t('menu.aiModelConfig') }}</span>
              </el-menu-item>
              <el-menu-item index="/configuration/prompt-config">
                <el-icon><Edit /></el-icon>
                <span>{{ $t('menu.promptConfig') }}</span>
              </el-menu-item>
              <el-menu-item index="/configuration/generation-config">
                <el-icon><Setting /></el-icon>
                <span>{{ $t('menu.generationConfig') }}</span>
              </el-menu-item>
            </el-sub-menu>
            <el-menu-item index="/configuration/ui-env">
              <el-icon><Monitor /></el-icon>
              <span>{{ $t('menu.uiEnvConfig') }}</span>
            </el-menu-item>
            <el-menu-item index="/configuration/app-env">
              <el-icon><Cellphone /></el-icon>
              <span>APP环境配置</span>
            </el-menu-item>
            <el-menu-item index="/configuration/ai-mode">
              <el-icon><MagicStick /></el-icon>
              <span>{{ $t('menu.aiModeConfig') }}</span>
            </el-menu-item>
            <el-menu-item index="/configuration/scheduled-task">
              <el-icon><Timer /></el-icon>
              <span>{{ $t('menu.scheduledTaskConfig') }}</span>
            </el-menu-item>
            <el-menu-item index="/configuration/dify">
              <el-icon><ChatDotRound /></el-icon>
              <span>{{ $t('menu.difyConfig') }}</span>
            </el-menu-item>
          </template>
          <!-- 智能评分器模块菜单 -->
          <template v-else-if="currentModule === 'llm-judge'">
            <el-menu-item index="/llm-judge/dashboard">
              <el-icon><Odometer /></el-icon>
              <span>{{ $t('menu.judgeDashboard') }}</span>
            </el-menu-item>
            <el-menu-item index="/llm-judge/single">
              <el-icon><Edit /></el-icon>
              <span>{{ $t('menu.judgeSingle') }}</span>
            </el-menu-item>
            <el-menu-item index="/llm-judge/batch">
              <el-icon><DocumentCopy /></el-icon>
              <span>{{ $t('menu.judgeBatch') }}</span>
            </el-menu-item>
            <el-menu-item index="/llm-judge/history">
              <el-icon><Timer /></el-icon>
              <span>{{ $t('menu.judgeHistory') }}</span>
            </el-menu-item>
            <el-menu-item index="/llm-judge/knowledge">
              <el-icon><Collection /></el-icon>
              <span>{{ $t('menu.judgeKnowledgeBase') }}</span>
            </el-menu-item>
            <el-menu-item index="/llm-judge/rubrics">
              <el-icon><Setting /></el-icon>
              <span>{{ $t('menu.judgeRubrics') }}</span>
            </el-menu-item>
          </template>

          <!-- 监控中心模块菜单 -->
          <template v-else-if="currentModule === 'monitor'">
            <el-menu-item index="/monitor/dashboard">
              <el-icon><Odometer /></el-icon>
              <span>{{ $t('menu.monitorDashboard') }}</span>
            </el-menu-item>
            <el-menu-item index="/monitor/checks">
              <el-icon><Timer /></el-icon>
              <span>{{ $t('menu.monitorChecks') }}</span>
            </el-menu-item>
            <el-menu-item index="/monitor/alerts">
              <el-icon><Bell /></el-icon>
              <span>{{ $t('menu.monitorAlerts') }}</span>
            </el-menu-item>
            <el-menu-item index="/monitor/targets">
              <el-icon><Monitor /></el-icon>
              <span>{{ $t('menu.monitorTargets') }}</span>
            </el-menu-item>
            <el-menu-item index="/monitor/channels">
              <el-icon><Connection /></el-icon>
              <span>{{ $t('menu.monitorChannels') }}</span>
            </el-menu-item>
          </template>

          <!-- 性能测试模块菜单 -->
          <template v-else-if="currentModule === 'performance-testing'">
            <el-menu-item index="/performance-testing/dashboard">
              <el-icon><Odometer /></el-icon>
              <span>{{ $t('menu.perfDashboard') }}</span>
            </el-menu-item>
            <el-menu-item index="/performance-testing/projects">
              <el-icon><Folder /></el-icon>
              <span>{{ $t('menu.perfProjects') }}</span>
            </el-menu-item>
            <el-menu-item index="/performance-testing/scenarios">
              <el-icon><SetUp /></el-icon>
              <span>{{ $t('menu.perfScenarios') }}</span>
            </el-menu-item>
            <el-menu-item index="/performance-testing/environments">
              <el-icon><SetUp /></el-icon>
              <span>{{ $t('performanceTesting.environment.title') }}</span>
            </el-menu-item>
            <el-menu-item index="/performance-testing/account-pools">
              <el-icon><SetUp /></el-icon>
              <span>{{ $t('performanceTesting.accountPool.title') }}</span>
            </el-menu-item>
            <el-menu-item index="/performance-testing/executions">
              <el-icon><VideoPlay /></el-icon>
              <span>{{ $t('menu.perfExecutions') }}</span>
            </el-menu-item>
            <el-menu-item index="/performance-testing/comparison">
              <el-icon><TrendCharts /></el-icon>
              <span>{{ $t('menu.perfComparison') }}</span>
            </el-menu-item>
            <el-menu-item index="/performance-testing/comparison-reports">
              <el-icon><Document /></el-icon>
              <span>{{ $t('menu.perfComparisonReports') }}</span>
            </el-menu-item>
            <el-menu-item index="/performance-testing/scheduled">
              <el-icon><Timer /></el-icon>
              <span>{{ $t('menu.perfScheduled') }}</span>
            </el-menu-item>
          </template>

          <!-- MCP 管理端模块菜单 -->
          <template v-else-if="currentModule === 'mcp'">
            <el-menu-item index="/mcp/console">
              <el-icon><Connection /></el-icon>
              <span>{{ $t('menu.mcpConsole') }}</span>
            </el-menu-item>
          </template>

          <!-- 文档中心模块菜单 -->
          <template v-else-if="currentModule === 'docs'">
            <el-menu-item index="/docs-center">
              <el-icon><Document /></el-icon>
              <span>{{ $t('menu.docsCenter') }}</span>
            </el-menu-item>
          </template>
        </el-menu>
      </el-aside>

      <!-- 主体内容 -->
      <el-container>
        <!-- 顶部导航 -->
        <el-header height="60px">
          <div class="header-content">
            <div class="header-left">
              <el-button v-if="currentModule === 'performance-testing'" class="performance-menu-toggle" aria-controls="module-navigation" :aria-expanded="performanceMenuOpen" @click="performanceMenuOpen = !performanceMenuOpen">{{ $t('performanceTesting.accountPool.openMenu') }}</el-button>
              <el-breadcrumb separator="/">
                <el-breadcrumb-item :to="{ path: '/home' }">{{ $t('nav.home') }}</el-breadcrumb-item>
                <el-breadcrumb-item v-if="moduleName">{{ moduleName }}</el-breadcrumb-item>
                <el-breadcrumb-item>{{ breadcrumbTitle }}</el-breadcrumb-item>
              </el-breadcrumb>
            </div>
            <div class="header-right">
              <!-- 语言切换 -->
              <el-dropdown @command="handleLanguageChange" class="language-dropdown">
                <span class="language-selector">
                  <span class="language-flag">{{ appStore.language === 'zh-cn' ? '🇨🇳' : '🇺🇸' }}</span>
                  <span>{{ currentLanguage }}</span>
                  <el-icon class="el-icon--right"><ArrowDown /></el-icon>
                </span>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="zh-cn" :disabled="appStore.language === 'zh-cn'">
                      <span class="dropdown-flag">🇨🇳</span> 简体中文
                    </el-dropdown-item>
                    <el-dropdown-item command="en" :disabled="appStore.language === 'en'">
                      <span class="dropdown-flag">🇺🇸</span> English
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>

              <!-- 用户信息 -->
              <el-dropdown @command="handleCommand" class="user-dropdown">
                <span class="user-info">
                  <el-avatar :size="32" :src="userStore.user?.avatar" />
                  <span class="username">{{ userStore.user?.username }}</span>
                  <el-icon><ArrowDown /></el-icon>
                </span>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="profile">{{ $t('nav.profile') }}</el-dropdown-item>
                    <el-dropdown-item divided command="logout">{{ $t('nav.logout') }}</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </div>
        </el-header>

        <!-- 页面内容 -->
        <el-main>
          <router-view />
        </el-main>
      </el-container>
    </el-container>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useAppStore } from '@/stores/app'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import {
  Monitor, Folder, Document, Flag, Check, Collection, VideoPlay,
  DataAnalysis, ChatDotRound, DocumentCopy, Link, MagicStick,
  Odometer, Timer, Setting, AlarmClock, Bell, Aim, Edit, Cpu, ArrowDown, Cellphone, Connection, FolderOpened, Tickets, Plus,
  SetUp, TrendCharts
} from '@element-plus/icons-vue'
import logoSvg from '@/assets/images/logo.svg'
import logoHomePng from '@/assets/images/logo_home.png'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const appStore = useAppStore()
const { t } = useI18n()
const performanceMenuOpen = ref(false)
watch(() => route.path, () => { performanceMenuOpen.value = false })

const logoImage = computed(() => {
		return route.path === '/home' ? logoSvg : logoHomePng
	})
	

// 当前语言显示
const currentLanguage = computed(() => {
  return appStore.language === 'zh-cn' ? '简体中文' : 'English'
})

// 切换语言（无需刷新页面）
const handleLanguageChange = (lang) => {
  appStore.setLanguage(lang)
  ElMessage.success(lang === 'zh-cn' ? '语言已切换为中文' : 'Language switched to English')
}

const currentModule = computed(() => {
  if (route.path.startsWith('/ai-generation')) return 'ai-generation'
  if (route.path.startsWith('/api-testing')) return 'api-testing'
  if (route.path.startsWith('/ui-automation')) return 'ui-automation'
  if (route.path.startsWith('/defects')) return 'defects'
  if (route.path.startsWith('/app-automation')) return 'app-automation'
  if (route.path.startsWith('/ai-intelligent-mode')) return 'ai-intelligent-mode'
  if (route.path.startsWith('/configuration')) return 'configuration'
  if (route.path.startsWith('/llm-judge')) return 'llm-judge'
  if (route.path.startsWith('/monitor')) return 'monitor'
  if (route.path.startsWith('/performance-testing')) return 'performance-testing'
  if (route.path.startsWith('/mcp')) return 'mcp'
  if (route.path.startsWith('/docs-center')) return 'docs'
  return ''
})

const moduleName = computed(() => {
  const map = {
    'ai-generation': t('modules.aiGeneration'),
    'api-testing': t('modules.apiTesting'),
    'ui-automation': t('modules.uiAutomation'),
    'defects': t('modules.defects'),
    'app-automation': t('modules.appAutomation'),
    'ai-intelligent-mode': t('modules.aiIntelligentMode'),
    'configuration': t('modules.configuration'),
    'llm-judge': t('modules.llmJudge'),
    'monitor': t('modules.monitor'),
    'performance-testing': t('modules.performanceTesting'),
    'mcp': t('modules.mcp'),
    'docs': t('modules.docs')
  }
  return map[currentModule.value] || ''
})

const breadcrumbTitle = computed(() => {
  const routeMap = {
    // AI用例生成
    '/ai-generation/requirement-analysis': t('menu.aiCaseGeneration'),
    '/ai-generation/generated-testcases': t('menu.aiGeneratedTestcases'),
    '/ai-generation/projects': t('menu.projectManagement'),
    '/ai-generation/testcases': t('menu.testCases'),
    '/ai-generation/versions': t('menu.versionManagement'),
    '/ai-generation/reviews': t('menu.reviewList'),
    '/ai-generation/review-templates': t('menu.reviewTemplates'),
    '/ai-generation/testsuites': t('menu.suiteManagement'),
    '/ai-generation/executions': t('menu.executionRecords'),
    '/ai-generation/reports': t('menu.testReport'),

    // 接口测试
    '/api-testing/dashboard': t('menu.dashboard'),
    '/api-testing/projects': t('menu.projectManagement'),
    '/api-testing/interfaces': t('menu.interfaceManagement'),
    '/api-testing/automation': t('menu.automationTesting'),
    '/api-testing/history': t('menu.requestHistory'),
    '/api-testing/environments': t('menu.environmentManagement'),
    '/api-testing/reports': t('menu.testReport'),
    '/api-testing/scheduled-tasks': t('menu.scheduledTasks'),
    '/api-testing/notification-logs': t('menu.notificationList'),

    // Bug缺陷管理
    '/defects/dashboard': t('menu.defectDashboard'),
    '/defects/list': t('menu.defectList'),
    '/defects/create': t('menu.defectCreate'),
    '/defects/reports': t('menu.defectReport'),

    // UI自动化测试
    '/ui-automation/dashboard': t('menu.dashboard'),
    '/ui-automation/projects': t('menu.projectManagement'),
    '/ui-automation/elements-enhanced': t('menu.elementManagement'),
    '/ui-automation/test-cases': t('menu.caseManagement'),
    '/ui-automation/scripts-enhanced': t('menu.scriptGeneration'),
    '/ui-automation/scripts': t('menu.scriptList'),
    '/ui-automation/suites': t('menu.suiteManagement'),
    '/ui-automation/executions': t('menu.executionRecords'),
    '/ui-automation/reports': t('menu.testReport'),
    '/ui-automation/scheduled-tasks': t('menu.scheduledTasks'),
    '/ui-automation/notification-logs': t('menu.notificationList'),

    // APP自动化测试
    '/app-automation/dashboard': t('menu.dashboard'),
    '/app-automation/projects': t('menu.projectManagement'),
    '/app-automation/devices': t('menu.deviceManagement'),
    '/app-automation/packages': t('menu.packageManagement'),
    '/app-automation/elements': t('menu.elementManagement'),
    '/app-automation/scene-builder': t('menu.caseDesign'),
    '/app-automation/test-cases': t('menu.testCases'),
    '/app-automation/test-suites': t('menu.suiteManagement'),
    '/app-automation/scheduled-tasks': t('menu.scheduledTasks'),
    '/app-automation/notification-logs': t('menu.notificationList'),
    '/app-automation/executions': t('menu.executionRecords'),
    '/app-automation/reports': t('menu.testReport'),

    // AI 智能模式
    '/ai-intelligent-mode/testing': t('menu.aiIntelligentTesting'),
    '/ai-intelligent-mode/cases': t('menu.aiCaseManagement'),
    '/ai-intelligent-mode/execution-records': t('menu.aiExecutionRecords'),


    // 配置中心
    '/configuration/ai-model': t('menu.aiModelConfig'),
    '/configuration/prompt-config': t('menu.promptConfig'),
    '/configuration/generation-config': t('menu.generationConfig'),
    '/configuration/ui-env': t('menu.uiEnvConfig'),
    '/configuration/ai-mode': t('menu.aiModeConfig'),
    '/configuration/scheduled-task': t('menu.scheduledTaskConfig'),
    '/configuration/dify': t('menu.difyConfig'),
    
    // 智能评分器
    '/llm-judge/dashboard': t('menu.judgeDashboard'),
    '/llm-judge/single': t('menu.judgeSingle'),
    '/llm-judge/batch': t('menu.judgeBatch'),
    '/llm-judge/history': t('menu.judgeHistory'),
    '/llm-judge/rubrics': t('menu.judgeRubrics'),
    '/llm-judge/knowledge': t('menu.judgeKnowledgeBase'),

    '/profile': t('nav.profile')
  }
  return routeMap[route.path] || route.meta.title || ''
})

const handleCommand = (command) => {
  if (command === 'logout') {
    userStore.logout()
    ElMessage.success('退出登录成功')
    router.push('/login')
  } else if (command === 'profile') {
    router.push('/ai-generation/profile')
  }
}
</script>

<style lang="scss" scoped>
.performance-menu-toggle, .performance-menu-close, .performance-menu-backdrop { display: none; }
.layout {
  height: 100vh;
  width: 100vw;
  overflow: hidden;
}

.layout > .el-container {
  height: 100%;
  overflow: hidden;
}

.logo {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: #001529;
  color: white;
  border-bottom: 1px solid #1f1f1f;
  flex-shrink: 0;

		.logo-img {
			width: 100%;
			height: 100%;
			object-fit: fill;
		}
	}

.el-aside {
  background-color: #001529;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: width 0.3s ease;
  width: 240px !important;

  .el-menu {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
    border-right: none;
    
    &::-webkit-scrollbar {
      width: 0;
    }
  }
}

.el-menu {
  :deep(.el-sub-menu__title),
  :deep(.el-menu-item) {
    font-size: 14px;
  }
}

.el-menu--collapse {
  width: 64px !important;
  
  :deep(.el-sub-menu__title),
  :deep(.el-menu-item) {
    padding-left: 20px !important;
  }
  
  :deep(.el-sub-menu__title span),
  :deep(.el-menu-item span) {
    display: none;
  }
}

.el-container .el-container {
  height: 100%;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.el-header {
  background-color: white;
  border-bottom: 1px solid #e8e8e8;
  padding: 0;
  flex-shrink: 0;
  height: 60px !important;

  .header-content {
    height: 100%;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0 20px;
  }

  .header-left {
    flex: 1;
    overflow: hidden;
    
    :deep(.el-breadcrumb) {
      font-size: 14px;
    }
  }

  .user-info {
    display: flex;
    align-items: center;
    cursor: pointer;
    white-space: nowrap;

    .username {
      margin: 0 8px;
      color: #303133;
      font-size: 14px;
    }
  }
}

.header-right {
    display: flex;
    align-items: center;
    gap: 20px;
  }

  .language-dropdown {
    .language-selector {
      display: flex;
      align-items: center;
      cursor: pointer;
      color: #303133;
      font-size: 14px;
      outline: none;

      &:focus {
        outline: none;
      }

      .language-flag {
        font-size: 18px;
        margin-right: 5px;
        line-height: 1;
      }

      span {
        margin: 0 4px;
      }

      &:hover {
        color: #1890ff;
      }
    }
  }

  .dropdown-flag {
    font-size: 16px;
    margin-right: 5px;
  }

  .user-dropdown {
    .user-info {
      display: flex;
      align-items: center;
      cursor: pointer;
      white-space: nowrap;

      .username {
        margin: 0 8px;
        color: #303133;
      }
    }
  }

.el-main {
  background-color: #f5f5f5;
  padding: 20px;
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
}

@media screen and (max-width: 1920px) {
  .el-aside {
    width: 220px !important;
  }
  
  .el-main {
    padding: 18px;
  }
}

@media screen and (max-width: 1600px) {
  .el-aside {
    width: 200px !important;
  }
  
  .el-main {
    padding: 16px;
  }

  .el-menu {
    :deep(.el-sub-menu__title),
    :deep(.el-menu-item) {
      font-size: 13px;
    }
  }
}

@media screen and (max-width: 1440px) {
  .el-aside {
    width: 180px !important;
  }
  
  .el-main {
    padding: 14px;
  }

  .el-menu {
    :deep(.el-sub-menu__title),
    :deep(.el-menu-item) {
      font-size: 13px;
    }
  }
}

@media screen and (max-width: 1366px) {
  .el-aside {
    width: 180px !important;
  }
  
  .el-main {
    padding: 12px;
  }

  .el-header {
    height: 56px !important;
  }
  
  .el-menu {
    :deep(.el-sub-menu__title),
    :deep(.el-menu-item) {
      font-size: 12px;
    }
  }
}

@media screen and (max-width: 1280px) {
  .el-aside {
    width: 160px !important;
  }
  
  .el-main {
    padding: 12px;
  }

  .el-header {
    height: 56px !important;
    
    .header-content {
      padding: 0 15px;
    }
  }
  
  .el-menu {
    :deep(.el-sub-menu__title),
    :deep(.el-menu-item) {
      font-size: 12px;
      padding-left: 15px !important;
    }
  }
}

@media screen and (max-width: 1024px) {
  .el-aside {
    width: 140px !important;
  }
  
  .el-main {
    padding: 10px;
  }

  .el-header {
    height: 52px !important;
    
    .header-content {
      padding: 0 12px;
    }
  }
  
  .el-menu {
    :deep(.el-sub-menu__title),
    :deep(.el-menu-item) {
      font-size: 12px;
      padding-left: 12px !important;
    }
  }
  
  .user-info .username {
    display: none;
  }
}

@media screen and (max-width: 768px) {
  .performance-menu-toggle, .performance-menu-close { display: inline-flex; }
  .performance-menu-toggle { margin-right: 8px; }
  .performance-menu-close { margin: 8px; }
  .performance-menu-backdrop { display: block; position: fixed; inset: 0; background: #0006; z-index: 999; border: 0; }
  .el-aside {
    position: fixed;
    left: 0;
    top: 0;
    z-index: 1000;
    width: 240px !important;
    transform: translateX(-100%);
    transition: transform 0.3s ease;
    
    &.mobile-open {
      transform: translateX(0);
    }
  }
  
  .el-main {
    padding: 8px;
  }

  .el-header {
    height: 50px !important;
    
    .header-content {
      padding: 0 10px;
    }
    
    .header-left {
      :deep(.el-breadcrumb__item) {
        &:not(:last-child) {
          display: none;
        }
      }
    }
  }
}

@media screen and (max-width: 480px) {
  .el-aside {
    width: 220px !important;
  }
  
  .el-main {
    padding: 6px;
  }

  .el-header {
    height: 48px !important;
    
    .header-content {
      padding: 0 8px;
    }
  }
  
  .user-info {
    .el-avatar {
      width: 28px !important;
      height: 28px !important;
    }
  }
}
</style>
