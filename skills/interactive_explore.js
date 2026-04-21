import { chromium } from 'playwright';
import { getValidSession } from './mcp/explore/scripts/auth.js';
import * as readline from 'readline';

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout
});

function prompt(question) {
  return new Promise(resolve => {
    rl.question(question, resolve);
  });
}

const session = getValidSession();
console.log('\n' + '='.repeat(70));
console.log('�� 小红书爬取交互式浏览器');
console.log('='.repeat(70));
console.log('📋 账号:', session.accountName);
console.log('🔗 Session 路径:', session.storagePath);

// 启动浏览器
const browser = await chromium.launch({ headless: false });
const context = await browser.newContext({
  storageState: session.storagePath,
  userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
});

const page = await context.newPage();

console.log('\n✅ 浏览器已启动（有头模式），你可以看到窗口');
console.log('📌 当前操作命令：');
console.log('   1 - 打开首页');
console.log('   2 - 进行搜索');
console.log('   3 - 截图当前页面');
console.log('   4 - 查看当前 URL');
console.log('   5 - 打开 DevTools');
console.log('   0 - 关闭浏览器\n');

let running = true;
while (running) {
  const cmd = await prompt('请输入命令 (0-5): ');
  
  switch(cmd) {
    case '1':
      console.log('🔄 打开首页...');
      await page.goto('https://www.xiaohongshu.com/', { waitUntil: 'networkidle' });
      await page.waitForTimeout(2000);
      console.log('✅ 首页已加载');
      break;
    
    case '2':
      const keyword = await prompt('请输入搜索关键词 (默认: 英国留学求职): ') || '英国留学求职';
      console.log(`🔄 搜索"${keyword}"...`);
      const searchUrl = `https://www.xiaohongshu.com/search_result?keyword=${encodeURIComponent(keyword)}`;
      console.log(`📍 URL: ${searchUrl}`);
      await page.goto(searchUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForTimeout(3000);
      
      const status = await page.evaluate(() => ({
        url: window.location.href,
        isVerifyPage: window.location.href.includes('verify'),
        hasContent: document.body.innerText.length > 100
      }));
      
      if (status.isVerifyPage) {
        console.log('❌ 被重定向到验证页面');
        console.log('💡 小红书需要手机 App 扫码验证');
      } else {
        console.log('✅ 搜索页面已加载');
        console.log(`📍 最终 URL: ${status.url}`);
      }
      break;
    
    case '3':
      const screenshotPath = `screenshot_${Date.now()}.png`;
      await page.screenshot({ path: screenshotPath });
      console.log(`✅ 截图已保存: ${screenshotPath}`);
      break;
    
    case '4':
      const currentUrl = page.url();
      console.log(`📍 当前 URL: ${currentUrl}`);
      break;
    
    case '5':
      console.log('⚙️  打开 DevTools (Cmd+Option+I)');
      await page.keyboard.press('F12');
      break;
    
    case '0':
      console.log('👋 关闭浏览器...');
      running = false;
      break;
    
    default:
      console.log('❌ 无效命令');
  }
}

rl.close();
await browser.close();
console.log('✅ 已关闭');
