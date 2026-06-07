import type { NextApiRequest, NextApiResponse } from 'next'

type LiveResponse = {
  status: string
  timestamp: string
}

export default function handler(
  req: NextApiRequest,
  res: NextApiResponse<LiveResponse>
) {
  res.status(200).json({
    status: 'ok',
    timestamp: new Date().toISOString()
  })
}
