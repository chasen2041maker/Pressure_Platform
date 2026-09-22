import elementZhCn from 'element-plus/es/locale/lang/zh-cn'
import common from './common.js'
import navModule from './nav.js'
import auth from './auth.js'
import projectModule from './project.js'
import testcaseModule from './testcase.js'
import execution from './execution.js'
import report from './report.js'
import reviewModule from './review.js'
import version from './version.js'
import requirementModule from './requirement.js'
import apiTestingModule from './api-testing.js'
import uiAutomationModule from './ui-automation.js'
import appAutomationModule from './app-automation.js'
import configurationModule from './configuration.js'
import assistantModule from './assistant.js'
import dataFactoryModule from './data-factory.js'
import notificationModule from './notification.js'
import llmJudgeModule from './llm-judge.js'
import monitorModule from './monitor.js'
import performanceTestingModule from './performance-testing.js'
import mcpModule from './mcp.js'
import docsModule from './docs.js'

export default {
  // 模块化导出
  common,
  auth,
  execution,
  report,
  version,

  // 导航模块
  nav: navModule.nav,
  modules: navModule.modules,
  menu: navModule.menu,

  // 项目模块
  project: projectModule.project,
  home: projectModule.home,
  profile: projectModule.profile,

  // 测试用例模块
  testcase: testcaseModule.testcase,
  testSuite: testcaseModule.testSuite,

  // 评审模块
  reviewList: reviewModule.reviewList,
  reviewForm: reviewModule.reviewForm,
  reviewDetail: reviewModule.reviewDetail,
  reviewTemplate: reviewModule.reviewTemplate,

  // 需求分析模块
  requirementAnalysis: requirementModule.requirementAnalysis,
  generatedTestCases: requirementModule.generatedTestCases,
  promptConfig: requirementModule.promptConfig,
  generationConfig: requirementModule.generationConfig,
  taskDetail: requirementModule.taskDetail,
  configGuide: requirementModule.configGuide,

  // API测试模块
  apiTesting: apiTestingModule,

  // UI自动化测试模块
  uiAutomation: uiAutomationModule,

  // APP自动化测试模块
  appAutomation: appAutomationModule,

  // 配置中心模块
  configuration: configurationModule,

  // AI助手模块
  assistant: assistantModule,

  // 数据工厂模块
  dataFactory: dataFactoryModule,

  // 通知模块
  notification: notificationModule,

  // 智能评分器模块
  llmJudge: llmJudgeModule,

  // 监控中心模块
  monitor: monitorModule,

  // 性能测试模块
  performanceTesting: performanceTestingModule,

  // MCP 管理端模块
  mcp: mcpModule,

  // 文档中心
  docs: docsModule.docs,

  // Element Plus 语言包
  ...elementZhCn
}
