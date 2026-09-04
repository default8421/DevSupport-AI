/**
 * @author: liuqinhe
 */
import { XProvider } from "@ant-design/x";
import { App, ConfigProvider, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import React from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider } from "react-router-dom";

import { router } from "./app/routes";
import { AuthProvider } from "./auth/AuthContext";

const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: "#0f766e",
          borderRadius: 10,
          borderRadiusLG: 14,
        },
        algorithm: prefersDark ? theme.darkAlgorithm : theme.defaultAlgorithm,
      }}
    >
      <XProvider>
        <App>
          <AuthProvider>
            <RouterProvider router={router} />
          </AuthProvider>
        </App>
      </XProvider>
    </ConfigProvider>
  </React.StrictMode>,
);
