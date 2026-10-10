import type { ReactElement } from "react";

import { ChatView } from "@/features/chat/ChatView";

/**
 * Chat.
 *
 * The page is a shell; everything lives in `features/chat` so the surface can be
 * tested without the router. Wired to the Day 11 SSE stream (`POST
 * /chat/sessions/{id}/stream`), which is why this page was inert until the
 * deterministic crisis gate shipped ahead of it (AGENTS.md rule 1).
 */
export function ChatPage(): ReactElement {
  return <ChatView />;
}
