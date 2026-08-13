import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiRequestError,
  findOrCreateRuntimeSession,
  getRuntimeSession,
  postRuntimeAction,
  runtimeStreamUrl,
  speedRequest,
  submitRuntimeEventDraft,
} from "./api";
import {
  isRuntimeStreamEvent,
  reduceRuntimeStreamEvent,
  RUNTIME_STREAM_EVENT_TYPES,
  shouldReplaceRuntimeSnapshot,
} from "./runtime";
import type {
  DemoPayload,
  EventDraftResponse,
  PlanningObjectiveProfile,
  RuntimeConnectionStatus,
  RuntimeSessionSnapshot,
  RuntimeStreamEventType,
  SimulationSpeed,
} from "./types";

const SSE_FAILURES_BEFORE_POLLING = 3;
const POLL_INTERVAL_MS = 2000;

interface UseRuntimeSessionOptions {
  payload: DemoPayload;
  onInitialUnavailable: () => void;
}

export interface RuntimeSessionController {
  snapshot: RuntimeSessionSnapshot | null;
  connectionStatus: RuntimeConnectionStatus;
  initializing: boolean;
  controlBusy: string | null;
  notice: string | null;
  lastEventType: RuntimeStreamEventType | null;
  start: () => Promise<void>;
  pause: () => Promise<void>;
  setSpeed: (speed: SimulationSpeed) => Promise<void>;
  reset: () => Promise<void>;
  replan: () => Promise<void>;
  acceptCandidate: () => Promise<void>;
  rejectCandidate: () => Promise<void>;
  submitReviewedEvent: (
    draft: EventDraftResponse,
    objectiveProfile: PlanningObjectiveProfile,
  ) => Promise<boolean>;
  refresh: () => Promise<void>;
}

function publicError(error: unknown): string {
  return error instanceof ApiRequestError
    ? error.message
    : "运行服务暂时无法完成请求，请刷新状态后重试。";
}

export function useRuntimeSession({
  payload,
  onInitialUnavailable,
}: UseRuntimeSessionOptions): RuntimeSessionController {
  const [snapshot, setSnapshot] = useState<RuntimeSessionSnapshot | null>(null);
  const [connectionStatus, setConnectionStatus] = useState<RuntimeConnectionStatus>("connecting");
  const [initializing, setInitializing] = useState(true);
  const [controlBusy, setControlBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [lastEventType, setLastEventType] = useState<RuntimeStreamEventType | null>(null);
  const snapshotRef = useRef<RuntimeSessionSnapshot | null>(null);
  const controlBusyRef = useRef<string | null>(null);

  const commitSnapshot = useCallback((next: RuntimeSessionSnapshot) => {
    if (!shouldReplaceRuntimeSnapshot(snapshotRef.current, next)) return;
    snapshotRef.current = next;
    setSnapshot(next);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let source: EventSource | null = null;
    let pollingTimer: number | null = null;
    let pollingInFlight = false;
    let sseFailures = 0;
    let pollFailures = 0;
    let sseLive = false;
    let cursor: { streamId: string; sequence: number } | null = null;

    const stopPolling = () => {
      if (pollingTimer !== null) {
        window.clearInterval(pollingTimer);
        pollingTimer = null;
      }
      pollFailures = 0;
    };

    const pollSnapshot = async (sessionId: string) => {
      if (pollingInFlight || !active) return;
      pollingInFlight = true;
      try {
        const next = await getRuntimeSession(sessionId, { signal: controller.signal });
        if (!active || sseLive) return;
        commitSnapshot(next);
        pollFailures = 0;
        setConnectionStatus("polling");
        setNotice("实时连接正在恢复，当前由权威快照每 2 秒同步。");
      } catch (error) {
        if (!active) return;
        pollFailures += 1;
        setNotice(publicError(error));
        if (pollFailures >= SSE_FAILURES_BEFORE_POLLING) {
          setConnectionStatus("offline_readonly");
        }
      } finally {
        pollingInFlight = false;
      }
    };

    const startPolling = (sessionId: string) => {
      if (pollingTimer !== null) return;
      setConnectionStatus("polling");
      void pollSnapshot(sessionId);
      pollingTimer = window.setInterval(() => void pollSnapshot(sessionId), POLL_INTERVAL_MS);
    };

    const handleEvent = (raw: MessageEvent<string>) => {
      if (!active) return;
      let parsed: unknown;
      try {
        parsed = JSON.parse(raw.data);
      } catch {
        setNotice("收到无法识别的运行消息，正在等待完整快照恢复。");
        return;
      }
      if (!isRuntimeStreamEvent(parsed)) return;
      const reduction = reduceRuntimeStreamEvent(snapshotRef.current, cursor, parsed);
      if (!reduction.accepted) return;
      cursor = reduction.cursor;
      if (reduction.snapshot !== null) commitSnapshot(reduction.snapshot);
      sseFailures = 0;
      sseLive = true;
      stopPolling();
      setConnectionStatus("live");
      setLastEventType(parsed.payload.event_type);
      setNotice(null);
    };

    const connect = (initial: RuntimeSessionSnapshot) => {
      source = new EventSource(runtimeStreamUrl(initial.session_id));
      RUNTIME_STREAM_EVENT_TYPES.forEach((eventType) => {
        source?.addEventListener(eventType, handleEvent as EventListener);
      });
      source.onopen = () => {
        if (!active) return;
        if (pollingTimer === null) {
          setConnectionStatus(cursor === null ? "connecting" : "recovering");
        }
      };
      source.onerror = () => {
        if (!active) return;
        sseLive = false;
        sseFailures += 1;
        if (sseFailures >= SSE_FAILURES_BEFORE_POLLING) {
          startPolling(initial.session_id);
        } else if (pollingTimer === null) {
          setConnectionStatus("recovering");
          setNotice("实时连接暂时中断，系统正在自动恢复。");
        }
      };
    };

    void findOrCreateRuntimeSession(payload, { signal: controller.signal })
      .then((initial) => {
        if (!active) return;
        commitSnapshot(initial);
        setInitializing(false);
        connect(initial);
      })
      .catch(() => {
        if (!active) return;
        setInitializing(false);
        setConnectionStatus("offline_readonly");
        onInitialUnavailable();
      });

    return () => {
      active = false;
      controller.abort();
      source?.close();
      stopPolling();
    };
  }, [commitSnapshot, onInitialUnavailable, payload]);

  const refresh = useCallback(async () => {
    const current = snapshotRef.current;
    if (current === null) return;
    try {
      commitSnapshot(await getRuntimeSession(current.session_id));
      setNotice(null);
    } catch (error) {
      setNotice(publicError(error));
    }
  }, [commitSnapshot]);

  const execute = useCallback(async (
    action: Parameters<typeof postRuntimeAction>[1],
    body: Record<string, unknown>,
  ) => {
    const current = snapshotRef.current;
    if (current === null || controlBusyRef.current !== null) return;
    controlBusyRef.current = action;
    setControlBusy(action);
    setNotice(null);
    try {
      commitSnapshot(await postRuntimeAction(current.session_id, action, body));
    } catch (error) {
      if (error instanceof ApiRequestError && error.status === 409) {
        try {
          commitSnapshot(await getRuntimeSession(current.session_id));
          setNotice("运行状态已在其他操作中更新，已载入最新快照，请重新确认后操作。");
        } catch (refreshError) {
          setNotice(publicError(refreshError));
        }
      } else {
        setNotice(publicError(error));
      }
    } finally {
      controlBusyRef.current = null;
      setControlBusy(null);
    }
  }, [commitSnapshot]);

  const start = useCallback(async () => {
    const current = snapshotRef.current;
    if (current) await execute("start", { expected_revision: current.revision });
  }, [execute]);
  const pause = useCallback(async () => {
    const current = snapshotRef.current;
    if (current) await execute("pause", { expected_revision: current.revision });
  }, [execute]);
  const setSpeed = useCallback(async (speed: SimulationSpeed) => {
    const current = snapshotRef.current;
    if (current) await execute("speed", speedRequest(current.revision, speed));
  }, [execute]);
  const reset = useCallback(async () => {
    const current = snapshotRef.current;
    if (current) await execute("reset", { expected_revision: current.revision, confirm_reset: true });
  }, [execute]);
  const replan = useCallback(async () => {
    const current = snapshotRef.current;
    if (current) await execute("replan", { expected_revision: current.revision });
  }, [execute]);
  const acceptCandidate = useCallback(async () => {
    const current = snapshotRef.current;
    if (current?.candidate_plan_id) {
      await execute("candidate/accept", {
        expected_revision: current.revision,
        candidate_plan_id: current.candidate_plan_id,
      });
    }
  }, [execute]);
  const rejectCandidate = useCallback(async () => {
    const current = snapshotRef.current;
    if (current?.candidate_plan_id) {
      await execute("candidate/reject", {
        expected_revision: current.revision,
        candidate_plan_id: current.candidate_plan_id,
      });
    }
  }, [execute]);

  const submitReviewedEvent = useCallback(async (
    draft: EventDraftResponse,
    objectiveProfile: PlanningObjectiveProfile,
  ): Promise<boolean> => {
    const current = snapshotRef.current;
    if (current === null || controlBusyRef.current !== null) return false;
    controlBusyRef.current = "event_submit";
    setControlBusy("event_submit");
    setNotice(null);
    try {
      const next = await submitRuntimeEventDraft(draft, objectiveProfile);
      commitSnapshot(next);
      return true;
    } catch (error) {
      if (error instanceof ApiRequestError && error.status === 409) {
        try {
          commitSnapshot(await getRuntimeSession(current.session_id));
          setNotice("运行状态已更新，事件草稿已过期；已载入最新快照，请重新生成并复核。");
        } catch (refreshError) {
          setNotice(publicError(refreshError));
        }
      } else {
        setNotice(publicError(error));
      }
      return false;
    } finally {
      controlBusyRef.current = null;
      setControlBusy(null);
    }
  }, [commitSnapshot]);

  return {
    snapshot,
    connectionStatus,
    initializing,
    controlBusy,
    notice,
    lastEventType,
    start,
    pause,
    setSpeed,
    reset,
    replan,
    acceptCandidate,
    rejectCandidate,
    submitReviewedEvent,
    refresh,
  };
}
