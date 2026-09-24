// Primary review UX: shows one candidate boundary's preview clip at a time
// and auto-advances to the next pending one after a hotkey decision.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client.js";
import BackButton from "../components/BackButton.jsx";
import { compareByRecordedDate, SortControl, useVideoSortPreference } from "../videoSort.jsx";

const SPEEDS = [1, 1.25, 1.5, 1.75, 2, 2.5, 3];

// Preview clips span [timestamp - PAD, timestamp + PAD] -- must match
// PREVIEW_PAD_SECONDS in backend/videos/services/clips.py.
const PREVIEW_PAD_SECONDS = 5;
// Approximate NTSC frame duration for the frame-step buttons. Not read from
// the actual source's frame rate (not exposed to the frontend today), so
// this is a nudge amount rather than a guaranteed single-frame step on
// every tape.
const FRAME_STEP_SECONDS = 1 / 30;

export default function ReviewQueue() {
  // ?video=<id> scopes the queue to one video (the "Review this video" link
  // from VideoList); no param reviews across the whole library.
  const [searchParams] = useSearchParams();
  const scopedVideoId = searchParams.get("video");

  const [boundary, setBoundary] = useState(undefined); // undefined = loading, null = empty queue
  const [speed, setSpeed] = useState(1);
  const [clipVersion, setClipVersion] = useState(0); // cache-bust the <video> src after an adjustment
  const videoRef = useRef(null);

  // Full, timestamp-ordered boundary list for the scoped video (any status,
  // not just pending) -- lets Prev/Next scene step through nearby cuts for
  // context ("is this actually a real break, or does it help to see what's
  // a couple scenes ahead?") without disturbing the actual decide/auto-
  // advance flow, which still always targets `boundary` (the real next-
  // pending item from the server). Only meaningful when scoped to one
  // video; peeking across unrelated videos in the library-wide queue
  // wouldn't mean anything.
  const [allBoundaries, setAllBoundaries] = useState([]);
  const [peekIndex, setPeekIndex] = useState(0);

  // Every source video that still has pending boundaries, with a count --
  // lets you jump straight to a specific video instead of only ever
  // working the single global next-up boundary. Sort preference is shared
  // (localStorage) with the same control on the main video list and the
  // Verify queue.
  const [summary, setSummary] = useState([]);
  const { mode: sortMode, direction: sortDirection, setMode: setSortMode, setDirection: setSortDirection } =
    useVideoSortPreference();
  const refreshSummary = () => api.reviewSummary().then(setSummary);

  // Local editable copies of the boundary's before/after log fields --
  // separate from `boundary` itself so typing doesn't fight the polling/
  // reload cycle; synced from the boundary whenever a new one loads.
  const [beforeDescription, setBeforeDescription] = useState("");
  const [beforeDate, setBeforeDate] = useState("");
  const [afterDescription, setAfterDescription] = useState("");
  const [afterDate, setAfterDate] = useState("");

  const syncFieldsFrom = (b) => {
    setBeforeDescription(b?.before_description || "");
    setBeforeDate(b?.before_date || "");
    setAfterDescription(b?.after_description || "");
    // Default After to match Before the moment a boundary is first shown
    // (e.g. Before was just carried forward from the prior boundary's
    // After) -- still freely editable, and never overrides an After date
    // that's actually been set already.
    setAfterDate(b?.after_date || b?.before_date || "");
    setValidationError("");
  };

  // `carry` is the previous boundary's after_description/after_date --
  // chronologically the same stretch of footage as the next boundary's
  // "before", so it's written into the new boundary immediately rather
  // than left for the reviewer to retype.
  const loadNext = useCallback(
    async (carry) => {
      setBoundary(undefined);
      setClipVersion(0);
      let next = await api.nextQueueBoundary(scopedVideoId);
      if (next && carry && (carry.description || carry.date) && !next.before_description && !next.before_date) {
        next = await api.updateBoundary(next.id, {
          before_description: carry.description,
          before_date: carry.date || null,
        });
      }
      setBoundary(next);
      syncFieldsFrom(next);

      if (scopedVideoId) {
        const list = await api.listBoundaries({ video: scopedVideoId });
        setAllBoundaries(list);
        const idx = next ? list.findIndex((b) => b.id === next.id) : -1;
        setPeekIndex(idx >= 0 ? idx : 0);
      } else {
        setAllBoundaries([]);
      }
    },
    [scopedVideoId]
  );

  useEffect(() => {
    loadNext();
    refreshSummary();
  }, [loadNext]);

  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = speed;
  }, [speed, boundary]);

  const [validationError, setValidationError] = useState("");

  const decide = useCallback(
    async (verdict) => {
      if (!boundary) return;

      // A date is required to advance at all -- it ends up baked into the
      // encoded clip's metadata, so letting a scene go undated here just
      // means re-encoding later. Approve closes two genuinely different
      // scenes, so both need a date; reject means no real cut (before/after
      // describe the same continuous scene), so only "before" matters.
      const missingDate = verdict === "approved" ? !beforeDate || !afterDate : !beforeDate;
      if (missingDate) {
        setValidationError(
          verdict === "approved"
            ? "Enter both a Before and After date before approving."
            : "Enter a Before date before rejecting."
        );
        return;
      }
      setValidationError("");

      // Flush whatever's currently typed (a field may not have been
      // blurred yet) before moving on.
      await api.updateBoundary(boundary.id, {
        before_description: beforeDescription,
        before_date: beforeDate || null,
        after_description: afterDescription,
        after_date: afterDate || null,
      });
      await api.reviewBoundary(boundary.id, verdict);

      // On approve, before/after are genuinely two different scenes, so
      // only "after" (the new one) carries forward. On reject, there was
      // no real cut -- before/after describe the same continuous scene --
      // so if the reviewer didn't bother re-typing "after" (nothing
      // changed), fall back to "before" rather than carrying forward a
      // blank and losing the description. Prefer "after" when it *was*
      // filled in, in case they added something more specific there.
      const carry =
        verdict === "rejected"
          ? { description: afterDescription || beforeDescription, date: afterDate || beforeDate }
          : { description: afterDescription, date: afterDate };
      loadNext(carry);
      refreshSummary();
    },
    [boundary, beforeDescription, beforeDate, afterDescription, afterDate, loadNext]
  );

  const currentIndex = boundary ? allBoundaries.findIndex((b) => b.id === boundary.id) : -1;
  const isPeeking = allBoundaries.length > 0 && currentIndex >= 0 && peekIndex !== currentIndex;
  const peekBoundary = isPeeking ? allBoundaries[peekIndex] : boundary;

  const peekPrev = () => setPeekIndex((i) => Math.max(0, i - 1));
  const peekNext = () => setPeekIndex((i) => Math.min(allBoundaries.length - 1, i + 1));
  const returnToCurrent = () => setPeekIndex(currentIndex);

  const replay = useCallback(() => {
    if (videoRef.current) {
      videoRef.current.currentTime = 0;
      videoRef.current.play();
    }
  }, []);

  // Frame-step the paused clip to line up the cut exactly, then either
  // "Set boundary here" (re-centers the clip on the new position without
  // deciding yet) or approve/reject as normal once it looks right.
  const nudgeFrame = useCallback((direction) => {
    const v = videoRef.current;
    if (!v) return;
    v.pause();
    const maxTime = PREVIEW_PAD_SECONDS * 2;
    v.currentTime = Math.min(maxTime, Math.max(0, v.currentTime + direction * FRAME_STEP_SECONDS));
  }, []);

  const setBoundaryHere = useCallback(async () => {
    const v = videoRef.current;
    if (!v || !boundary) return;
    const absoluteTimestamp = boundary.timestamp_seconds - PREVIEW_PAD_SECONDS + v.currentTime;
    const updated = await api.adjustBoundary(boundary.id, absoluteTimestamp);
    setBoundary(updated);
    setClipVersion((n) => n + 1);
  }, [boundary]);

  // Hotkeys: Y/right-arrow approve, N/left-arrow reject, R/space replay.
  // Disabled while peeking ahead/behind -- Y/N should never fire against
  // whatever clip happens to be on screen while browsing for context; jump
  // back to the actual current boundary first ("Back to current").
  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
      if (isPeeking) return;
      switch (e.key.toLowerCase()) {
        case "y":
        case "arrowright":
          decide("approved");
          break;
        case "n":
        case "arrowleft":
          decide("rejected");
          break;
        case "r":
        case " ":
          e.preventDefault();
          replay();
          break;
        default:
          break;
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [decide, replay, isPeeking]);

  if (boundary === undefined) {
    return (
      <div>
        <BackButton />
        <p>Loading next boundary...</p>
      </div>
    );
  }

  const sortedSummary =
    sortMode === "date"
      ? [...summary].sort((a, b) => compareByRecordedDate({ ...a, path: a.video_path }, { ...b, path: b.video_path }, sortDirection))
      : summary;

  const videoList = summary.length > 0 && (
    <div className="boundary-log">
      <h2>Videos with boundaries to review</h2>
      <SortControl
        mode={sortMode}
        direction={sortDirection}
        setMode={setSortMode}
        setDirection={setSortDirection}
        showStatusMode={false}
      />
      <ul>
        {sortedSummary.map((row) => (
          <li key={row.video_id}>
            <Link to={`/review?video=${row.video_id}`}>
              {row.video_path} ({row.count})
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );

  if (boundary === null) {
    return (
      <div>
        <BackButton />
        <h1>Nothing to review</h1>
        <p>
          {scopedVideoId
            ? "This video hasn't been processed yet, or every candidate boundary has already been reviewed."
            : "Nothing's been detected yet, or everything detected so far has already been reviewed."}{" "}
          Run detection on a video from the <Link to="/">Videos page</Link> to get candidates here.
        </p>
        {videoList}
      </div>
    );
  }

  return (
    <div className="review-queue">
      <BackButton />
      <h1>{scopedVideoId ? "Review Queue (this video)" : "Review Queue"}</h1>
      <p className="boundary-meta">
        {peekBoundary.video_path} @ {formatTime(peekBoundary.timestamp_seconds)}
        {allBoundaries.length > 0 && `, Scene ${peekIndex + 1}/${allBoundaries.length}`}
        {isPeeking && ` -- previewing (${peekBoundary.review_status}), not the current boundary`}
        {" -- "}
        <Link to={`/videos/${peekBoundary.video}`}>Go to video</Link>
      </p>
      {allBoundaries.length > 0 && (
        <div className="peek-controls">
          <button onClick={peekPrev} disabled={peekIndex <= 0}>
            &laquo; Prev scene
          </button>
          <button onClick={peekNext} disabled={peekIndex >= allBoundaries.length - 1}>
            Next scene &raquo;
          </button>
          {isPeeking && <button onClick={returnToCurrent}>Back to current</button>}
        </div>
      )}
      <video
        ref={videoRef}
        key={`${peekBoundary.id}-${isPeeking ? "peek" : clipVersion}`}
        src={`/api/boundaries/${peekBoundary.id}/clip/${isPeeking ? "" : `?v=${clipVersion}`}`}
        autoPlay
        controls
        // Loading a new src resets playbackRate to 1 in most browsers, and
        // a fresh clip after auto-advance/peek-navigate is exactly that --
        // re-applying once metadata is actually loaded keeps the selected
        // speed sticking on the next clip.
        onLoadedMetadata={() => {
          if (videoRef.current) videoRef.current.playbackRate = speed;
        }}
        style={{ maxWidth: "720px", width: "100%", display: "block", margin: "0 auto" }}
      />
      <div className="speed-controls">
        {SPEEDS.map((s) => (
          <button
            key={s}
            className={s === speed ? "active" : ""}
            onClick={() => setSpeed(s)}
          >
            {s}x
          </button>
        ))}
      </div>
      <div className="scrub-controls">
        <button onClick={() => nudgeFrame(-1)} disabled={isPeeking}>&laquo; frame</button>
        <button onClick={() => nudgeFrame(1)} disabled={isPeeking}>frame &raquo;</button>
        <button onClick={setBoundaryHere} disabled={isPeeking}>Set boundary here</button>
      </div>

      {isPeeking && (
        <p className="boundary-log-hint">
          Previewing a nearby boundary for context -- the log below and approve/reject still apply
          to the current boundary ({formatTime(boundary.timestamp_seconds)}), not this one.
        </p>
      )}

      <div className="boundary-log">
        <div className="boundary-log-row">
          <span className="boundary-log-label">Before</span>
          <input
            type="text"
            placeholder="What's happening before this cut..."
            value={beforeDescription}
            onChange={(e) => setBeforeDescription(e.target.value)}
            onBlur={() => api.updateBoundary(boundary.id, { before_description: beforeDescription })}
          />
          <input
            type="date"
            value={beforeDate}
            // Only setBeforeDate here, not a live copy into After -- a
            // native date input fires onChange on every keystroke,
            // including a momentarily "valid" but wrong intermediate value
            // (e.g. right after typing just the first digit of the year),
            // and copying that into After immediately picked up the wrong
            // date. Blur (below) is when the value is actually finalized.
            onChange={(e) => setBeforeDate(e.target.value)}
            onBlur={() => {
              // Default After to match Before once Before is finalized --
              // same day unless the reviewer knows otherwise, still freely
              // editable, and never overwrites an After date already set.
              const finalAfterDate = afterDate || beforeDate;
              if (finalAfterDate !== afterDate) setAfterDate(finalAfterDate);
              api.updateBoundary(boundary.id, {
                before_date: beforeDate || null,
                after_date: finalAfterDate || null,
              });
            }}
          />
        </div>
        <div className="boundary-log-row">
          <span className="boundary-log-label">After</span>
          <input
            type="text"
            placeholder="What's happening after this cut..."
            value={afterDescription}
            onChange={(e) => setAfterDescription(e.target.value)}
            onBlur={() => api.updateBoundary(boundary.id, { after_description: afterDescription })}
          />
          <input
            type="date"
            value={afterDate}
            onChange={(e) => setAfterDate(e.target.value)}
            onBlur={() => api.updateBoundary(boundary.id, { after_date: afterDate || null })}
          />
        </div>
        <p className="boundary-log-hint">
          "After" carries forward as "Before" on the next boundary automatically.
        </p>
        {validationError && <p className="validation-error">{validationError}</p>}
      </div>

      <div className="review-actions">
        <button className="reject" onClick={() => decide("rejected")} disabled={isPeeking}>
          Not a scene change (N)
        </button>
        <button onClick={replay}>Replay (R)</button>
        <button className="approve" onClick={() => decide("approved")} disabled={isPeeking}>
          Real scene change (Y)
        </button>
      </div>

      {videoList}
    </div>
  );
}

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
