/**
 * Context API client
 */
import { api } from './index';
import { Context, ContextCreate, ContextUpdate } from '../types/context';

export async function listContexts(projectId: string): Promise<Context[]> {
  return api.get<Context[]>(`/projects/${projectId}/contexts`);
}

export async function getContext(projectId: string, contextId: string): Promise<Context> {
  return api.get<Context>(`/projects/${projectId}/contexts/${contextId}`);
}

export async function createContext(projectId: string, data: ContextCreate): Promise<Context> {
  return api.post<Context>(`/projects/${projectId}/contexts`, data);
}

export async function updateContext(
  projectId: string,
  contextId: string,
  data: ContextUpdate
): Promise<Context> {
  return api.put<Context>(`/projects/${projectId}/contexts/${contextId}`, data);
}

export async function deleteContext(projectId: string, contextId: string): Promise<void> {
  await api.delete(`/projects/${projectId}/contexts/${contextId}`);
}
