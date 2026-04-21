import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import { z } from 'zod'
import { spawn } from 'child_process'
import { fileURLToPath } from 'url'
import path from 'path'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const METRICS_SCRIPT = path.join(__dirname, 'get_metrics.py')

const server = new McpServer({
  name: 'xhs-metrics-mcp',
  version: '1.0.0'
})

function runPython(args) {
  return new Promise((resolve, reject) => {
    const proc = spawn('python3', [METRICS_SCRIPT, ...args])
    let stdout = ''
    let stderr = ''
    proc.stdout.on('data', d => stdout += d)
    proc.stderr.on('data', d => stderr += d)
    proc.on('close', code => {
      if (code === 0) resolve(stdout.trim())
      else reject(new Error(stderr.trim() || stdout.trim()))
    })
  })
}

server.tool(
  'xhs_get_metrics',
  '获取已发布小红书笔记的数据表现（点赞/收藏/评论/分享等统计）',
  {
    noteIds: z.array(z.string()).describe('笔记ID列表'),
    summary: z.boolean().optional().default(false).describe('是否同时返回笔记摘要（标题/封面等），默认只返回数字统计')
  },
  async ({ noteIds, summary }) => {
    try {
      const args = ['--note-ids', ...noteIds]
      if (summary) args.push('--summary')
      const output = await runPython(args)
      return {
        content: [{ type: 'text', text: output }]
      }
    } catch (err) {
      return {
        content: [{ type: 'text', text: err.message }],
        isError: true
      }
    }
  }
)

const transport = new StdioServerTransport()
await server.connect(transport)
