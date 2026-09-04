/**
 * @author: liuqinhe
 */
import { Spin } from "antd";
import { Suspense, lazy, type ReactNode } from "react";
import { createBrowserRouter } from "react-router-dom";

import LoginPage from "../features/auth/LoginPage";
import AppShell from "./AppShell";
import { RequireAuth, RequireInternal } from "./guards";

const ChatPage = lazy(() => import("../features/chat/ChatPage"));
const KnowledgePage = lazy(() => import("../features/knowledge/KnowledgePage"));
const DocsPage = lazy(() => import("../features/docs/DocsPage"));
const ConversationsPage = lazy(() => import("../features/conversations/ConversationsPage"));
const TicketsPage = lazy(() => import("../features/tickets/TicketsPage"));
const WorkbenchPage = lazy(() => import("../features/workbench/WorkbenchPage"));
const MetricsPage = lazy(() => import("../features/metrics/MetricsPage"));

function Fallback() {
  return <Spin style={{ margin: "auto" }} />;
}

function Lazy({ children }: { children: ReactNode }) {
  return <Suspense fallback={<Fallback />}>{children}</Suspense>;
}

export const router = createBrowserRouter(
  [
    { path: "/login", element: <LoginPage /> },
    {
      element: <RequireAuth />,
      children: [
        {
          element: <AppShell />,
          children: [
            { path: "/", element: <Lazy><ChatPage /></Lazy> },
            { path: "/knowledge", element: <Lazy><KnowledgePage /></Lazy> },
            { path: "/docs", element: <Lazy><DocsPage /></Lazy> },
            { path: "/conversations", element: <Lazy><ConversationsPage /></Lazy> },
            { path: "/tickets", element: <Lazy><TicketsPage /></Lazy> },
            {
              element: <RequireInternal />,
              children: [
                { path: "/workbench", element: <Lazy><WorkbenchPage /></Lazy> },
                { path: "/metrics", element: <Lazy><MetricsPage /></Lazy> },
              ],
            },
          ],
        },
      ],
    },
  ],
  { basename: import.meta.env.BASE_URL },
);
