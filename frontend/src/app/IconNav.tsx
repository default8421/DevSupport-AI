/**
 * @author: liuqinhe
 */
import {
  CommentOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  HistoryOutlined,
  LogoutOutlined,
  ProfileOutlined,
  ReadOutlined,
  ToolOutlined,
} from "@ant-design/icons";
import { Tooltip } from "antd";
import { useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { isInternal } from "../types/api";
import styles from "./shell.module.css";

const CUSTOMER = [
  { to: "/", icon: <CommentOutlined />, label: "对话" },
  { to: "/knowledge", icon: <DatabaseOutlined />, label: "我的知识库" },
  { to: "/docs", icon: <ReadOutlined />, label: "平台文档" },
  { to: "/conversations", icon: <HistoryOutlined />, label: "历史会话" },
  { to: "/tickets", icon: <ProfileOutlined />, label: "我的工单" },
];

const INTERNAL = [
  { to: "/workbench", icon: <ToolOutlined />, label: "工作台" },
  { to: "/metrics", icon: <DashboardOutlined />, label: "运营指标" },
];

export default function IconNav() {
  const { user, logout } = useAuth();
  const loc = useLocation();
  const nav = useNavigate();
  const items = user && isInternal(user.role) ? [...CUSTOMER, ...INTERNAL] : CUSTOMER;

  return (
    <nav className={styles.nav}>
      <div className={styles.brand}>DS</div>
      <div className={styles.navItems}>
        {items.map((it) => {
          const active = it.to === "/" ? loc.pathname === "/" : loc.pathname.startsWith(it.to);
          return (
            <Tooltip key={it.to} title={it.label} placement="right">
              <button
                type="button"
                className={`${styles.navBtn} ${active ? styles.navBtnActive : ""}`}
                onClick={() => nav(it.to)}
                aria-label={it.label}
              >
                {it.icon}
              </button>
            </Tooltip>
          );
        })}
      </div>
      <div className={styles.user}>
        <Tooltip title={`${user?.display_name ?? ""} · 退出`} placement="right">
          <button
            type="button"
            className={styles.navBtn}
            onClick={() => {
              logout();
              nav("/login");
            }}
            aria-label="退出"
          >
            <LogoutOutlined />
          </button>
        </Tooltip>
      </div>
    </nav>
  );
}
