/**
 * @author: liuqinhe
 */
import { beforeEach, describe, expect, it } from "vitest";

import { clearAuth, readStoredUser, writeAuth } from "./storage";

const mem: Record<string, string> = {};

beforeEach(() => {
  for (const k of Object.keys(mem)) delete mem[k];
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (k: string) => (k in mem ? mem[k] : null),
      setItem: (k: string, v: string) => {
        mem[k] = v;
      },
      removeItem: (k: string) => {
        delete mem[k];
      },
      clear: () => {
        for (const k of Object.keys(mem)) delete mem[k];
      },
    },
  });
});

describe("readStoredUser", () => {
  it("缺记录返回 null", () => {
    expect(readStoredUser()).toBeNull();
  });

  it("坏 JSON 不抛异常", () => {
    localStorage.setItem("user", "{not json");
    expect(readStoredUser()).toBeNull();
  });

  it("缺 user_id 视为无效", () => {
    localStorage.setItem("user", JSON.stringify({ username: "x" }));
    expect(readStoredUser()).toBeNull();
  });

  it("读写往返", () => {
    const user = {
      user_id: "u1",
      username: "dev",
      display_name: "Dev",
      role: "customer_dev",
      tenant_id: "t1",
    };
    writeAuth("tok", user);
    expect(readStoredUser()).toEqual(user);
    clearAuth();
    expect(readStoredUser()).toBeNull();
  });
});
