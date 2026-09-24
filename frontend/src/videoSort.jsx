// Shared video-sort preference: used by the main video list, the Review
// Queue's "videos to review" list, and the Verify queue's "videos to
// verify" list -- persisted in localStorage so it's the same choice
// wherever you are, and survives navigating between pages/reloading.
import { useEffect, useState } from "react";

const STORAGE_KEY = "sceneclip.videoSort";
const DEFAULT_PREF = { mode: "status", direction: "asc" };

function loadPref() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_PREF;
    const parsed = JSON.parse(raw);
    return {
      mode: parsed.mode === "date" ? "date" : "status",
      direction: parsed.direction === "desc" ? "desc" : "asc",
    };
  } catch {
    return DEFAULT_PREF;
  }
}

function savePref(pref) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(pref));
  } catch {
    // private window/blocked storage -- the preference just won't persist
  }
}

export function useVideoSortPreference() {
  const [pref, setPref] = useState(loadPref);

  useEffect(() => {
    savePref(pref);
  }, [pref]);

  const setMode = (mode) => setPref((p) => ({ ...p, mode }));
  const setDirection = (direction) => setPref((p) => ({ ...p, direction }));

  return { mode: pref.mode, direction: pref.direction, setMode, setDirection };
}

// Compares by recorded_date (ISO "YYYY-MM-DD" strings sort correctly as
// plain strings); undated items always sort last regardless of direction
// -- there's no meaningful position for "no date" in either direction, and
// the "No date" filter is the actual tool for finding them.
export function compareByRecordedDate(a, b, direction) {
  if (!a.recorded_date && !b.recorded_date) return a.path.localeCompare(b.path);
  if (!a.recorded_date) return 1;
  if (!b.recorded_date) return -1;
  const cmp = a.recorded_date.localeCompare(b.recorded_date) || a.path.localeCompare(b.path);
  return direction === "desc" ? -cmp : cmp;
}

// The little "Sort by: Status | Recorded date  [asc/desc]" control,
// reused wherever a video list can be sorted. `showStatusMode` hides the
// Status option on pages that only ever show one implicit status (e.g. a
// summary list of videos with pending work) -- date is still the same
// shared preference there, just without a meaningless "status" toggle.
export function SortControl({ mode, direction, setMode, setDirection, showStatusMode = true }) {
  return (
    <div className="filter-toolbar">
      <span className="boundary-log-label">Sort by</span>
      {showStatusMode && (
        <button className={mode === "status" ? "active" : ""} onClick={() => setMode("status")}>
          Status
        </button>
      )}
      <button className={mode === "date" ? "active" : ""} onClick={() => setMode("date")}>
        Recorded date
      </button>
      {mode === "date" && (
        <button onClick={() => setDirection(direction === "asc" ? "desc" : "asc")}>
          {direction === "asc" ? "Ascending ↑" : "Descending ↓"}
        </button>
      )}
    </div>
  );
}
