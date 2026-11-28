import { onlineManager } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useOnlineStatus } from "./useOnlineStatus";

afterEach(() => {
  onlineManager.setOnline(true);
});

describe("useOnlineStatus", () => {
  it("segue o onlineManager", () => {
    onlineManager.setOnline(false);
    const { result } = renderHook(() => useOnlineStatus());
    expect(result.current).toBe(false);

    act(() => {
      onlineManager.setOnline(true);
    });
    expect(result.current).toBe(true);
  });
});
