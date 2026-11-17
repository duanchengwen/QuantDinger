import request from '@/utils/request'

export async function paperRequest (path = '/state', method = 'get', data) {
  const result = await request({ url: `/api/ashare-paper${path}`, method, data })
  if (!result || result.code !== 1) throw new Error((result && result.msg) || 'Paper request failed')
  return result.data
}
