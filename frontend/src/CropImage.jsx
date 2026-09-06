import React, { useEffect, useState } from 'react'
import { apiBlob } from './api'

// The exact pixels a value was read from — authenticated blob fetch (JWT
// can't ride on <img src>), rendered as an image with its pixel bbox.
export default function CropImage({ cropId, maxWidth = 320 }) {
  const [url, setUrl] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let alive = true
    apiBlob(`/crops/${cropId}/image`)
      .then((b) => { if (alive) setUrl(URL.createObjectURL(b)) })
      .catch((e) => { if (alive) setError(e.message) })
    return () => { alive = false }
  }, [cropId])

  if (error) return <span className="muted">crop unavailable ({error})</span>
  if (!url) return <span className="muted">loading crop…</span>
  return <img src={url} alt={`evidence crop #${cropId}`} className="crop-img" style={{ maxWidth }} />
}
