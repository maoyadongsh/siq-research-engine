import { createContext, useContext } from 'react'

export interface User {
  id: number;
  username: string;
  email: string;
  full_name: string;
  role: string;
  approval_status?: string;
  is_active?: boolean;
}

export interface AuthContextType {
  user: User | null;
  token: string | null;
  login: (username: string, password: string) => Promise<void>;
  setSession: (accessToken: string, nextUser: User) => void;
  logout: () => void;
  hasPermission: (permission: string) => boolean;
}

export const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
