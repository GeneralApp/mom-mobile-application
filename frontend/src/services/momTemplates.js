import { apiRequest } from '../lib/api.js';
import { removeObject, uploadObject } from './storage.js';

const BUCKET = 'workspace-mom-template';

function sanitizeFileName(fileName) {
  return fileName
    .trim()
    .replace(/[^\w.\- ]+/g, '')
    .replace(/\s+/g, '-');
}

export async function getMOMTemplate(workspaceId) {
  return await apiRequest(
    `/api/v1/mom-templates/workspace/${workspaceId}`,
    {
      method: 'GET',
    }
  );
}

export async function uploadMOMTemplate(
  workspaceId,
  file,
  sourceType = 'template'
) {
  if (!file) {
    throw new Error('Please select a file');
  }

  const allowedTypes = [
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  ];

  if (!allowedTypes.includes(file.type)) {
    throw new Error('Only PDF, DOC, and DOCX files are supported');
  }

  const maxSize = 10 * 1024 * 1024;

  if (file.size > maxSize) {
    throw new Error('File size must be 10 MB or less');
  }

  const safeName = sanitizeFileName(file.name);

  const filePath = `${workspaceId}/${Date.now()}-${safeName}`;

  try {
    await uploadObject({
      bucket: BUCKET,
      path: filePath,
      file,
      upsert: false,
      contentType: file.type || 'application/octet-stream',
    });
  } catch (uploadError) {
    throw new Error(uploadError.message || 'Failed to upload file');
  }

  try {
    return await apiRequest(
      `/api/v1/mom-templates/workspace/${workspaceId}`,
      {
        method: 'POST',
        body: JSON.stringify({
          name: file.name,
          file_name: file.name,
          file_path: filePath,
          file_type: file.type || null,
          file_size: file.size,
          source_type: sourceType,
        }),
      }
    );
  } catch (error) {
    await removeObject(BUCKET, filePath).catch(() => {});

    throw error;
  }
}

export async function replaceMOMTemplate(
  workspaceId,
  file,
  sourceType = 'template'
) {
  const existing = await getMOMTemplate(workspaceId);

  // Deleting the template record also deletes its stored file on the backend.
  if (existing?.id) {
    await deleteMOMTemplate(workspaceId);
  }

  return await uploadMOMTemplate(
    workspaceId,
    file,
    sourceType
  );
}

export async function updateMOMTemplate(
  workspaceId,
  data
) {
  return await apiRequest(
    `/api/v1/mom-templates/workspace/${workspaceId}`,
    {
      method: 'PATCH',
      body: JSON.stringify(data),
    }
  );
}

export async function deleteMOMTemplate(workspaceId) {
  return await apiRequest(
    `/api/v1/mom-templates/workspace/${workspaceId}`,
    {
      method: 'DELETE',
    }
  );
}
