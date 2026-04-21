import { chromium } from 'playwright';
import { getValidSession } from './mcp/explore/scripts/auth.js';

const session = getValidSession();
console.log('📋 账号:', session.accountName);
console.log('🔗 Session 路径:', session.storagePath);

// 启动浏览器并保持打开
const browser = await chromium.launch({ headless: false });
const context = await browser.newContext({
  storageState: session.storagePath,
  userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
});

const page = await context.newPage();

console.log('\n========================================');
console.log('🔄 第一步：打开小红书首页');
console.log('========================================');
await page.goto('https://www.xiaohongshu.com/', { waitUntil: 'networkidle' });
await page.waitForTimeout(2000);

let result = await page.evaluate(() => ({
  url: window.location.href,
  title: document.title,
  hasContent: document.body.innerText.length > 100
}));
console.log('✅ 首页加载成功：', result.url);
console.log('   标题：', result.title);

// 暂停让用户观察
console.log('\n⏸️  浏览器保持开启，请查看首页是否正常加载');
console.log('按 Enter 继续到第二步（搜索）...');
await new Promise(r => {
  process.stdin.once('data', r);
});

console.log('\n========================================');
console.log('🔄 第二步：进行搜索');
console.log('========================================');

const keyword = '海外求职';
const searchUrl = `https://www.xiaohongshu.com/search_result?keyword=${encodeURIComponent(keyword)}`;
console.log(`搜索关键词: "${keyword}"`);
console.log(`搜索 URL: ${searchUrl}`);

await page.goto(searchUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(3000);

result = await page.evaluate(() => ({
  url: window.location.href,
  isLoginPage: window.location.href.includes('login'),
  isVerifyPage: window.location.href.includes('verify'),
  bodyText: document.body.innerText.slice(0, 500)
}));

console.log('📍 最终 URL:', result.url);
console.log('🔐 是否被重定向到登录页:', result.isLoginPage);
console.log('🔐 是否被重定向到验证页:', result.isVerifyPage);
console.log('\n📄 页面内容（前 500 字符）:');
console.log('---');
console.log(result.bodyText);
console.log('---');

if (result.isVerifyPage) {
  console.log('\n❌ 搜索被重定向到验证页面');
  console.log('💡 这是小红书的反爬虫措施，需要手机 App 扫码验证');
} else {
  // 尝试找列表项
  const items = await page.evaluate(() => {
    const selectors = [
      '[data-v-a264d] .note-item',
      '.search-feed-item',
      '.note-list-item',
      '[class*="feed-item"]',
      'article'
    ];
    
    for (const sel of selectors) {
      const els = document.querySelectorAll(sel);
      if (els.length > 0) {
        return {
          selector: sel,
          count: els.length,
          found: true,
          firstItem: els[0]?.outerHTML?.slice(0, 300)
        };
      }
    }
    return { selector: 'N/A', count: 0, found: false };
  });
  
  console.log('\n📋 搜索结果项目：');
  console.log('   选择器:', items.selector);
  console.log('   找到项目数:', items.count);
  console.log('   第一项 HTML:', items.firstItem?.slice(0, 100) + '...');
}

console.log('\n========================================');
console.log('✅ 测试完成 - 浏览器保持打开');
console.log('========================================');
console.log('\n📌 你可以在浏览器中进行以下操作观察：');
console.log('   1. 检查页面是否正常加载');
console.log('   2. 查看 DevTools 中的 Network 标签，观察网络请求');
console.log('   3. 检查 Console 中是否有错误信息');
console.log('   4. 观察 Cookie 是否已保存');
console.log('\n按 Ctrl+C 关闭浏览器');

// 保持程序运行，直到用户中断
await new Promise(() => {});
