const $ = id => document.getElementById(id);

$('saveKey').addEventListener('click', () => {
  chrome.runtime.sendMessage({ action: 'set_api_key', key: $('apiKey').value });
  $('apiKey').value = '';
  $('saveKey').textContent = '已保存 ✓';
  setTimeout(() => $('saveKey').textContent = '保存', 1500);
});

$('start').addEventListener('click', async () => {
  const topic = $('topic').value.trim();
  if (!topic) return alert('请输入研究主题');

  const kw = $('keywords').value.trim();
  const keywords = kw ? kw.split(/[,，]/).map(s => s.trim()).filter(Boolean) : null;

  $('start').disabled = true;
  $('start').textContent = '研究中...';
  $('status').style.display = 'block';
  $('status').textContent = '启动研究流程...\n';

  chrome.runtime.sendMessage(
    { action: 'start_research', topic, keywords },
    (resp) => {
      $('start').disabled = false;
      $('start').textContent = '开始研究';

      if (resp?.error) {
        $('status').textContent += `\n❌ Error: ${resp.error}`;
        return;
      }

      const r = resp.report;
      $('status').textContent += `\n✅ 完成！收集了 ${r.notes.length} 篇笔记\n`;
      $('status').textContent += `\n关键词: ${r.keywords.join(', ')}`;
      r.notes.forEach((n, i) => {
        $('status').textContent += `\n${i+1}. ${n.title} — ${n.author} (${n.likes} likes)`;
      });

      // Log
      $('status').textContent += '\n\n--- 执行日志 ---';
      r.log.forEach(e => {
        $('status').textContent += `\n[${e.time}] ${e.action}: ${e.detail}`;
      });
    }
  );
});
