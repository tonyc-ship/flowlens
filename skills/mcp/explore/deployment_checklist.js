#!/usr/bin/env node

/**
 * 部署检查清单：explore_v3.js 上线前的最后验证
 */

import fs from 'fs'
import path from 'path'

const colors = {
  reset: '\x1b[0m',
  green: '\x1b[32m',
  red: '\x1b[31m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  cyan: '\x1b[36m',
  bold: '\x1b[1m'
}

function log(msg, color = 'reset') {
  console.log(`${colors[color]}${msg}${colors.reset}`)
}

function title(msg) {
  log(`\n${'═'.repeat(70)}`, 'cyan')
  log(`  ${msg}`, 'cyan')
  log(`${'═'.repeat(70)}`, 'cyan')
}

function checkmark(passed, msg) {
  const status = passed ? '✅' : '❌'
  const color = passed ? 'green' : 'red'
  log(`${status} ${msg}`, color)
  return passed
}

async function runDeploymentChecklist() {
  let allPassed = true
  const baseDir = path.dirname(new URL(import.meta.url).pathname)

  title('部署前检查清单')

  // 1. 文件检查
  log('\n【1】文件完整性', 'bold')
  
  const requiredFiles = [
    { path: 'scripts/explore_v3.js', name: '新版 explore_v3.js' },
    { path: 'scripts/test_viral_score.js', name: '评分测试' },
    { path: 'scripts/integration_test.js', name: '集成测试' },
    { path: 'IMPROVEMENT_GUIDE.md', name: '改进说明文档' },
    { path: 'IMPLEMENTATION_SUMMARY.md', name: '实现总结文档' }
  ]

  requiredFiles.forEach(file => {
    const fullPath = path.join(baseDir, file.path)
    const exists = fs.existsSync(fullPath)
    allPassed &= checkmark(exists, `${file.name} (${file.path})`)
  })

  // 2. 代码质量检查
  log('\n【2】代码质量', 'bold')
  
  const explore_v3_path = path.join(baseDir, 'scripts/explore_v3.js')
  const content = fs.readFileSync(explore_v3_path, 'utf-8')
  
  const hasNeedLogin = content.includes('async function needsLogin')
  allPassed &= checkmark(hasNeedLogin, '包含 needsLogin() 函数')
  
  const hasNeedsCaptcha = content.includes('async function needsCaptcha')
  allPassed &= checkmark(hasNeedsCaptcha, '包含 needsCaptcha() 函数')
  
  const hasSearchAPI = content.includes('async function searchKeywordByAPI')
  allPassed &= checkmark(hasSearchAPI, '包含 searchKeywordByAPI() 函数')
  
  const hasCalculateViral = content.includes('function calculateViralScore')
  allPassed &= checkmark(hasCalculateViral, '包含 calculateViralScore() 函数')
  
  const hasAggregate = content.includes('function aggregateByAuthor')
  allPassed &= checkmark(hasAggregate, '包含 aggregateByAuthor() 函数')
  
  const hasExplore = content.includes('export async function explore')
  allPassed &= checkmark(hasExplore, '导出 explore() 主函数')

  // 3. 功能完整性
  log('\n【3】功能完整性', 'bold')
  
  const hasChromiumImport = content.includes("import { chromium }")
  allPassed &= checkmark(hasChromiumImport, 'Playwright chromium 导入')
  
  const hasGetValidSession = content.includes('getValidSession')
  allPassed &= checkmark(hasGetValidSession, '集成 auth.js 认证')
  
  const hasStorageState = content.includes('storageState:')
  allPassed &= checkmark(hasStorageState, '支持 StorageState')
  
  const hasResponseListener = content.includes("page.on('response'")
  allPassed &= checkmark(hasResponseListener, 'API 响应拦截')
  
  const hasTwoScoring = content.includes('absoluteScore') && content.includes('relativeScore')
  allPassed &= checkmark(hasTwoScoring, '两维度爆款评分')
  
  const hasDataSource = content.includes('data_source:')
  allPassed &= checkmark(hasDataSource, '数据溯源标记')

  // 4. 特性检查
  log('\n【4】特性检查', 'bold')
  
  const hasChineseWarning = content.includes("IP存在风险")
  allPassed &= checkmark(hasChineseWarning, '检测中文安全警告')
  
  const hasDOMFallback = content.includes('$$eval')
  allPassed &= checkmark(hasDOMFallback, '备选 DOM 提取方案')
  
  const hasErrorHandling = content.includes('try') && content.includes('catch')
  allPassed &= checkmark(hasErrorHandling, '完整的错误处理')
  
  const has120sWait = content.includes('120')
  allPassed &= checkmark(has120sWait, '120 秒验证码等待')

  // 5. 文档检查
  log('\n【5】文档完整性', 'bold')
  
  const improvement = fs.readFileSync(path.join(baseDir, 'IMPROVEMENT_GUIDE.md'), 'utf-8')
  const hasImproveGuide = improvement.includes('两维度爆款评分')
  allPassed &= checkmark(hasImproveGuide, 'IMPROVEMENT_GUIDE.md 包含评分公式')
  
  const summary = fs.readFileSync(path.join(baseDir, 'IMPLEMENTATION_SUMMARY.md'), 'utf-8')
  const hasSummary = summary.includes('流程架构') && summary.includes('测试结果')
  allPassed &= checkmark(hasSummary, 'IMPLEMENTATION_SUMMARY.md 包含完整说明')

  // 6. 依赖检查
  log('\n【6】依赖检查', 'bold')
  
  // 检查 auth.js
  const authPath = path.join(baseDir, 'auth.js')
  const hasAuthJs = fs.existsSync(authPath)
  allPassed &= checkmark(hasAuthJs, '存在 auth.js')
  
  if (hasAuthJs) {
    const authContent = fs.readFileSync(authPath, 'utf-8')
    const hasGetValidSession = authContent.includes('getValidSession')
    allPassed &= checkmark(hasGetValidSession, 'auth.js 导出 getValidSession()')
  }

  // 7. 测试覆盖
  log('\n【7】测试覆盖', 'bold')
  
  const testContent = fs.readFileSync(path.join(baseDir, 'scripts/integration_test.js'), 'utf-8')
  const testCases = [
    { pattern: 'testStorageState', name: 'StorageState 检查' },
    { pattern: 'testLoginDetection', name: '登录检测' },
    { pattern: 'testCaptchaDetection', name: '验证码检测' },
    { pattern: 'testViralScoring', name: '爆款评分' },
    { pattern: 'testAuthorAggregation', name: '作者聚合' }
  ]
  
  testCases.forEach(tc => {
    const hasTest = testContent.includes(`function ${tc.pattern}`)
    allPassed &= checkmark(hasTest, `测试覆盖：${tc.name}`)
  })

  // 8. 向后兼容性
  log('\n【8】向后兼容性', 'bold')
  
  const exportFormat = content.includes('export async function explore')
  allPassed &= checkmark(exportFormat, 'export async function explore() 格式')
  
  const paramFormat = content.includes('{ keyword')
  allPassed &= checkmark(paramFormat, '接受配置对象参数')
  
  const returnFormat = content.includes('keyword:') && content.includes('authors:')
  allPassed &= checkmark(returnFormat, '返回标准结构')

  // 总结
  title('检查总结')
  
  if (allPassed) {
    log('✅ 所有检查通过！explore_v3.js 已准备就绪', 'green')
    log('\n下一步部署：', 'blue')
    log('  1. 备份: cp explore.js explore_backup.js', 'blue')
    log('  2. 启用: mv explore_v3.js explore.js', 'blue')
    log('  3. 测试: node -e "import {explore} from \'./scripts/explore.js\'; ..."', 'blue')
    log('  4. 监控: 关注返回的 data_source 字段', 'blue')
  } else {
    log('⚠️  部分检查未通过，请检查上述标记为 ❌ 的项目', 'yellow')
    log('\n常见问题：', 'yellow')
    log('  - 文件缺失？检查 scripts 目录', 'yellow')
    log('  - 函数缺失？重新生成 explore_v3.js', 'yellow')
    log('  - 导入失败？检查 auth.js 是否存在', 'yellow')
  }
  
  log(`\n${'═'.repeat(70)}\n`, 'cyan')
  
  return allPassed
}

runDeploymentChecklist()
  .then(passed => process.exit(passed ? 0 : 1))
  .catch(err => {
    console.error('部署检查失败:', err)
    process.exit(1)
  })
