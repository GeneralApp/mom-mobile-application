import { apiRequest } from '../lib/api.js';
import { uploadObject } from './storage.js';

export async function uploadToStorage({
  file,
  bucket,
  workspaceId,
  meetingId,
  customFileName,
}) {
  const originalName = file?.name || 'recording.webm';
  const fileName =
    customFileName ||
    `${Date.now()}_${originalName.replace(/[^a-zA-Z0-9._-]/g, '_')}`;
  const filePath = `${workspaceId}/${meetingId}/${fileName}`;

  try {
    const stored = await uploadObject({
      bucket,
      path: filePath,
      file,
      upsert: true,
    });

    return {
      path: stored?.path || filePath,
      fileName,
      fileType: file.type || 'application/octet-stream',
      fileSize: file.size,
    };
  } catch (error) {
    throw new Error(`Failed to upload to storage: ${error.message}`);
  }
}

export async function confirmAttachmentUpload({
  meeting_id,
  bucket,
  path,
  file_name,
  file_type,
  file_size,
  attachment_type = 'meeting_attachment',
}) {
  return await apiRequest('/api/v1/attachments/confirm', {
    method: 'POST',
    body: JSON.stringify({
      meeting_id,
      bucket,
      path,
      file_name,
      file_type,
      file_size,
      attachment_type,
    }),
  });
}
