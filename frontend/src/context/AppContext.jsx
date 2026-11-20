import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import { fetchApi } from '../api/client';

const AppContext = createContext();

export function AppProvider({ children }) {
  const [workspaces, setWorkspaces] = useState([]);
  const [activeWorkspaceId, setActiveWorkspaceIdState] = useState(
    () => window.localStorage.getItem('pucho_active_workspace') || null,
  );
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const workspaceRequest = useRef(0);
  // Latest committed list, so callbacks never pick a fallback from a stale render.
  const workspacesRef = useRef(workspaces);
  useEffect(() => {
    workspacesRef.current = workspaces;
  }, [workspaces]);

  const setActiveWorkspaceId = useCallback((workspaceIdOrUpdater) => {
    setActiveWorkspaceIdState((currentId) => {
      const nextId =
        typeof workspaceIdOrUpdater === 'function'
          ? workspaceIdOrUpdater(currentId)
          : workspaceIdOrUpdater;
      if (nextId) window.localStorage.setItem('pucho_active_workspace', nextId);
      else window.localStorage.removeItem('pucho_active_workspace');
      return nextId;
    });
  }, []);

  const refreshWorkspaces = useCallback(async () => {
    const request = ++workspaceRequest.current;
    const wsData = (await fetchApi('/workspaces/')) || [];
    if (request !== workspaceRequest.current) return wsData;
    setWorkspaces(wsData);
    setActiveWorkspaceId((currentId) =>
      wsData.some((workspace) => workspace.id === currentId) ? currentId : wsData[0]?.id || null,
    );
    return wsData;
  }, [setActiveWorkspaceId]);

  // A locally created source wins over any list request that was already in flight.
  const addWorkspace = useCallback((workspace) => {
    workspaceRequest.current += 1;
    setWorkspaces((current) => [...current.filter((item) => item.id !== workspace.id), workspace]);
  }, []);

  const removeWorkspace = useCallback(
    (workspaceId) => {
      workspaceRequest.current += 1;
      const remaining = workspacesRef.current.filter((workspace) => workspace.id !== workspaceId);
      workspacesRef.current = remaining;
      setWorkspaces((current) => current.filter((workspace) => workspace.id !== workspaceId));
      setActiveWorkspaceId((currentId) =>
        currentId === workspaceId ? remaining[0]?.id || null : currentId,
      );
    },
    [setActiveWorkspaceId],
  );

  // Load initial data
  useEffect(() => {
    const clearUnauthorizedWorkspace = () => {
      workspaceRequest.current += 1;
      setWorkspaces([]);
      setActiveWorkspaceId(null);
    };
    window.addEventListener('puchoo:unauthorized', clearUnauthorizedWorkspace);
    async function loadInitialData() {
      try {
        const [, profileData] = await Promise.all([
          refreshWorkspaces(),
          fetchApi('/settings/profile'),
        ]);
        setProfile(profileData);
      } catch (err) {
        console.error('Failed to load initial data', err);
      } finally {
        setLoading(false);
      }
    }

    loadInitialData();
    let refreshing = false;
    const refreshSharedSources = async () => {
      if (document.hidden || refreshing) return;
      refreshing = true;
      try {
        await refreshWorkspaces();
      } catch (err) {
        console.error('Failed to refresh shared data sources', err);
      } finally {
        refreshing = false;
      }
    };
    window.addEventListener('focus', refreshSharedSources);
    document.addEventListener('visibilitychange', refreshSharedSources);
    const timer = window.setInterval(refreshSharedSources, 30000);
    return () => {
      workspaceRequest.current += 1;
      window.clearInterval(timer);
      window.removeEventListener('focus', refreshSharedSources);
      document.removeEventListener('visibilitychange', refreshSharedSources);
      window.removeEventListener('puchoo:unauthorized', clearUnauthorizedWorkspace);
    };
  }, [refreshWorkspaces, setActiveWorkspaceId]);

  const getActiveWorkspace = () => {
    return workspaces.find((w) => w.id === activeWorkspaceId) || null;
  };

  const value = {
    workspaces,
    setWorkspaces,
    addWorkspace,
    removeWorkspace,
    activeWorkspaceId,
    setActiveWorkspaceId,
    activeWorkspace: getActiveWorkspace(),
    refreshWorkspaces,
    profile,
    setProfile,
    loading,
  };

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useAppContext() {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error('useAppContext must be used within an AppProvider');
  }
  return context;
}
