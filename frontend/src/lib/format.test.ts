import { describe, expect, it } from "vitest";
import { formatDateTime, formatDuration, severityTone } from "./format";

describe("format", () => {
  it("renders missing dates as a dash", () => {
    expect(formatDateTime(null)).toBe("-");
    expect(formatDateTime("")).toBe("-");
  });

  it("formats valid ISO timestamps", () => {
    const text = formatDateTime("2026-08-24T10:30:00Z");
    expect(text).not.toBe("-");
    expect(text.length).toBeGreaterThan(0);
  });

  it("formats durations", () => {
    expect(
      formatDuration("2026-08-24T10:00:00Z", "2026-08-24T10:00:30.500Z"),
    ).toContain("с");
    expect(formatDuration(null, null)).toBe("-");
  });

  it("maps severities to tones", () => {
    expect(severityTone("HIGH")).toBe("critical");
    expect(severityTone("critical")).toBe("critical");
    expect(severityTone("MEDIUM")).toBe("medium");
    expect(severityTone("LOW")).toBe("low");
    expect(severityTone(undefined)).toBe("none");
  });
});
