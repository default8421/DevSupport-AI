/**
 * @author: liuqinhe
 */
import { describe, expect, it } from "vitest";

import { conversationGroup, formatTime } from "./formatTime";

describe("formatTime", () => {
  it("把 T 换成空格并截到秒", () => {
    expect(formatTime("2026-09-04T16:41:28.123456")).toBe("2026-09-04 16:41:28");
  });

  it("空值返回空串", () => {
    expect(formatTime(null)).toBe("");
    expect(formatTime(undefined)).toBe("");
  });
});

describe("conversationGroup", () => {
  const now = new Date("2026-09-04T18:00:00");

  it("今天", () => {
    expect(conversationGroup("2026-09-04T09:00:00", now)).toBe("今天");
  });

  it("昨天", () => {
    expect(conversationGroup("2026-09-03T23:50:00", now)).toBe("昨天");
  });

  it("更早", () => {
    expect(conversationGroup("2026-08-01T12:00:00", now)).toBe("更早");
  });

  it("缺时间归到更早", () => {
    expect(conversationGroup(null, now)).toBe("更早");
    expect(conversationGroup("not-a-date", now)).toBe("更早");
  });
});
