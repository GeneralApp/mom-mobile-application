import { apiRequest } from '../lib/api.js';

// Files are uploaded to the backend in parts so each request stays small
// (serverless hosts cap request bodies at a few MB). The backend stores
// them in MongoDB.
const DEFAULT_PART_SIZE = 4 * 1024 * 1024;

export async function uploadObject({ bucket, path, file, upsert = false, contentType }) {
  const type = contentType || file.type || 'application/octet-stream';

  const { upload_id: uploadId, chunk_size: chunkSize } = await apiRequest(
    `/api/v1/storage/${encodeURIComponent(bucket)}/uploads`,
    {
      method: 'POST',
      body: JSON.stringify({ path, content_type: type, upsert }),
    }
  );

  const partSize = Math.min(chunkSize || DEFAULT_PART_SIZE, DEFAULT_PART_SIZE);
  const partCount = Math.max(1, Math.ceil(file.size / partSize));
  const base = `/api/v1/storage/${encodeURIComponent(bucket)}/uploads/${uploadId}`;

  try {
    for (let index = 0; index < partCount; index += 1) {
      const part = file.slice(index * partSize, (index + 1) * partSize);

      await apiRequest(`${base}/parts/${index}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: part,
        timeout: 120000,
      });
    }

    return await apiRequest(`${base}/complete`, {
      method: 'POST',
      body: JSON.stringify({ part_count: partCount }),
      timeout: 120000,
    });
  } catch (error) {
    apiRequest(base, { method: 'DELETE' }).catch(() => {});
    throw error;
  }
}

export async function removeObject(bucket, path) {
  if (!path) return null;

  return await apiRequest(
    `/api/v1/storage/${encodeURIComponent(bucket)}/object?path=${encodeURIComponent(path)}`,
    { method: 'DELETE' }
  );
}
