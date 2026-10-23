import { useState, useEffect } from 'react'
import type { HealthStatus } from '@/types'
import { fetchHealth } from '@/services/api'

export function useHealth() {
  const [data, setData] = useState<HealthStatus | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let isMounted = true

    fetchHealth()
      .then((res) => {
        if (isMounted) {
          setData(res)
          setError(null)
          setLoading(false)
        }
      })
      .catch((err: Error) => {
        if (isMounted) {
          setError(err.message)
          setLoading(false)
        }
      })

    return () => {
      isMounted = false
    }
  }, [])

  return { data, loading, error }
}
