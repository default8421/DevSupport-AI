/**
 * @author: liuqinhe
 */
import { App, Button, Form, Input, Tabs, Tag, Typography } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "../../auth/AuthContext";
import { isInternal } from "../../types/api";

const DEMO = [
  { username: "dev_acme", label: "Acme 开发者", hint: "客户侧 · 本租户对话 / 知识库 / 工单" },
  { username: "admin_acme", label: "Acme 管理员", hint: "客户侧 · 与开发者同权，同属 Acme 租户" },
  { username: "support1", label: "技术支持", hint: "内部 · 跨租户工作台与人工回复" },
  { username: "admin", label: "系统管理员", hint: "内部 · 工作台 + 运营指标" },
];

type Mode = "login" | "register";

function errorText(e: unknown): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
  return "";
}

export default function LoginPage() {
  const { message } = App.useApp();
  const { login, register } = useAuth();
  const nav = useNavigate();
  const [mode, setMode] = useState<Mode>("login");
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm<{ username: string; password: string; display_name?: string }>();

  const enter = (user: { display_name: string; role: string }) => {
    message.success(`欢迎，${user.display_name}`);
    nav(isInternal(user.role) ? "/workbench" : "/", { replace: true });
  };

  const onFinish = async (v: { username: string; password: string; display_name?: string }) => {
    setLoading(true);
    try {
      if (mode === "register") {
        enter(await register(v.username, v.password, v.display_name));
      } else {
        enter(await login(v.username, v.password));
      }
    } catch (e) {
      message.error(errorText(e) || (mode === "register" ? "注册失败" : "用户名或密码错误"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <div
        style={{
          flex: 1,
          minWidth: 0,
          background: "linear-gradient(160deg, #0b3d3a 0%, #0f766e 60%, #14b8a6 100%)",
          color: "#ecfdf5",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "0 56px",
        }}
      >
        <Typography.Title style={{ color: "#fff", marginBottom: 12 }}>DevSupport AI</Typography.Title>
        <div style={{ color: "#ccfbf1", fontSize: 16, lineHeight: 1.6, whiteSpace: "nowrap" }}>
          面向 API 开放平台的多 Agent 智能技术支持。先给结论，再给证据。
        </div>
      </div>
      <div style={{ width: 480, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", padding: 40 }}>
        <div style={{ width: 380 }}>
          <Tabs
            activeKey={mode}
            onChange={(k) => {
              setMode(k as Mode);
              form.resetFields();
            }}
            items={[
              { key: "login", label: "登录" },
              { key: "register", label: "注册" },
            ]}
          />
          <Form form={form} onFinish={onFinish} layout="vertical">
            <Form.Item
              name="username"
              label="用户名"
              rules={[
                { required: true, message: "请输入用户名" },
                ...(mode === "register"
                  ? [{ pattern: /^[a-zA-Z][a-zA-Z0-9_]{2,31}$/, message: "3-32 位，字母开头，仅含字母数字下划线" }]
                  : []),
              ]}
            >
              <Input placeholder={mode === "register" ? "字母开头，3-32 位" : "请输入用户名"} />
            </Form.Item>
            {mode === "register" && (
              <Form.Item name="display_name" label="显示名">
                <Input placeholder="可选，默认与用户名相同" />
              </Form.Item>
            )}
            <Form.Item
              name="password"
              label="密码"
              rules={[
                { required: true, message: "请输入密码" },
                ...(mode === "register" ? [{ min: 8, message: "至少 8 位" }] : []),
              ]}
            >
              <Input.Password placeholder={mode === "register" ? "至少 8 位" : "请输入密码"} />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={loading} block>
              {mode === "register" ? "注册并进入" : "登录"}
            </Button>
          </Form>
          {mode === "login" && (
            <>
              <div style={{ marginTop: 20, fontSize: 12, color: "var(--ant-color-text-secondary, #8c8c8c)" }}>
                演示账号（点击填入，密码不展示）
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
                {DEMO.map((d) => (
                  <Tag
                    key={d.username}
                    title={d.hint}
                    style={{ cursor: "pointer", marginInlineEnd: 0 }}
                    onClick={() => form.setFieldsValue({ username: d.username, password: "password123" })}
                  >
                    {d.label}
                  </Tag>
                ))}
              </div>
              <div style={{ marginTop: 10, fontSize: 12, color: "var(--ant-color-text-secondary, #8c8c8c)", lineHeight: 1.7 }}>
                {DEMO.map((d) => (
                  <div key={d.username}>
                    {d.label}：{d.hint}
                  </div>
                ))}
              </div>
            </>
          )}
          {mode === "register" && (
            <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0, fontSize: 12 }}>
              注册后会创建独立租户空间，上传的文档仅自己可见。角色为客户开发者。
            </Typography.Paragraph>
          )}
        </div>
      </div>
    </div>
  );
}
