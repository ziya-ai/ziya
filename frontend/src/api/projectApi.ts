/**
 * Project API client
 */
import { api } from './index';
import { Project, ProjectCreate, ProjectUpdate, ProjectListItem } from '../types/project';

export async function listProjects(): Promise<ProjectListItem[]> {
  return api.get<ProjectListItem[]>('/projects');
}

export async function getCurrentProject(): Promise<Project> {
  return api.get<Project>('/projects/current');
}

export async function getProject(id: string): Promise<Project> {
  return api.get<Project>(`/projects/${id}`);
}

export async function createProject(data: ProjectCreate): Promise<Project> {
  return api.post<Project>('/projects', data);
}

export async function updateProject(id: string, data: ProjectUpdate): Promise<Project> {
  return api.put<Project>(`/projects/${id}`, data);
}

export async function deleteProject(id: string): Promise<void> {
  await api.delete(`/projects/${id}`);
}
