/**
 * ProjectContext - Manages projects, contexts, and skills
 */
 import React, {
  createContext, 
  useContext, 
  useState, 
  useEffect, 
  useCallback,
  useMemo,
  ReactNode 
} from 'react';
import { Project, ProjectListItem, ProjectUpdate } from '../types/project';
import { Context, ContextCreate, ContextUpdate } from '../types/context';
import { Skill, SkillCreate, SkillUpdate } from '../types/skill';
import { TokenCalculationResponse } from '../types/token';
import * as projectApi from '../api/projectApi';
import * as contextApi from '../api/contextApi';
import * as skillApi from '../api/skillApi';
import * as tokenApi from '../api/tokenApi';
import { db } from '../utils/db';

interface ProjectContextType {
  // Project state
  currentProject: Project | null;
  projects: ProjectListItem[];
  isLoadingProject: boolean;
  switchProject: (projectId: string) => Promise<void>;
  refreshProjects: () => Promise<void>;
  updateProject: (id: string, updates: ProjectUpdate) => Promise<void>;
  deleteProject: (id: string) => Promise<void>;
  mergeProjects: (sourceId: string, targetId: string) => Promise<void>;
  createProject: (path: string, name?: string) => Promise<Project>;
  
  // Context state
  contexts: Context[];
  isLoadingContexts: boolean;
  createContext: (name: string, files: string[]) => Promise<Context>;
  updateContext: (id: string, updates: ContextUpdate) => Promise<void>;
  deleteContext: (id: string) => Promise<void>;
  
  // Skill state
  skills: Skill[];
  isLoadingSkills: boolean;
  createSkill: (data: SkillCreate) => Promise<Skill>;
  updateSkill: (id: string, updates: SkillUpdate) => Promise<void>;
  deleteSkill: (id: string) => Promise<void>;
  
  // Active lens (current selection)
  activeContextIds: string[];
  activeSkillIds: string[];
  additionalFiles: string[];
  additionalPrompt: string | null;
  setActiveContextIds: (ids: string[]) => void;
  setActiveSkillIds: (ids: string[]) => void;
  addContextToLens: (contextId: string) => void;
  removeContextFromLens: (contextId: string) => void;
  addSkillToLens: (skillId: string) => void;
  removeSkillFromLens: (skillId: string) => void;
  setAdditionalFiles: (files: string[]) => void;
  setAdditionalPrompt: (prompt: string | null) => void;
  clearLens: () => void;
  
  // Computed values
  activeFiles: string[];
  activeSkillPrompts: string;
  activeModelOverrides: Record<string, any>;
  activeToolIds: string[];
  tokenInfo: TokenCalculationResponse | null;
  isCalculatingTokens: boolean;
}

const ProjectContext = createContext<ProjectContextType | undefined>(undefined);

export function ProjectProvider({ children }: { children: ReactNode }) {
  // Project state
  const [currentProject, setCurrentProject] = useState<Project | null>(null);
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [isLoadingProject, setIsLoadingProject] = useState(true);
  
  // Context state
  const [contexts, setContexts] = useState<Context[]>([]);
  const [isLoadingContexts, setIsLoadingContexts] = useState(false);
  
  // Skill state
  const [skills, setSkills] = useState<Skill[]>([]);
  const [isLoadingSkills, setIsLoadingSkills] = useState(false);
  
  // ── Lens persistence helpers ──────────────────────────────────────
  // Store active context/skill IDs per project in localStorage so they
  // survive page reloads AND project switches.
  const _lensKey = (projectId: string) => `ZIYA_LENS_${projectId}`;

  const _saveLens = useCallback((projectId: string, ctxIds: string[], skillIds: string[]) => {
    try {
      localStorage.setItem(_lensKey(projectId), JSON.stringify({ contextIds: ctxIds, skillIds }));
    } catch { /* quota exceeded — non-fatal */ }
  }, []);

  const _loadLens = useCallback((projectId: string): { contextIds: string[]; skillIds: string[] } => {
    try {
      const raw = localStorage.getItem(_lensKey(projectId));
      if (raw) {
        const parsed = JSON.parse(raw);
        return {
          contextIds: Array.isArray(parsed.contextIds) ? parsed.contextIds : [],
          skillIds: Array.isArray(parsed.skillIds) ? parsed.skillIds : [],
        };
      }
    } catch { /* corrupt data — start fresh */ }
    return { contextIds: [], skillIds: [] };
  }, []);

  // Active lens state
  const [activeContextIds, _setActiveContextIds] = useState<string[]>([]);
  const [activeSkillIds, _setActiveSkillIds] = useState<string[]>([]);

  // Wrapped setters that auto-persist to localStorage
  const setActiveContextIds = useCallback((ids: string[] | ((prev: string[]) => string[])) => {
    _setActiveContextIds(prev => {
      const next = typeof ids === 'function' ? ids(prev) : ids;
      // Persist (needs current project ID — read from ref to avoid stale closure)
      const pid = (window as any).__ZIYA_CURRENT_PROJECT_ID__;
      if (pid) _saveLens(pid, next, _activeSkillIdsRef.current);
      return next;
    });
  }, [_saveLens]);

  const setActiveSkillIds = useCallback((ids: string[] | ((prev: string[]) => string[])) => {
    _setActiveSkillIds(prev => {
      const next = typeof ids === 'function' ? ids(prev) : ids;
      const pid = (window as any).__ZIYA_CURRENT_PROJECT_ID__;
      if (pid) _saveLens(pid, _activeContextIdsRef.current, next);
      return next;
    });
  }, [_saveLens]);

  // Refs to let the setters read each other's current value without
  // creating circular dependencies.
  const _activeContextIdsRef = React.useRef(activeContextIds);
  const _activeSkillIdsRef = React.useRef(activeSkillIds);
  React.useEffect(() => { _activeContextIdsRef.current = activeContextIds; }, [activeContextIds]);
  React.useEffect(() => { _activeSkillIdsRef.current = activeSkillIds; }, [activeSkillIds]);

  const [additionalFiles, setAdditionalFiles] = useState<string[]>([]);
  const [additionalPrompt, setAdditionalPrompt] = useState<string | null>(null);
  
  // Token calculation
  const [tokenInfo, setTokenInfo] = useState<TokenCalculationResponse | null>(null);
  const [isCalculatingTokens, setIsCalculatingTokens] = useState(false);
  const [astRevision, setAstRevision] = useState(0);
  
  // Initialize - load current project
  useEffect(() => {
    const init = async () => {
      // Migrate from old sessionStorage key (one-time)
      try {
        const legacySkills = sessionStorage.getItem('ZIYA_ACTIVE_SKILL_IDS');
        if (legacySkills) {
          // Will be merged into the project-scoped key once we know the project ID
          (window as any).__ZIYA_LEGACY_SKILL_IDS__ = JSON.parse(legacySkills);
          sessionStorage.removeItem('ZIYA_ACTIVE_SKILL_IDS');
          console.log('ProjectContext: Migrated legacy skill IDs from sessionStorage');
        }
      } catch {}

      try {
        setIsLoadingProject(true);
        
        // Log for debugging
        console.log('ProjectContext: Initializing...');
        
        const project = await projectApi.getCurrentProject();
        console.log('ProjectContext: Got current project:', project);
        setCurrentProject(project);
        // Expose project ID globally for lens persistence
        (window as any).__ZIYA_CURRENT_PROJECT_ID__ = project.id;
        
        // Restore lens state for this project
        const savedLens = _loadLens(project.id);
        // Merge any legacy skill IDs from the old sessionStorage migration
        const legacySkills = (window as any).__ZIYA_LEGACY_SKILL_IDS__;
        if (legacySkills && Array.isArray(legacySkills)) {
          const merged = new Set([...savedLens.skillIds, ...legacySkills]);
          savedLens.skillIds = Array.from(merged);
          delete (window as any).__ZIYA_LEGACY_SKILL_IDS__;
          console.log('ProjectContext: Merged legacy skill IDs:', legacySkills);
        }
        if (savedLens.contextIds.length > 0 || savedLens.skillIds.length > 0) {
          console.log('ProjectContext: Restoring lens state:', savedLens);
          _setActiveContextIds(savedLens.contextIds);
          _setActiveSkillIds(savedLens.skillIds);
        }
        
        const allProjects = await projectApi.listProjects();
        console.log('ProjectContext: Got all projects:', allProjects.length);
        setProjects(allProjects);
      } catch (error) {
        console.error('Failed to initialize project:', error);
        // Continue even if API fails - show error state instead of blocking
      } finally {
        setIsLoadingProject(false);
      }
    };
    init();
  }, []);
  
  // Expose current project path globally for FolderContext (avoids circular hook dependency)
  useEffect(() => {
    (window as any).__ZIYA_CURRENT_PROJECT_PATH__ = currentProject?.path || null;
    (window as any).__ZIYA_CURRENT_PROJECT_ID__ = currentProject?.id || null;
  }, [currentProject?.path]);
  
  // Load contexts when project changes
  useEffect(() => {
    if (!currentProject) {
      setContexts([]);
      return;
    }
    
    const loadContexts = async () => {
      setIsLoadingContexts(true);
      try {
        const ctx = await contextApi.listContexts(currentProject.id);
        setContexts(ctx);
      } catch (error) {
        console.error('Failed to load contexts:', error);
      } finally {
        setIsLoadingContexts(false);
      }
    };
    loadContexts();
  }, [currentProject?.id]);
  
  // Load skills when project changes
  useEffect(() => {
    if (!currentProject) {
      setSkills([]);
      return;
    }
    
    const loadSkills = async () => {
      setIsLoadingSkills(true);
      try {
        const skls = await skillApi.listSkills(currentProject.id);
        setSkills(skls);
      } catch (error) {
        console.error('Failed to load skills:', error);
      } finally {
        setIsLoadingSkills(false);
      }
    };
    loadSkills();
  }, [currentProject?.id]);
  
  // Calculate active files from contexts + additional
  const activeFiles = useMemo(() => {
    const files = new Set<string>();
    
    // Add files from active contexts
    for (const ctxId of activeContextIds) {
      const ctx = contexts.find(c => c.id === ctxId);
      if (ctx) {
        ctx.files.forEach(f => files.add(f));
      }
    }
    
    // Add additional files
    additionalFiles.forEach(f => files.add(f));
    
    // Add files from active skills
    for (const skillId of activeSkillIds) {
      const skill = skills.find(s => s.id === skillId);
      if (skill?.files) {
        skill.files.forEach(f => files.add(f));
      }
    }

    return Array.from(files);
  }, [activeContextIds, activeSkillIds, additionalFiles, contexts, skills]);
  
  // Combine active skill prompts
  const activeSkillPrompts = useMemo(() => {
    const prompts: string[] = [];
    
    for (const skillId of activeSkillIds) {
      const skill = skills.find(s => s.id === skillId);
      if (skill) {
        prompts.push(`[Active Skill: ${skill.name}]\n${skill.prompt}`);
      }
    }
    
    if (additionalPrompt) {
      prompts.push(additionalPrompt);
    }
    
    return prompts.join('\n\n');
  }, [activeSkillIds, additionalPrompt, skills]);
  
  // Recalculate tokens when selection changes
  // Merge modelOverrides from all active skills (last-write-wins per key)
  const activeModelOverrides = useMemo(() => {
    const merged: Record<string, any> = {};
    for (const skillId of activeSkillIds) {
      const skill = skills.find(s => s.id === skillId);
      if (skill?.modelOverrides) {
        if (skill.modelOverrides.temperature !== undefined)
          merged.temperature = skill.modelOverrides.temperature;
        if (skill.modelOverrides.maxOutputTokens !== undefined)
          merged.maxOutputTokens = skill.modelOverrides.maxOutputTokens;
        if (skill.modelOverrides.thinkingMode !== undefined)
          merged.thinkingMode = skill.modelOverrides.thinkingMode;
      }
    }
    return merged;
  }, [activeSkillIds, skills]);

  // Collect toolIds from active skills for tool filtering
  const activeToolIds = useMemo(() => {
    const ids = new Set<string>();
    for (const skillId of activeSkillIds) {
      const skill = skills.find(s => s.id === skillId);
      if (skill?.toolIds) {
        skill.toolIds.forEach(t => ids.add(t));
      }
      if (skill?.allowedTools) {
        skill.allowedTools.forEach(t => ids.add(t));
      }
    }
    return Array.from(ids);
  }, [activeSkillIds, skills]);

  // Bump astRevision when background AST indexing completes.
  // This is fired by FolderContext when the ws/file-tree WebSocket
  // delivers an ast_indexing_complete event from the backend.
  useEffect(() => {
    const handler = () => setAstRevision(r => r + 1);
    window.addEventListener('astIndexingComplete', handler);
    return () => window.removeEventListener('astIndexingComplete', handler);
  }, []);

  useEffect(() => {
    if (!currentProject) {
      setTokenInfo(null);
      return;
    }
    
    if (activeFiles.length === 0 && activeSkillIds.length === 0 && !additionalPrompt) {
      setTokenInfo(null);
      return;
    }
    
    const calculateTokens = async () => {
      setIsCalculatingTokens(true);
      try {
        const info = await tokenApi.calculateTokens(
          currentProject.id,
          additionalFiles,
          activeContextIds,
          activeSkillIds,
          additionalPrompt || undefined
        );
        setTokenInfo(info);
      } catch (error) {
        console.error('Failed to calculate tokens:', error);
      } finally {
        setIsCalculatingTokens(false);
      }
    };
    
    // Debounce calculation
    const timeout = setTimeout(calculateTokens, 300);
    return () => clearTimeout(timeout);
  }, [currentProject?.id, activeFiles, activeContextIds, activeSkillIds, additionalFiles, additionalPrompt, astRevision]);
  
  // Project actions
  const switchProject = useCallback(async (projectId: string) => {
    setIsLoadingProject(true);
    
    try {
      // Save current lens state for the project we're leaving
      const leavingId = (window as any).__ZIYA_CURRENT_PROJECT_ID__;
      if (leavingId) {
        _saveLens(leavingId, _activeContextIdsRef.current, _activeSkillIdsRef.current);
      }

      // 1. Load the project data FIRST
      const project = await projectApi.getProject(projectId);
      setCurrentProject(project);
      
      // 2. Update global path SYNCHRONOUSLY (before any events)
      (window as any).__ZIYA_CURRENT_PROJECT_PATH__ = project.path;
      (window as any).__ZIYA_CURRENT_PROJECT_ID__ = project.id;
      
      // 3. Restore lens state for the project we're switching TO
      const savedLens = _loadLens(project.id);
      console.log(`ProjectContext: Restoring lens for project ${project.name}:`, savedLens);
      _setActiveContextIds(savedLens.contextIds);
      _setActiveSkillIds(savedLens.skillIds);
      setAdditionalFiles([]);
      setAdditionalPrompt(null);
      
      // 4. Dispatch single event with all project data
      window.dispatchEvent(new CustomEvent('projectSwitched', {
        detail: { 
          projectId: project.id,
          projectPath: project.path,
          projectName: project.name
        }
      }));
      
      console.log(`✅ Switched to project: ${project.name} at ${project.path}`);
    } catch (error) {
      console.error('Failed to switch project:', error);
      throw error;
    } finally {
      setIsLoadingProject(false);
    }
  }, []);
  
  const refreshProjects = useCallback(async () => {
    const allProjects = await projectApi.listProjects();
    setProjects(allProjects);
  }, []);
  
  const createProjectFn = useCallback(async (path: string, name?: string) => {
    const newProject = await projectApi.createProject({ path, name });
    
    // Add to projects list
    setProjects(prev => [...prev, newProject]);
    
    // Auto-switch to new project
    await switchProject(newProject.id);
    
    console.log(`✅ Created and switched to project: ${newProject.name} at ${newProject.path}`);
    
    return newProject;
  }, [switchProject]);
  
  const updateProjectFn = useCallback(async (id: string, updates: ProjectUpdate) => {
    const updated = await projectApi.updateProject(id, updates);
    
    // Update in state
    if (currentProject?.id === id) {
      setCurrentProject(updated);
    }
    setProjects(prev => prev.map(p => p.id === id ? updated : p));
  }, [currentProject]);
  
  const deleteProjectFn = useCallback(async (id: string) => {
    if (currentProject?.id === id) {
      throw new Error('Cannot delete the currently active project');
    }

    await projectApi.deleteProject(id);
    setProjects(prev => prev.filter(p => p.id !== id));
  }, [currentProject]);

  const mergeProjectsFn = useCallback(async (sourceId: string, targetId: string) => {
    if (sourceId === targetId) {
      throw new Error('Cannot merge a project into itself');
    }

    // Reassign conversations and folders directly in IndexedDB
    const allConversations = await db.getConversations();
    const updated = allConversations.map(c =>
      c.projectId === sourceId
        ? { ...c, projectId: targetId, _version: Date.now() }
        : c
    );
    await db.saveConversations(updated);

    const allFolders = await db.getFolders();
    const foldersToUpdate = allFolders.filter(f => f.projectId === sourceId);
    for (const folder of foldersToUpdate) {
      await db.saveFolder({ ...folder, projectId: targetId });
    }

    console.log(
      `📦 MERGE: Reassigned ${updated.filter(c => c.projectId === targetId && allConversations.find(o => o.id === c.id)?.projectId === sourceId).length} conversations and ${foldersToUpdate.length} folders from ${sourceId} to ${targetId}`
    );

    // Delete the source project from backend storage
    await projectApi.deleteProject(sourceId);
    setProjects(prev => prev.filter(p => p.id !== sourceId));

    // If the deleted project was the current project, switch to target
    if (currentProject?.id === sourceId) {
      await switchProject(targetId);
    }

    // Notify ChatContext to reload from IndexedDB so it picks up the reassigned items
    window.dispatchEvent(new CustomEvent('projectSwitched', {
      detail: { projectId: targetId, projectPath: '', projectName: '' }
    }));
  }, [currentProject, switchProject]);

  // Context actions
  const createContextFn = useCallback(async (name: string, files: string[]) => {
    if (!currentProject) throw new Error('No project selected');
    
    const ctx = await contextApi.createContext(currentProject.id, { name, files });
    setContexts(prev => [...prev, ctx]);
    
    // Auto-activate the new context
    setActiveContextIds(prev => [...prev, ctx.id]);
    
    // Remove these files from additionalFiles since they're now in a context
    setAdditionalFiles(prev => prev.filter(f => !files.includes(f)));
    
    return ctx;
  }, [currentProject]);
  
  const updateContextFn = useCallback(async (id: string, updates: ContextUpdate) => {
    if (!currentProject) return;
    
    const updated = await contextApi.updateContext(currentProject.id, id, updates);
    setContexts(prev => prev.map(c => c.id === id ? updated : c));
  }, [currentProject]);
  
  const deleteContextFn = useCallback(async (id: string) => {
    if (!currentProject) return;
    
    await contextApi.deleteContext(currentProject.id, id);
    setContexts(prev => prev.filter(c => c.id !== id));
    setActiveContextIds(prev => prev.filter(cid => cid !== id));
  }, [currentProject]);
  
  // Skill actions
  const createSkillFn = useCallback(async (data: SkillCreate) => {
    if (!currentProject) throw new Error('No project selected');
    
    const skill = await skillApi.createSkill(currentProject.id, data);
    setSkills(prev => [...prev, skill]);
    
    return skill;
  }, [currentProject]);
  
  const updateSkillFn = useCallback(async (id: string, updates: SkillUpdate) => {
    if (!currentProject) return;
    
    const updated = await skillApi.updateSkill(currentProject.id, id, updates);
    setSkills(prev => prev.map(s => s.id === id ? updated : s));
  }, [currentProject]);
  
  const deleteSkillFn = useCallback(async (id: string) => {
    if (!currentProject) return;
    
    await skillApi.deleteSkill(currentProject.id, id);
    setSkills(prev => prev.filter(s => s.id !== id));
    setActiveSkillIds(prev => prev.filter(sid => sid !== id));
  }, [currentProject]);
  
  const addContextToLens = useCallback((contextId: string) => {
    const ctx = contexts.find(c => c.id === contextId);
    if (!ctx) return;
    
    setActiveContextIds(prev => 
      prev.includes(contextId) ? prev : [...prev, contextId]
    );
    
    // Dispatch event to add files to tree selection
    window.dispatchEvent(new CustomEvent('addFilesToSelection', {
      detail: { files: ctx.files }
    }));
  }, [contexts]);
  
  const removeContextFromLens = useCallback((contextId: string) => {
    const ctx = contexts.find(c => c.id === contextId);
    if (!ctx) return;
    
    setActiveContextIds(prev => prev.filter(id => id !== contextId));
    
    // Dispatch event to remove files from tree selection
    window.dispatchEvent(new CustomEvent('removeFilesFromSelection', {
      detail: { files: ctx.files }
    }));
  }, [contexts]);
  
  const addSkillToLens = useCallback((skillId: string) => {
    setActiveSkillIds(prev => 
      prev.includes(skillId) ? prev : [...prev, skillId]
    );

    // Auto-activate contexts referenced by this skill
    const skill = skills.find(s => s.id === skillId);
    if (skill?.contextIds && skill.contextIds.length > 0) {
      setActiveContextIds(prev => {
        const merged = new Set([...prev, ...skill.contextIds!]);
        return Array.from(merged);
      });
    }
  }, [skills]);  
  const removeSkillFromLens = useCallback((skillId: string) => {
    setActiveSkillIds(prev => prev.filter(id => id !== skillId));
  }, []);
  
  const clearLens = useCallback(() => {
    setActiveContextIds([]);
    setActiveSkillIds([]);
    setAdditionalFiles([]);
    setAdditionalPrompt(null);
    // Also clear persisted state
    const pid = (window as any).__ZIYA_CURRENT_PROJECT_ID__;
    if (pid) {
      try { localStorage.removeItem(_lensKey(pid)); } catch {}
    }
  }, []);
  
  const value = useMemo(() => ({
    // Projects
    currentProject,
    projects,
    isLoadingProject,
    switchProject,
    refreshProjects,
    updateProject: updateProjectFn,
    createProject: createProjectFn,
    deleteProject: deleteProjectFn,
    mergeProjects: mergeProjectsFn,
    
    // Contexts
    contexts,
    isLoadingContexts,
    createContext: createContextFn,
    updateContext: updateContextFn,
    deleteContext: deleteContextFn,
    
    // Skills
    skills,
    isLoadingSkills,
    createSkill: createSkillFn,
    updateSkill: updateSkillFn,
    deleteSkill: deleteSkillFn,
    
    // Active lens
    activeContextIds,
    activeSkillIds,
    additionalFiles,
    additionalPrompt,
    setActiveContextIds,
    setActiveSkillIds,
    addContextToLens,
    removeContextFromLens,
    addSkillToLens,
    removeSkillFromLens,
    setAdditionalFiles,
    setAdditionalPrompt,
    clearLens,
    
    // Computed
    activeFiles,
    activeSkillPrompts,
    activeModelOverrides,
    activeToolIds,
    tokenInfo,
    isCalculatingTokens,
  }), [
    currentProject,
    projects,
    isLoadingProject,
    switchProject,
    refreshProjects,
    updateProjectFn,
    createProjectFn,
    deleteProjectFn,
    mergeProjectsFn,
    contexts,
    isLoadingContexts,
    createContextFn,
    updateContextFn,
    deleteContextFn,
    skills,
    isLoadingSkills,
    createSkillFn,
    updateSkillFn,
    deleteSkillFn,
    activeContextIds,
    activeSkillIds,
    additionalFiles,
    additionalPrompt,
    addContextToLens,
    removeContextFromLens,
    addSkillToLens,
    removeSkillFromLens,
    clearLens,
    activeFiles,
    activeSkillPrompts,
    activeModelOverrides,
    activeToolIds,
    tokenInfo,
    isCalculatingTokens,
  ]);
  
  return (
    <ProjectContext.Provider value={value}>
      {children}
    </ProjectContext.Provider>
  );
}

export function useProject() {
  const context = useContext(ProjectContext);
  if (!context) {
    throw new Error('useProject must be used within ProjectProvider');
  }
  return context;
}
