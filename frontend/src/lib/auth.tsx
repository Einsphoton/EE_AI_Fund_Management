import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { api, AuthApi, AuthUser } from "../api/client";

type AuthContextValue = {
  user: AuthUser | null;
  token: string;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string, email?: string) => Promise<void>;
  logout: () => void;
};

const TOKEN_KEY = "ee_auth_token";
const USER_KEY = "ee_auth_user";
const AuthContext = createContext<AuthContextValue | null>(null);

function setCookieToken(token: string) {
  if (token) {
    document.cookie = `ee_auth_token=${encodeURIComponent(token)}; path=/; max-age=${60 * 60 * 24 * 30}; samesite=lax`;
  } else {
    document.cookie = "ee_auth_token=; path=/; max-age=0; samesite=lax";
  }
}

function persistAuth(token: string, user: AuthUser | null) {
  if (token && user) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
    setCookieToken(token);
    api.defaults.headers.common.Authorization = `Bearer ${token}`;
  } else {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setCookieToken("");
    delete api.defaults.headers.common.Authorization;
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || "");
  const [user, setUser] = useState<AuthUser | null>(() => {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || "null");
    } catch {
      return null;
    }
  });
  const [loading, setLoading] = useState(Boolean(token));

  useEffect(() => {
    if (!token) {
      setLoading(false);
      return;
    }
    api.defaults.headers.common.Authorization = `Bearer ${token}`;
    AuthApi.me()
      .then((me) => {
        setUser(me);
        persistAuth(token, me);
      })
      .catch(() => {
        setToken("");
        setUser(null);
        persistAuth("", null);
      })
      .finally(() => setLoading(false));
  }, [token]);

  const value = useMemo<AuthContextValue>(() => ({
    user,
    token,
    loading,
    login: async (username, password) => {
      const res = await AuthApi.login(username, password);
      setToken(res.token);
      setUser(res.user);
      persistAuth(res.token, res.user);
    },
    register: async (username, password, email) => {
      const res = await AuthApi.register(username, password, email);
      setToken(res.token);
      setUser(res.user);
      persistAuth(res.token, res.user);
    },
    logout: () => {
      setToken("");
      setUser(null);
      persistAuth("", null);
    },
  }), [loading, token, user]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
