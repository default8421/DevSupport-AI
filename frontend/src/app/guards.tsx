/**
 * @author: liuqinhe
 */
import { Navigate, Outlet } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { isInternal } from "../types/api";

export function RequireAuth() {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  return <Outlet />;
}

export function RequireInternal() {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  if (!isInternal(user.role)) return <Navigate to="/" replace />;
  return <Outlet />;
}
