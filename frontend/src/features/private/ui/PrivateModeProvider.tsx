/**
 * The Private Mode surface, loaded on demand.
 *
 * `ChatView` imports this module with a dynamic `import()` and nothing else: the
 * rules engine, the compiled-bundle loader, the capability probe, the download
 * manager, the crypto and the panel are ~30 KB gzipped, and the Day-25 bundle
 * budget gives the whole /chat route 25 KB. Everybody who opens chat therefore
 * pays for Private Mode only if they ask for it.
 *
 * It owns both private hooks and hands the chat surface back three things: which
 * controller should answer (`chat`), the controller itself (`privateMode`, for
 * the badge and the voice notice), and the panel as a node to place in the page's
 * reading order — the panel is rendered *by the caller* so it still sits between
 * the page heading and the conversation controls.
 *
 * The standard-mode controller is passed in and kept running by the caller, so
 * switching Private Mode off returns to the conversation that was already on
 * screen instead of starting a new one.
 */

import { useEffect, type ReactElement } from "react";

import type { ChatLocale } from "@/features/chat/types";
import type { ChatController } from "@/features/chat/useChat";

import { usePrivateChat } from "../session/usePrivateChat";
import type { PrivateChatController } from "../session/usePrivateChat";
import { usePrivateMode } from "../session/usePrivateMode";
import type { PrivateModeController } from "../session/usePrivateMode";
import { PrivateModePanel } from "./PrivateModePanel";
import { writePrivateEnabled } from "./preference";

export interface PrivateModeSurface {
  /** The controller that should answer: private when Private Mode is on. */
  chat: ChatController;
  mode: PrivateModeController;
  privateChat: PrivateChatController;
  /** The panel, as a node, so the caller decides where it sits in the page. */
  panel: ReactElement;
}

export interface PrivateModeProviderProps {
  /** Keeps running while Private Mode is on, so switching off is not a reset. */
  serverChat: ChatController;
  /** The app's interface language; drives crisis copy and the template writer. */
  locale: ChatLocale;
  /** True when the reader left with Private Mode on, so boot immediately. */
  autoBoot: boolean;
  children: (surface: PrivateModeSurface) => ReactElement;
}

export function PrivateModeProvider({
  serverChat,
  locale,
  autoBoot,
  children,
}: PrivateModeProviderProps): ReactElement {
  const mode = usePrivateMode({ autoBoot });
  const privateChat = usePrivateChat({
    session: mode.enabled ? mode.session : null,
    locale,
    onStoreTranscriptChange: (value) => {
      void mode.setStoreTranscript(value);
    },
  });

  const { enabled, setLocale } = mode;

  // The app's language picker drives the private session too: helplines, crisis
  // copy and the template writer all read the locale from private settings.
  useEffect(() => {
    if (!enabled) {
      return;
    }
    void setLocale(locale);
  }, [enabled, locale, setLocale]);

  // A pointer for the next page load, so a private reader comes back private.
  // The truth stays in IndexedDB settings; this only decides whether to boot.
  useEffect(() => {
    writePrivateEnabled(enabled);
  }, [enabled]);

  return children({
    chat: enabled ? privateChat : serverChat,
    mode,
    privateChat,
    panel: <PrivateModePanel controller={mode} />,
  });
}

export default PrivateModeProvider;
