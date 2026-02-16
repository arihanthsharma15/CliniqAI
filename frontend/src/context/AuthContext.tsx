import { createContext, useContext, useEffect, useMemo, useState } from "react";

export type DemoRole = "staff" | "doctor";

export type AuthUser = {
  name: string;
  email: string;
  role: DemoRole;
};

type AuthContextValue = {
  user: AuthUser | null;
  signIn: (email: string, password: string) => { ok: boolean; error?: string };
  signOut: () => void;
};

const DEMO_ACCOUNTS: Array<AuthUser & { password: string }> = [
  { name: "Aarav Staff", email: "staff@cliniqai.demo", password: "staff123", role: "staff" },
  { name: "Dr. Meera", email: "doctor@cliniqai.demo", password: "doctor123", role: "doctor" },
];

const STORAGE_KEY = "cliniqai_auth_user";

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);

  useEffect(() => {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw) as AuthUser;
      if (parsed?.email) setUser(parsed);
    } catch {
      localStorage.removeItem(STORAGE_KEY);
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      signIn: (email: string, password: string) => {
        const found = DEMO_ACCOUNTS.find(
          (a) => a.email.toLowerCase() === email.trim().toLowerCase() && a.password === password,
        );
        if (!found) return { ok: false, error: "Invalid demo credentials." };
        const logged: AuthUser = { name: found.name, email: found.email, role: found.role };
        setUser(logged);
        localStorage.setItem(STORAGE_KEY, JSON.stringify(logged));
        return { ok: true };
      },
      signOut: () => {
        setUser(null);
        localStorage.removeItem(STORAGE_KEY);
      },
    }),
    [user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

export const demoAccounts = DEMO_ACCOUNTS.map(({ name, email, password, role }) => ({
  name,
  email,
  password,
  role,
}));
