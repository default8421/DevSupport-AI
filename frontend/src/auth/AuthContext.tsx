/**
 * @author: liuqinhe
 */
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import api from "../api/client";
import type { UserInfo } from "../types/api";
import { clearAuth, readStoredUser, writeAuth } from "./storage";

interface AuthValue {
  user: UserInfo | null;
  login: (username: string, password: string) => Promise<UserInfo>;
  register: (username: string, password: string, displayName?: string) => Promise<UserInfo>;
  logout: () => void;
}

const Ctx = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserInfo | null>(() => readStoredUser());

  const applyAuth = (data: { access_token: string; user: UserInfo }) => {
    writeAuth(data.access_token, data.user);
    setUser(data.user);
    return data.user;
  };

  const login = useCallback(async (username: string, password: string) => {
    const { data } = await api.post("/auth/login", { username, password });
    return applyAuth(data);
  }, []);

  const register = useCallback(async (username: string, password: string, displayName?: string) => {
    const { data } = await api.post("/auth/register", {
      username,
      password,
      display_name: displayName || undefined,
    });
    return applyAuth(data);
  }, []);

  const logout = useCallback(() => {
    clearAuth();
    setUser(null);
  }, []);

  const value = useMemo(() => ({ user, login, register, logout }), [user, login, register, logout]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth 必须包在 AuthProvider 里");
  return v;
}
