import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.jsx";
import "./styles.css";

// Django only sets the csrftoken cookie as a side effect of a request that
// calls get_token() -- never true for a plain fetch()-based SPA otherwise.
// Hit the bootstrap endpoint once, before anything else can fire a POST,
// so the cookie is guaranteed to exist by the time any component mounts.
fetch("/api/csrf/", { credentials: "same-origin" }).finally(() => {
  ReactDOM.createRoot(document.getElementById("root")).render(
    <React.StrictMode>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </React.StrictMode>
  );
});
