/**
 * Skill API client
 */
import { api } from './index';
import { Skill, SkillCreate, SkillUpdate } from '../types/skill';

export async function listSkills(projectId: string): Promise<Skill[]> {
  return api.get<Skill[]>(`/projects/${projectId}/skills`);
}

export async function getSkill(projectId: string, skillId: string): Promise<Skill> {
  return api.get<Skill>(`/projects/${projectId}/skills/${skillId}`);
}

export async function createSkill(projectId: string, data: SkillCreate): Promise<Skill> {
  return api.post<Skill>(`/projects/${projectId}/skills`, data);
}

export async function updateSkill(
  projectId: string,
  skillId: string,
  data: SkillUpdate
): Promise<Skill> {
  return api.put<Skill>(`/projects/${projectId}/skills/${skillId}`, data);
}

export async function deleteSkill(projectId: string, skillId: string): Promise<void> {
  await api.delete(`/projects/${projectId}/skills/${skillId}`);
}
