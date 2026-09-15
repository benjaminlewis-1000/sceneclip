// Danger-zone page: currently just "clear the database." Gated behind
// typing the exact confirm phrase (not just a click) since this is
// destructive and irreversible -- it's meant to be reachable without
// asking Claude to do it by hand, not to be a single accidental click away.
import React, { useState } from "react";
import { api } from "../api/client.js";

const CONFIRM_PHRASE = "CLEAR";

export default function Settings() {
  const [confirmText, setConfirmText] = useState("");
  const [status, setStatus] = useState(null); // null | "clearing" | {deleted}

  const clearDatabase = async () => {
    setStatus("clearing");
    const result = await api.clearDatabase();
    setStatus(result);
    setConfirmText("");
  };

  return (
    <div>
      <h1>Settings</h1>

      <section className="danger-zone">
        <h2>Danger zone</h2>
        <p>
          Clears every video, detection run, boundary, scene, and notification from the database.
          Source files on disk are untouched -- this just resets the library back to "nothing
          scanned yet," useful for re-testing the directory scan from a clean slate. This cannot
          be undone.
        </p>
        <label className="danger-confirm-label">
          Type <code>{CONFIRM_PHRASE}</code> to enable the button
          <input
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder={CONFIRM_PHRASE}
          />
        </label>
        <button
          className="danger-button"
          disabled={confirmText !== CONFIRM_PHRASE || status === "clearing"}
          onClick={clearDatabase}
        >
          {status === "clearing" ? "Clearing..." : "Clear database"}
        </button>
        {status && status !== "clearing" && (
          <p className="danger-result">Deleted {status.deleted} video(s) and everything derived from them.</p>
        )}
      </section>
    </div>
  );
}
