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

interface ProjectContextType {
  // Project state
  currentProject: Project | null;
  projects: ProjectListItem[];
  isLoadingProject: boolean;
  switchProject: (projectId: string) => Promise<void>;
  refreshProjects: () => Promise<void>;
  updateProject: (id: string, updates: ProjectUpdate) => Promise<void>;
  
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
  
  // Active lens state
  const [activeContextIds, setActiveContextIds] = useState<string[]>([]);
  const [activeSkillIds, setActiveSkillIds] = useState<string[]>([]);
  const [additionalFiles, setAdditionalFiles] = useState<string[]>([]);
  const [additionalPrompt, setAdditionalPrompt] = useState<string | null>(null);
  
  // Token calculation
  const [tokenInfo, setTokenInfo] = useState<TokenCalculationResponse | null>(null);
  const [isCalculatingTokens, setIsCalculatingTokens] = useState(false);
  
  // Initialize - load current project
  useEffect(() => {
    const init = async () => {
      try {
        setIsLoadingProject(true);
        
        // Log for debugging
        console.log('ProjectContext: Initializing...');
        
        const project = await projectApi.getCurrentProject();
        console.log('ProjectContext: Got current project:', project);
        setCurrentProject(project);
        
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
    
    return Array.from(files);
  }, [activeContextIds, additionalFiles, contexts]);
  
  // Combine active skill prompts
  const activeSkillPrompts = useMemo(() => {
    const prompts: string[] = [];
    
    for (const skillId of activeSkillIds) {
      const skill = skills.find(s => s.id === skillId);
      if (skill) {
        prompts.push(skill.prompt);
      }
    }
    
    if (additionalPrompt) {
      prompts.push(additionalPrompt);
    }
    
    return prompts.join('\n\n');
  }, [activeSkillIds, additionalPrompt, skills]);
  
  // Recalculate tokens when selection changes
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
  }, [currentProject?.id, activeFiles, activeContextIds, activeSkillIds, additionalFiles, additionalPrompt]);
  
  // Project actions
  const switchProject = useCallback(async (projectId: string) => {
    setIsLoadingProject(true);
    
    try {
      // 1. Load the project data FIRST
      const project = await projectApi.getProject(projectId);
      setCurrentProject(project);
      
      // 2. Update global path SYNCHRONOUSLY (before any events)
      (window as any).__ZIYA_CURRENT_PROJECT_PATH__ = project.path;
      
      // 3. Clear active lens
      setActiveContextIds([]);
      setActiveSkillIds([]);
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
  
  const updateProjectFn = useCallback(async (id: string, updates: ProjectUpdate) => {
    const updated = await projectApi.updateProject(id, updates);
    
    // Update in state
    if (currentProject?.id === id) {
      setCurrentProject(updated);
    }
    setProjects(prev => prev.map(p => p.id === id ? updated : p));
  }, [currentProject]);
  
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
  }, []);
  
  const removeSkillFromLens = useCallback((skillId: string) => {
    setActiveSkillIds(prev => prev.filter(id => id !== skillId));
  }, []);
  
  const clearLens = useCallback(() => {
    setActiveContextIds([]);
    setActiveSkillIds([]);
    setAdditionalFiles([]);
    setAdditionalPrompt(null);
  }, []);
  
  const value = useMemo(() => ({
    // Projects
    currentProject,
    projects,
    isLoadingProject,
    switchProject,
    refreshProjects,
    updateProject: updateProjectFn,
    
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
    tokenInfo,
    isCalculatingTokens,
  }), [
    currentProject,
    projects,
    isLoadingProject,
    switchProject,
    refreshProjects,
    updateProjectFn,
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
