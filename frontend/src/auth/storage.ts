/**
 * @author: liuqinhe
 */
import type { UserInfo } from "../types/api";

export function readStoredUser(): UserInfo | null {
  const raw = localStorage.getItem("user");
  if (!raw) return null;
  try {
    const u = JSON.parse(raw) as UserInfo;
    if (!u || typeof u.user_id !== "string") return null;
    return u;
  } catch {
    return null;
  }
}

export function writeAuth(token: string, user: UserInfo): void {
  localStorage.setItem("token", token);
  localStorage.setItem("user", JSON.stringify(user));
}

export function clearAuth(): void {
  localStorage.removeItem("token");
  localStorage.removeItem("user");
}
