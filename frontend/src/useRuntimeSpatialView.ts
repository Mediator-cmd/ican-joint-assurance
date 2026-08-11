import { useCallback, useEffect, useState } from "react";

import { ApiRequestError, getRuntimeSpatialView } from "./api";
import type {
  RuntimeSessionSnapshot,
  RuntimeSpatialStatus,
  RuntimeSpatialView,
} from "./types";


export interface RuntimeSpatialController {
  view: RuntimeSpatialView | null;
  status: RuntimeSpatialStatus;
  error: string | null;
  refresh: () => void;
}

export function useRuntimeSpatialView(
  snapshot: RuntimeSessionSnapshot | null,
): RuntimeSpatialController {
  const [view, setView] = useState<RuntimeSpatialView | null>(null);
  const [status, setStatus] = useState<RuntimeSpatialStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const refresh = useCallback(() => setRefreshToken((value) => value + 1), []);

  const sessionId = snapshot?.session_id ?? null;
  const revision = snapshot?.revision ?? null;
  const simulationTime = snapshot?.clock.simulation_time ?? null;

  useEffect(() => {
    if (sessionId === null || revision === null) {
      setView(null);
      setStatus("loading");
      setError(null);
      return;
    }

    const controller = new AbortController();
    let active = true;
    setStatus((current) => (
      view?.overlay.session_id === sessionId && view.overlay.revision === revision
        ? current
        : "loading"
    ));
    setError(null);

    void getRuntimeSpatialView(sessionId, revision, { signal: controller.signal })
      .then((next) => {
        if (!active) return;
        if (next.overlay.session_id !== sessionId || next.overlay.revision !== revision) {
          setStatus("error");
          setError("空间态势与当前运行修订不一致，已停止绘制。");
          return;
        }
        setView(next);
        setStatus("ready");
      })
      .catch((caught: unknown) => {
        if (!active) return;
        if (caught instanceof ApiRequestError && caught.code === "spatial_layout_not_available") {
          setView(null);
          setStatus("unavailable");
          setError("当前场景尚未登记可公开的匿名机场空间布局。");
          return;
        }
        if (caught instanceof ApiRequestError && caught.status === 409) {
          setView(null);
          setStatus("loading");
          setError("运行状态已进入新修订，正在等待最新权威快照。");
          return;
        }
        setStatus("error");
        setError(caught instanceof ApiRequestError
          ? caught.message
          : "机场空间态势暂时无法读取，请稍后重试。");
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [refreshToken, revision, sessionId, simulationTime]);

  const currentView = view?.overlay.session_id === sessionId && view.overlay.revision === revision
    ? view
    : null;
  return { view: currentView, status, error, refresh };
}
