import { chromium } from 'playwright';
import { getValidSession } from './mcp/explore/scripts/auth.js';

const session = getValidSession();
console.log('\n' + '='.repeat(80));
console.log('🌐 小红书浏览器 - 长期保持打开（你可以自由观察和操作）');
console.log('='.repeat(80));
console.log('\n📋 账号信息：');
console.log('   账号名: ' + session.accountName);
console.log('   Session 路径: ' + session.storagePath);

const browser = await chromium.launch({ 
  headless: false,
  args: [
    '--start-maximized'  // 最大化窗口
  ]
});

const context = await browser.newContext({
  storageState: session.storagePath,
  userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
});

const page = await context.newPage();

console.log('\n' + '='.repeat(80));
console.log('📍 正在打开首页...');
console.log('='.repeat(80));

try {
  await page.goto('https://www.xiaohongshu.com/', { waitUntil: 'networkidle' });
  console.log('✅ 首页已加载');
  console.log('   URL: ' + page.url());
} catch (e) {
  console.log('⚠️  首页加载出现问题：' + e.message);
}

console.log('\n' + '='.repeat(80));
console.log('📌 浏览器现在保持打开中');
console.log('='.repeat(80));
console.log('\n你可以：');
console.log('  1. 在浏览器中自由浏览和点击');
console.log('  2. 在搜索框输入关键词进行搜索');
console.log('  3. 打开 DevTools (Cmd+Option+I) 查看网络请求和 Console');
console.log('  4. 观察是否出现验证弹窗');
console.log('  5. 按 Ctrl+C 结束脚本并关闭浏览器');
console.log('\n' + '='.repeat(80));
console.log('⏳ 浏览器保持打开，等待你的操作...');
console.log('='.repeat(80) + '\n');

// 保持程序运行
process.on('SIGINT', async () => {
  console.log('\n\n👋 正在关闭浏览器...');
  await browser.close();
  process.exit(0);
});

// 防止程序退出
await new Promise(() => {});
