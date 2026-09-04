/**
 * @author: liuqinhe
 */
import axios from "axios";

import { clearAuth } from "../auth/storage";

const api = axios.create({ baseURL: `${import.meta.env.BASE_URL}api` });

api.interceptors.request.use((cfg) => {
  const token = localStorage.getItem("token");
  if (token) cfg.headers.Authorization = `Bearer ${token}`;
  return cfg;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401) {
      clearAuth();
      const login = `${import.meta.env.BASE_URL}login`.replace(/([^:]\/)\/+/g, "$1");
      if (!window.location.pathname.endsWith("/login")) {
        window.location.assign(login);
      }
    }
    return Promise.reject(err);
  },
);

export default api;
