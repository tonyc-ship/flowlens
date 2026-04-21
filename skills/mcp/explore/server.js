import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import { z } from 'zod'
import { explore } from './scripts/explore.js'

const server = new McpServer({
  name: 'xhs-explore-mcp',
  version: '1.0.0'
})

server.tool(
  'xhs_explore',
  '采集小红书关键词下的爆款笔记，返回互动数据（点赞/收藏/评论/爆款评分）',
  {
    keyword: z.string().describe('搜索关键词，例如：海外求职'),
    searchLimit: z.number().optional().default(20).describe('抓取笔记数量上限（默认20）'),
    viralThreshold: z.number().optional().default(60).describe('爆款评分门槛 0-100（默认60）'),
    authorLimit: z.number().optional().default(20).describe('返回爆款作者数量上限（默认20）')
  },
  async ({ keyword, searchLimit, viralThreshold, authorLimit }) => {
    try {
      const result = await explore({ keyword, searchLimit, viralThreshold, authorLimit })
      return {
        content: [{
          type: 'text',
          text: JSON.stringify(result, null, 2)
        }]
      }
    } catch (err) {
      return {
        content: [{
          type: 'text',
          text: JSON.stringify({ error: err.message })
        }],
        isError: true
      }
    }
  }
)

const transport = new StdioServerTransport()
await server.connect(transport)
