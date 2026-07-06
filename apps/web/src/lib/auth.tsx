import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiJson, authCookieModeEnabled } from './apiClient';
import { resetAgentChatStores } from './useAgentChat';
import { AuthContext, useAuth, type User } from '../hooks/useAuth';

interface LoginResponse {
  access_token: string;
  user: User;
}

const rolePermissions: Record<string, string[]> = {
  super_admin: ['*'],
  admin: ['user.manage', 'report.*', 'company.*', 'audit.view', 'system.config'],
  analyst: ['report.create', 'report.edit', 'report.view', 'company.view'],
  reviewer: ['report.view', 'report.review', 'company.view'],
  viewer: ['report.view', 'company.view'],
};

function readStoredSession(): { token: string | null; user: User } | null {
  try {
    const savedToken = localStorage.getItem('access_token');
    const savedUser = localStorage.getItem('user');

    if (!savedUser || (!savedToken && !authCookieModeEnabled())) return null;

    return {
      token: savedToken || null,
      user: JSON.parse(savedUser) as User,
    };
  } catch {
    localStorage.removeItem('access_token');
    localStorage.removeItem('user');
    return null;
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(() => readStoredSession()?.user ?? null);
  const [token, setToken] = useState<string | null>(() => readStoredSession()?.token ?? null);

  useEffect(() => {
    const cookieMode = authCookieModeEnabled();
    const savedToken = localStorage.getItem('access_token');
    if (!savedToken && !cookieMode) return;
    let ignore = false;
    apiJson<User>('/api/auth/me')
      .then((freshUser) => {
        if (ignore) return;
        setUser(freshUser);
        localStorage.setItem('user', JSON.stringify(freshUser));
      })
      .catch(() => {
        if (ignore) return;
        resetAgentChatStores();
        setToken(null);
        setUser(null);
        localStorage.removeItem('access_token');
        localStorage.removeItem('user');
      });
    return () => { ignore = true; };
  }, []);

  const setSession = useCallback((accessToken: string, nextUser: User) => {
    const cookieMode = authCookieModeEnabled();
    resetAgentChatStores();
    setToken(cookieMode ? null : accessToken);
    setUser(nextUser);
    if (cookieMode) {
      localStorage.removeItem('access_token');
    } else {
      localStorage.setItem('access_token', accessToken);
    }
    localStorage.setItem('user', JSON.stringify(nextUser));
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const data = await apiJson<LoginResponse>('/api/auth/login', {
      method: 'POST',
      body: { username, password },
    });

    setSession(data.access_token, data.user);
  }, [setSession]);

  const logout = useCallback(() => {
    void apiJson('/api/auth/logout', { method: 'POST' }).catch(() => undefined);
    resetAgentChatStores();
    setToken(null);
    setUser(null);
    localStorage.removeItem('access_token');
    localStorage.removeItem('user');
  }, []);

  const hasPermission = useCallback((permission: string): boolean => {
    if (!user) return false;

    const permissions = rolePermissions[user.role] || [];
    return permissions.includes('*') || permissions.includes(permission) ||
           permissions.some(p => p.endsWith('.*') && permission.startsWith(p.slice(0, -2)));
  }, [user]);

  const authValue = useMemo(() => ({
    user,
    token,
    login,
    setSession,
    logout,
    hasPermission,
  }), [user, token, login, setSession, logout, hasPermission]);

  return (
    <AuthContext.Provider value={authValue}>
      {children}
    </AuthContext.Provider>
  );
}

export function ProtectedRoute({ children, permission }: { children: React.ReactNode; permission?: string }) {
  const { user, hasPermission } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (!user) {
      navigate('/login');
    } else if (permission && !hasPermission(permission)) {
      navigate('/forbidden');
    }
  }, [user, permission, hasPermission, navigate]);

  if (!user) return null;
  if (permission && !hasPermission(permission)) return null;

  return <>{children}</>;
}
