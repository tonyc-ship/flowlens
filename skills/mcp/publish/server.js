import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import { z } from 'zod'
import { spawn } from 'child_process'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const PUBLISH_SCRIPT = path.resolve(__dirname, '../../scripts/publish_xhs.py')

const server = new McpServer({
  name: 'xhs-publish-mcp',
  version: '1.0.0'
})

function runPython(args) {
  return new Promise((resolve, reject) => {
    const proc = spawn('python3', [PUBLISH_SCRIPT, ...args], {
      cwd: path.resolve(__dirname, '../..')
    })
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
  'xhs_publish',
  '发布图文笔记到小红书',
  {
    title: z.string().describe('笔记标题（不超过20字）'),
    desc: z.string().optional().default('').describe('笔记正文/描述'),
    images: z.array(z.string()).describe('图片文件绝对路径列表（第一张为封面）'),
    public: z.boolean().optional().default(false).describe('是否公开发布（默认仅自己可见）'),
    postTime: z.string().optional().describe('定时发布时间，格式：2024-01-01 12:00:00'),
    dryRun: z.boolean().optional().default(false).describe('仅验证不发布')
  },
  async ({ title, desc, images, public: isPublic, postTime, dryRun }) => {
    try {
      const args = ['--title', title, '--desc', desc, '--images', ...images]
      if (isPublic) args.push('--public')
      if (postTime) args.push('--post-time', postTime)
      if (dryRun) args.push('--dry-run')

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
