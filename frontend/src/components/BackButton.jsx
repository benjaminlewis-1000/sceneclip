// Small "< Back" link used on pages reached by drilling into something
// (a specific video, a scoped review queue) rather than top-level nav
// tabs, so there's always an obvious way out without hitting the browser's
// own back button.
import React from "react";
import { useNavigate } from "react-router-dom";

export default function BackButton({ fallback = "/" }) {
  const navigate = useNavigate();

  const goBack = () => {
    if (window.history.state && window.history.state.idx > 0) {
      navigate(-1);
    } else {
      navigate(fallback);
    }
  };

  return (
    <button className="back-button" onClick={goBack}>
      &larr; Back
    </button>
  );
}
