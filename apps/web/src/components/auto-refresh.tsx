"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

// Re-renders the page's server data every few seconds (new chats and bookings show
// up without a reload). Paused while the tab is hidden or someone is typing.
export function AutoRefresh({ seconds = 5 }: { seconds?: number }) {
  const router = useRouter();
  useEffect(() => {
    const id = setInterval(() => {
      const typing = document.activeElement?.matches("input, textarea, select");
      if (document.visibilityState === "visible" && !typing) router.refresh();
    }, seconds * 1000);
    return () => clearInterval(id);
  }, [router, seconds]);
  return null;
}
